"""Inventory Lambda: every read and write of tupper and profile state.

DynamoDB is the source of truth. Each tupper is closed with a conditional
update on `status = ACTIVE`, so a button press, a text message, and the
workflow timeout can race safely: exactly one of them wins.

Dispatches on `event["action"]`; every response includes `task_token` and
`action` so the workflow can treat all replies uniformly.
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
INVITE_PARTITION = "INVITE"
TUPPER_PREFIX = "T#"
DEFAULT_DAYS = 2
INVITE_TTL_SECONDS = 7 * 24 * 3600
MAX_TUPPER_ID_LENGTH = 32

BUTTON_ACTIONS = {"e": "eaten", "t": "tossed", "s": "snooze"}
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


def _bump_rescued(chat_key):
    response = _tuppers().update_item(
        Key={"chat_id": chat_key, "item_id": PROFILE_KEY},
        UpdateExpression="ADD rescued_count :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(response["Attributes"]["rescued_count"])


def _active_tuppers(chat_key):
    items, start_key = [], None
    while True:
        query = {
            "KeyConditionExpression": Key("chat_id").eq(chat_key)
            & Key("item_id").begins_with(TUPPER_PREFIX),
            "FilterExpression": Attr("status").eq("ACTIVE"),
        }
        if start_key:
            query["ExclusiveStartKey"] = start_key
        response = _tuppers().query(**query)
        items.extend(response["Items"])
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def _close(chat_key, item_id, new_status):
    """Move an ACTIVE tupper to `new_status` (or just clear its token when
    `new_status` is None). Returns the previous item, or None if it was not
    ACTIVE anymore."""
    update = "REMOVE task_token" if new_status is None else "SET #s = :new REMOVE task_token"
    values = {":active": "ACTIVE"}
    if new_status is not None:
        values[":new"] = new_status
    try:
        response = _tuppers().update_item(
            Key={"chat_id": chat_key, "item_id": item_id},
            UpdateExpression=update,
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_OLD",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return response.get("Attributes")


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
    food = event["food"]
    days, default_used = _shelf_days(event.get("category") or "other")
    per_day = _seconds_per_day()
    now = _now()
    expires = now + days * per_day
    tupper_id = secrets.token_hex(6)
    item_id = TUPPER_PREFIX + tupper_id
    _tuppers().put_item(
        Item={
            "chat_id": chat_key,
            "item_id": item_id,
            "food": food,
            "category": event.get("category") or "other",
            "status": "ACTIVE",
            "stored_at": _iso(now),
            "expires_at": _iso(expires),
            "expires_epoch": int(expires),
        }
    )
    _remember_name(chat_key, event.get("first_name"))
    return {
        "chat_key": chat_key,
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
    }


def consume(event):
    chat_key = str(event["chat_id"])
    match = _pick(event.get("food", ""), _active_tuppers(chat_key))
    previous = _close(chat_key, match["item_id"], "CONSUMED") if match else None
    if not previous:
        return {"found": False, "food": event.get("food", "")}
    return {
        "found": True,
        "food": previous["food"],
        "action": "eaten",
        "task_token": previous.get("task_token", ""),
        "rescued_count": _bump_rescued(chat_key),
    }


def list_active(event):
    chat_key = str(event["chat_id"])
    per_day = _seconds_per_day()
    now = _now()
    items = sorted(_active_tuppers(chat_key), key=lambda item: int(item["expires_epoch"]))
    return {
        "items": [
            {
                "food": item["food"],
                "days_left": math.ceil((int(item["expires_epoch"]) - now) / per_day),
            }
            for item in items
        ],
        "rescued_count": int(_profile(chat_key).get("rescued_count", 0)),
    }


def resolve(event):
    """Apply a button press. `data` is untrusted: "<code>|<tupper_id>"."""
    chat_key = str(event["chat_id"])
    code, _, tupper_id = str(event.get("data", "")).partition("|")
    action = BUTTON_ACTIONS.get(code)
    if not action or not tupper_id.isalnum() or len(tupper_id) > MAX_TUPPER_ID_LENGTH:
        return {"action": "already"}
    previous = _close(chat_key, TUPPER_PREFIX + tupper_id, CLOSING_STATUS.get(action))
    if not previous:
        return {"action": "already"}
    result = {
        "action": action,
        "food": previous["food"],
        "task_token": previous.get("task_token", ""),
    }
    if action == "eaten":
        result["rescued_count"] = _bump_rescued(chat_key)
    return result


def expire(event):
    chat_key = str(event["chat_id"])
    previous = _close(chat_key, event["item_id"], "EXPIRED")
    if not previous:
        return {"expired": False, "food": "", "caregiver_chat_id": "", "owner_name": ""}
    profile = _profile(chat_key)
    return {
        "expired": True,
        "food": previous["food"],
        "caregiver_chat_id": profile.get("caregiver_chat_id", ""),
        "owner_name": profile.get("first_name", ""),
    }


def invite(event):
    chat_key = str(event["chat_id"])
    code = secrets.token_urlsafe(9)
    _remember_name(chat_key, event.get("first_name"))
    _tuppers().put_item(
        Item={
            "chat_id": INVITE_PARTITION,
            "item_id": code,
            "owner_chat_id": chat_key,
            "ttl": int(_now()) + INVITE_TTL_SECONDS,
        }
    )
    return {"code": code}


def link(event):
    """Register the caller as caregiver of whoever created the invite code."""
    caregiver_key = str(event["chat_id"])
    key = {"chat_id": INVITE_PARTITION, "item_id": str(event.get("code", ""))}
    if not key["item_id"]:
        return {"linked": False}
    invitation = _tuppers().get_item(Key=key).get("Item")
    expired = not invitation or int(invitation.get("ttl", 0)) < _now()
    if expired or invitation["owner_chat_id"] == caregiver_key:
        return {"linked": False}
    owner_key = invitation["owner_chat_id"]
    _tuppers().update_item(
        Key={"chat_id": owner_key, "item_id": PROFILE_KEY},
        UpdateExpression="SET caregiver_chat_id = :caregiver",
        ExpressionAttributeValues={":caregiver": caregiver_key},
    )
    _tuppers().delete_item(Key=key)
    return {"linked": True, "owner_name": _profile(owner_key).get("first_name", "")}


_ACTIONS = {
    "store": store,
    "consume": consume,
    "list": list_active,
    "resolve": resolve,
    "expire": expire,
    "invite": invite,
    "link": link,
}


def handler(event, _context=None):
    result = _ACTIONS[event["action"]](event)
    result.setdefault("task_token", "")
    result.setdefault("action", "")
    return result
