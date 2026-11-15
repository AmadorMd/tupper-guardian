"""Inventory Lambda: every read and write of fridge state.

DynamoDB is the source of truth. A fridge is a partition shared by the people
invited to it; each person's profile points to the fridge they belong to.
Every item is closed with a conditional update on `status = ACTIVE`, so a
button press, a text message, and the workflow timeout can race safely:
exactly one of them wins.

Questions are asked one at a time per person. The turn is a slot on that
person's profile, taken with a conditional write and released when the
question is answered or times out. It also expires by itself, so a failed
execution can never block the questions that follow.

Dispatches on `event["action"]`; every response includes `task_token`,
`action`, `wake_token` and `review_id` so the workflow can treat all replies
uniformly.
"""

import math
import os
import secrets
import time
import unicodedata
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

PROFILE_KEY = "PROFILE"
FRIDGE_KEY = "FRIDGE"
INVITE_PARTITION = "INVITE"
TUPPER_PREFIX = "T#"
DEFAULT_DAYS = 2
INVITE_TTL_SECONDS = 7 * 24 * 3600
MAX_TUPPER_ID_LENGTH = 32
SLOT_MARGIN_SECONDS = 60
MAX_TURN_POLL_SECONDS = 900
MIN_TURN_POLL_SECONDS = 5

BUTTON_ACTIONS = {"e": "eaten", "t": "tossed", "s": "snooze", "r": "review"}
CLOSING_STATUS = {"eaten": "CONSUMED", "tossed": "DISCARDED"}

_resource = None


def _table(env_name):
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource.Table(os.environ[env_name])


def _tuppers():
    return _table("TABLE_NAME")


def _now():
    return time.time()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_per_day():
    return int(os.environ.get("SECONDS_PER_DAY", "86400"))


def _normalize(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join("".join(c if c.isalnum() else " " for c in plain.lower()).split())


def _shelf_days(category):
    """Return (days, default_used) for a category from the shelf-life table."""
    item = _table("SHELF_TABLE_NAME").get_item(Key={"category": category}).get("Item")
    if item and int(item.get("days", 0)) > 0:
        return int(item["days"]), False
    return DEFAULT_DAYS, True


def _profile(chat_key):
    return _tuppers().get_item(Key={"chat_id": chat_key, "item_id": PROFILE_KEY}).get("Item") or {}


def _remember_name(chat_key, first_name):
    if first_name:
        _tuppers().update_item(
            Key={"chat_id": chat_key, "item_id": PROFILE_KEY},
            UpdateExpression="SET first_name = :name",
            ExpressionAttributeValues={":name": first_name},
        )


def _conditional(update):
    """Run a conditional write; return its response, or None if the condition failed."""
    try:
        return update()
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def _fridge_key(chat_key):
    """The fridge a person belongs to: the one they joined, or their own."""
    return _profile(chat_key).get("fridge_id") or chat_key


def _members(fridge_key):
    record = _tuppers().get_item(Key={"chat_id": fridge_key, "item_id": FRIDGE_KEY}).get("Item")
    return sorted((record or {}).get("members") or {fridge_key})


def _asker(item, fridge_key):
    """Who gets the questions about an item: whoever stored it."""
    return item.get("stored_by") or fridge_key


def _is_overdue(item):
    return _now() >= int(item["expires_epoch"]) + _seconds_per_day()


def _active_tuppers(fridge_key):
    items, start_key = [], None
    while True:
        query = {
            "KeyConditionExpression": Key("chat_id").eq(fridge_key)
            & Key("item_id").begins_with(TUPPER_PREFIX),
            "FilterExpression": Attr("status").eq("ACTIVE"),
            "ConsistentRead": True,
        }
        if start_key:
            query["ExclusiveStartKey"] = start_key
        response = _tuppers().query(**query)
        items.extend(response["Items"])
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def _close(fridge_key, item_id, new_status):
    """Move an ACTIVE item to `new_status` (or just clear its token when
    `new_status` is None). Returns the previous item, or None if it was not
    ACTIVE anymore."""
    update = "REMOVE task_token"
    values = {":active": "ACTIVE"}
    if new_status is not None:
        update = "SET #s = :new REMOVE task_token, turn_token"
        values[":new"] = new_status
    response = _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": fridge_key, "item_id": item_id},
            UpdateExpression=update,
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_OLD",
        )
    )
    return response.get("Attributes") if response else None


def _release_turn(asker_key, item_id):
    _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": asker_key, "item_id": PROFILE_KEY},
            UpdateExpression="REMOVE asking, asking_until",
            ConditionExpression="asking = :item",
            ExpressionAttributeValues={":item": item_id},
        )
    )


def _soonest(items):
    return min(items, key=lambda item: int(item["expires_epoch"])) if items else None


def _review_question(fridge_key, pending):
    """The next flagged item to ask about, shaped like the data of an `ask` message."""
    item = _soonest(pending)
    if not item:
        return {}
    return {
        "fridge_key": fridge_key,
        "item_id": item["item_id"],
        "tupper_id": item["item_id"][len(TUPPER_PREFIX):],
        "food": item["food"],
        "review": True,
        "waiting": len(pending) - 1,
    }


def _what_follows(fridge_key, answered):
    """After `answered` leaves its question: free the turn, then name what to
    ask next. A waiting execution is woken first; with none waiting, the next
    item flagged for review is offered."""
    asker = _asker(answered, fridge_key)
    _release_turn(asker, answered["item_id"])
    others = [i for i in _active_tuppers(fridge_key) if i["item_id"] != answered["item_id"]]
    waiting = _soonest(
        [i for i in others if i.get("turn_token") and _asker(i, fridge_key) == asker]
    )
    if waiting:
        return {"wake_token": waiting["turn_token"]}
    review = _review_question(fridge_key, [i for i in others if i.get("needs_review")])
    return {"review_id": review.get("item_id", ""), "review": review}


def _pick(food, items):
    """Choose which active tupper a free-text food name refers to."""
    if not items:
        return None
    newest_first = sorted(items, key=lambda item: item["stored_at"], reverse=True)
    target = _normalize(food)
    if not target:
        awaiting = [item for item in newest_first if item.get("task_token")]
        if len(awaiting) == 1:
            return awaiting[0]
        return newest_first[0] if len(newest_first) == 1 else None
    named = [(item, _normalize(item["food"])) for item in newest_first]
    for matches in (
        lambda name: name == target,
        lambda name: target in name or name in target,
        lambda name: any(len(word) > 2 and word in name.split() for word in target.split()),
    ):
        for item, name in named:
            if matches(name):
                return item
    return None


def store(event):
    chat_key = str(event["chat_id"])
    fridge_key = _fridge_key(chat_key)
    food = event["food"]
    days, default_used = _shelf_days(event.get("category") or "other")
    per_day = _seconds_per_day()
    now = _now()
    expires = now + days * per_day
    tupper_id = secrets.token_hex(6)
    item_id = TUPPER_PREFIX + tupper_id
    _tuppers().put_item(
        Item={
            "chat_id": fridge_key,
            "item_id": item_id,
            "food": food,
            "category": event.get("category") or "other",
            "status": "ACTIVE",
            "stored_by": chat_key,
            "stored_by_name": event.get("first_name") or "",
            "stored_at": _iso(now),
            "expires_at": _iso(expires),
            "expires_epoch": int(expires),
        }
    )
    _remember_name(chat_key, event.get("first_name"))
    return {
        "fridge_key": fridge_key,
        "tupper_id": tupper_id,
        "item_id": item_id,
        "food": food,
        "days": days,
        "default_used": default_used,
        # No early reminder for one-day items: it would fire immediately.
        "remind_at": _iso(expires - per_day) if days > 1 else "",
        "expires_at": _iso(expires),
        "ask_timeout_seconds": int(os.environ.get("ASK_TIMEOUT_SECONDS", "21600")),
        "snooze_seconds": per_day,
        "turn_poll_seconds": max(MIN_TURN_POLL_SECONDS, min(per_day // 4, MAX_TURN_POLL_SECONDS)),
    }


def consume(event):
    """Close the item a free-text message refers to, eaten or thrown away."""
    fridge_key = _fridge_key(str(event["chat_id"]))
    tossed = event.get("disposal") == "discarded"
    match = _pick(event.get("food", ""), _active_tuppers(fridge_key))
    previous = None
    if match:
        previous = _close(fridge_key, match["item_id"], "DISCARDED" if tossed else "CONSUMED")
    if not previous:
        return {"found": False, "food": event.get("food", "")}
    result = {
        "found": True,
        "food": previous["food"],
        "action": "tossed" if tossed else "eaten",
        "task_token": previous.get("task_token", ""),
        **_what_follows(fridge_key, previous),
    }
    if not tossed and _is_overdue(previous):
        result["overdue"] = True
    return result


def list_active(event):
    chat_key = str(event["chat_id"])
    fridge_key = _fridge_key(chat_key)
    per_day = _seconds_per_day()
    now = _now()
    items = sorted(_active_tuppers(fridge_key), key=lambda item: int(item["expires_epoch"]))
    return {
        "items": [
            {
                "food": item["food"],
                "days_left": math.ceil((int(item["expires_epoch"]) - now) / per_day),
                "needs_review": bool(item.get("needs_review")),
                "stored_by_name": ""
                if _asker(item, fridge_key) == chat_key
                else item.get("stored_by_name") or "",
            }
            for item in items
        ],
        "review_count": sum(1 for item in items if item.get("needs_review")),
        "shared": len(_members(fridge_key)) > 1,
    }


def resolve(event):
    """Apply a button press. `data` is untrusted: "<code>|<tupper_id>"."""
    fridge_key = _fridge_key(str(event["chat_id"]))
    code, _, tupper_id = str(event.get("data", "")).partition("|")
    action = BUTTON_ACTIONS.get(code)
    if not action or not tupper_id.isalnum() or len(tupper_id) > MAX_TUPPER_ID_LENGTH:
        return {"action": "already"}
    if action == "review":
        pending = [i for i in _active_tuppers(fridge_key) if i.get("needs_review")]
        review = _review_question(fridge_key, pending)
        return {"action": "review", "review_id": review.get("item_id", ""), "review": review}
    item_id = TUPPER_PREFIX + tupper_id
    if action == "snooze":
        # Only an open question can be postponed. An old button on an item that
        # was left for review must not promise a question nobody will ask.
        current = _tuppers().get_item(
            Key={"chat_id": fridge_key, "item_id": item_id}, ConsistentRead=True
        ).get("Item")
        if current and current.get("status") == "ACTIVE" and not current.get("task_token"):
            return {"action": "pending", "food": current["food"]}
    previous = _close(fridge_key, item_id, CLOSING_STATUS.get(action))
    if not previous:
        return {"action": "already"}
    result = {
        "action": action,
        "food": previous["food"],
        "task_token": previous.get("task_token", ""),
        **_what_follows(fridge_key, previous),
    }
    if action == "eaten" and _is_overdue(previous):
        result["overdue"] = True
    return result


def claim(event):
    """Take the asker's question turn for an item. One question is open per
    person; a turn nobody released expires after the question timeout."""
    chat_key = str(event["chat_id"])
    fridge_key, item_id = event["fridge_key"], event["item_id"]
    now = int(_now())
    timeout = int(os.environ.get("ASK_TIMEOUT_SECONDS", "21600"))
    granted = _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": chat_key, "item_id": PROFILE_KEY},
            UpdateExpression="SET asking = :item, asking_until = :until",
            ConditionExpression=(
                "attribute_not_exists(asking) OR asking = :item OR asking_until < :now"
            ),
            ExpressionAttributeValues={
                ":item": item_id,
                ":until": now + timeout + SLOT_MARGIN_SECONDS,
                ":now": now,
            },
        )
    )
    if not granted:
        return {"granted": False, "waiting": 0}
    _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": fridge_key, "item_id": item_id},
            UpdateExpression="REMOVE turn_token",
            ConditionExpression="attribute_exists(item_id)",
        )
    )
    due = [
        item
        for item in _active_tuppers(fridge_key)
        if item["item_id"] != item_id
        and _asker(item, fridge_key) == chat_key
        and int(item["expires_epoch"]) <= now
    ]
    return {"granted": True, "waiting": len(due)}


def wait_turn(event):
    """Remember the token that wakes this item's execution when the turn frees up."""
    _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": event["fridge_key"], "item_id": event["item_id"]},
            UpdateExpression="SET turn_token = :token",
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":token": event["task_token"], ":active": "ACTIVE"},
        )
    )
    return {}


def escalate(event):
    """A question went unanswered: keep the item in the fridge, flagged for
    review, and report who else should hear about it."""
    chat_key = str(event["chat_id"])
    fridge_key, item_id = event["fridge_key"], event["item_id"]
    response = _conditional(
        lambda: _tuppers().update_item(
            Key={"chat_id": fridge_key, "item_id": item_id},
            UpdateExpression="SET needs_review = :yes REMOVE task_token",
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":yes": True, ":active": "ACTIVE"},
            ReturnValues="ALL_OLD",
        )
    )
    previous = response.get("Attributes") if response else None
    follows = _what_follows(fridge_key, previous or {"item_id": item_id, "stored_by": chat_key})
    return {
        "escalated": bool(previous),
        "food": previous["food"] if previous else "",
        "owner_name": _profile(chat_key).get("first_name", ""),
        "others": [member for member in _members(fridge_key) if member != chat_key],
        "wake_token": follows.get("wake_token", ""),
    }


def invite(event):
    chat_key = str(event["chat_id"])
    code = secrets.token_urlsafe(9)
    _remember_name(chat_key, event.get("first_name"))
    _tuppers().put_item(
        Item={
            "chat_id": INVITE_PARTITION,
            "item_id": code,
            "fridge_id": _fridge_key(chat_key),
            "inviter_name": event.get("first_name") or "",
            "ttl": int(_now()) + INVITE_TTL_SECONDS,
        }
    )
    return {"code": code}


def link(event):
    """Add the caller to the fridge of whoever created the invite code."""
    chat_key = str(event["chat_id"])
    key = {"chat_id": INVITE_PARTITION, "item_id": str(event.get("code", ""))}
    if not key["item_id"]:
        return {"linked": False}
    invitation = _tuppers().get_item(Key=key).get("Item")
    if not invitation or int(invitation.get("ttl", 0)) < _now():
        return {"linked": False}
    fridge_key = invitation["fridge_id"]
    previous_fridge = _fridge_key(chat_key)
    if previous_fridge == fridge_key:
        return {"linked": False}
    # Leaving a fridge nobody else shares would hide its food from everyone.
    stays_shared = any(member != chat_key for member in _members(previous_fridge))
    left_behind = 0 if stays_shared else len(_active_tuppers(previous_fridge))
    if left_behind:
        return {"linked": False, "own_items": left_behind}
    # The code is single use: only the caller that deletes it gets in.
    if not _conditional(
        lambda: _tuppers().delete_item(Key=key, ConditionExpression="attribute_exists(item_id)")
    ):
        return {"linked": False}
    _tuppers().update_item(
        Key={"chat_id": previous_fridge, "item_id": FRIDGE_KEY},
        UpdateExpression="DELETE members :member",
        ExpressionAttributeValues={":member": {chat_key}},
    )
    _tuppers().update_item(
        Key={"chat_id": fridge_key, "item_id": FRIDGE_KEY},
        UpdateExpression="ADD members :members",
        ExpressionAttributeValues={":members": {fridge_key, chat_key}},
    )
    _tuppers().update_item(
        Key={"chat_id": chat_key, "item_id": PROFILE_KEY},
        UpdateExpression="SET fridge_id = :fridge",
        ExpressionAttributeValues={":fridge": fridge_key},
    )
    _remember_name(chat_key, event.get("first_name"))
    return {"linked": True, "owner_name": invitation.get("inviter_name", "")}


_ACTIONS = {
    "store": store,
    "consume": consume,
    "list": list_active,
    "resolve": resolve,
    "claim": claim,
    "wait_turn": wait_turn,
    "escalate": escalate,
    "invite": invite,
    "link": link,
}


def handler(event, _context=None):
    result = _ACTIONS[event["action"]](event)
    for key in ("task_token", "action", "wake_token", "review_id"):
        result.setdefault(key, "")
    return result
