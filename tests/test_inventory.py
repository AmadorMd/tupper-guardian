from conftest import NOW

from guardian import inventory

CHAT = 111
RELATIVE = 222
DAY = 86400


def call(action, chat=CHAT, **fields):
    return inventory.handler({"action": action, "chat_id": chat, **fields})


def stored_item(tables, result):
    return tables.get_item(Key={"chat_id": result["fridge_key"], "item_id": result["item_id"]})[
        "Item"
    ]


def store(food="caldo de pollo", category="cooked_soup_stew", chat=CHAT, first_name="Lupita"):
    return call("store", chat=chat, food=food, category=category, first_name=first_name)


def set_token(tables, result, name="task_token", value="TOKEN"):
    tables.update_item(
        Key={"chat_id": result["fridge_key"], "item_id": result["item_id"]},
        UpdateExpression="SET #n = :t",
        ExpressionAttributeNames={"#n": name},
        ExpressionAttributeValues={":t": value},
    )


def share_fridge():
    """Lupita (CHAT) invites her relative (RELATIVE) into her fridge."""
    code = call("invite", first_name="Lupita")["code"]
    return call("link", chat=RELATIVE, code=code, first_name="Ana")


def escalate(result, chat=CHAT):
    return call("escalate", chat=chat, item_id=result["item_id"], fridge_key=result["fridge_key"])


# ---------------------------------------------------------------- storing


def test_store_uses_table_shelf_life_and_schedules_both_alerts(tables):
    result = store()

    assert result["days"] == 3
    assert result["default_used"] is False
    assert result["expires_at"] == inventory._iso(NOW + 3 * DAY)
    assert result["remind_at"] == inventory._iso(NOW + 2 * DAY)
    assert result["fridge_key"] == str(CHAT)
    item = stored_item(tables, result)
    assert item["status"] == "ACTIVE"
    assert item["food"] == "caldo de pollo"
    assert item["stored_by"] == str(CHAT)
    assert int(item["expires_epoch"]) == NOW + 3 * DAY
    assert result["task_token"] == ""


def test_store_unknown_category_falls_back_to_conservative_default(tables):
    result = store(food="comida rara", category="other")

    assert result["days"] == inventory.DEFAULT_DAYS
    assert result["default_used"] is True


def test_store_one_day_item_has_no_early_reminder(tables):
    assert store(food="guacamole", category="guacamole")["remind_at"] == ""


def test_demo_clock_shortens_every_interval(tables, monkeypatch):
    monkeypatch.setenv("SECONDS_PER_DAY", "20")

    result = store()

    assert result["expires_at"] == inventory._iso(NOW + 60)
    assert result["snooze_seconds"] == 20
    assert result["turn_poll_seconds"] == 5


def test_real_clock_polls_for_the_question_turn_every_fifteen_minutes(tables):
    assert store()["turn_poll_seconds"] == 900


# ------------------------------------------------------- eating, by text


def test_consume_matches_ignoring_accents_and_closes_the_item(tables):
    result = store(food="Caldo de pollo")
    set_token(tables, result)

    consumed = call("consume", food="el caldó")

    assert consumed["found"] is True
    assert consumed["food"] == "Caldo de pollo"
    assert consumed["task_token"] == "TOKEN"
    assert consumed["action"] == "eaten"
    assert "rescued_count" not in consumed
    item = stored_item(tables, result)
    assert item["status"] == "CONSUMED"
    assert "task_token" not in item


def test_consume_reported_as_thrown_away_is_discarded_never_overdue(tables, monkeypatch):
    result = store()
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 10 * DAY)

    consumed = call("consume", food="caldo", disposal="discarded")

    assert consumed["found"] is True and consumed["action"] == "tossed"
    assert "overdue" not in consumed
    assert stored_item(tables, result)["status"] == "DISCARDED"


def test_consume_unknown_food_changes_nothing(tables):
    result = store()

    consumed = call("consume", food="pizza")

    assert consumed["found"] is False
    assert consumed["task_token"] == ""
    assert stored_item(tables, result)["status"] == "ACTIVE"


def test_consume_without_a_name_resolves_the_only_item(tables):
    store()

    assert call("consume", food="")["found"] is True


def test_consume_without_a_name_is_ambiguous_with_several_items(tables):
    store()
    store(food="arroz")

    assert call("consume", food="")["found"] is False


# ----------------------------------------------------------------- list


def test_list_returns_only_active_items_soonest_first(tables):
    store(food="caldo", category="cooked_soup_stew")
    store(food="guacamole", category="guacamole")
    store(food="arroz", category="other")
    call("consume", food="arroz")

    listing = call("list")

    assert [(item["food"], item["days_left"]) for item in listing["items"]] == [
        ("guacamole", 1),
        ("caldo", 3),
    ]
    assert listing["review_count"] == 0
    assert "rescued_count" not in listing


def test_list_names_who_stored_an_item_only_when_it_was_someone_else(tables):
    share_fridge()
    store(food="caldo")
    store(food="arroz", category="other", chat=RELATIVE, first_name="Ana")

    mine = {item["food"]: item["stored_by_name"] for item in call("list")["items"]}

    assert mine == {"caldo": "", "arroz": "Ana"}


# -------------------------------------------------------------- buttons


def test_resolve_eaten_closes_the_item_and_returns_its_token(tables):
    result = store()
    set_token(tables, result)

    resolved = call("resolve", data=f"e|{result['tupper_id']}")

    assert resolved["action"] == "eaten"
    assert resolved["food"] == "caldo de pollo"
    assert resolved["task_token"] == "TOKEN"
    assert "rescued_count" not in resolved and "overdue" not in resolved
    assert stored_item(tables, result)["status"] == "CONSUMED"


def test_eating_right_at_the_deadline_is_not_overdue(tables, monkeypatch):
    result = store()  # 3 days
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 4 * DAY - 1)

    assert "overdue" not in call("resolve", data=f"e|{result['tupper_id']}")


def test_eating_a_day_past_the_deadline_is_overdue(tables, monkeypatch):
    by_button, by_text = store(), store(food="arroz rojo")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 4 * DAY)

    resolved = call("resolve", data=f"e|{by_button['tupper_id']}")
    consumed = call("consume", food="arroz")

    assert resolved["action"] == "eaten" and resolved["overdue"] is True
    assert consumed["found"] is True and consumed["overdue"] is True
    assert stored_item(tables, by_button)["status"] == "CONSUMED"
    assert stored_item(tables, by_text)["status"] == "CONSUMED"


def test_resolve_tossed_discards_the_item(tables):
    result = store()

    resolved = call("resolve", data=f"t|{result['tupper_id']}")

    assert resolved["action"] == "tossed"
    assert stored_item(tables, result)["status"] == "DISCARDED"


def test_resolve_snooze_keeps_the_item_active_and_clears_the_token(tables):
    result = store()
    set_token(tables, result)

    resolved = call("resolve", data=f"s|{result['tupper_id']}")

    assert resolved["action"] == "snooze"
    assert resolved["task_token"] == "TOKEN"
    item = stored_item(tables, result)
    assert item["status"] == "ACTIVE"
    assert "task_token" not in item


def test_second_button_press_is_reported_as_already_resolved(tables):
    result = store()
    call("resolve", data=f"e|{result['tupper_id']}")

    again = call("resolve", data=f"t|{result['tupper_id']}")

    assert again["action"] == "already" and again["task_token"] == ""
    assert stored_item(tables, result)["status"] == "CONSUMED"


def test_resolve_rejects_malformed_button_data(tables):
    result = store()

    for data in ("", "x|abc", "e|", "e|../PROFILE", f"e|{'a' * 64}"):
        assert call("resolve", data=data)["action"] == "already"
    assert stored_item(tables, result)["status"] == "ACTIVE"


def test_resolve_cannot_touch_a_fridge_the_sender_does_not_belong_to(tables):
    result = store()

    other = call("resolve", chat=999, data=f"e|{result['tupper_id']}")

    assert other["action"] == "already"
    assert stored_item(tables, result)["status"] == "ACTIVE"


# ------------------------------------------ nothing disappears by silence


def test_unanswered_item_stays_in_the_fridge_flagged_for_review(tables, monkeypatch):
    result = store()
    set_token(tables, result)
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)

    outcome = escalate(result)

    assert outcome["escalated"] is True
    assert outcome["food"] == "caldo de pollo"
    assert outcome["owner_name"] == "Lupita"
    assert outcome["others"] == []
    item = stored_item(tables, result)
    assert item["status"] == "ACTIVE" and item["needs_review"] is True
    assert "task_token" not in item
    listing = call("list")
    assert listing["review_count"] == 1
    assert listing["items"][0]["food"] == "caldo de pollo"
    assert listing["items"][0]["needs_review"] is True


def test_escalate_skips_an_item_that_was_already_resolved(tables):
    result = store()
    call("consume", food="caldo")

    outcome = escalate(result)

    assert outcome["escalated"] is False
    assert stored_item(tables, result)["status"] == "CONSUMED"


def test_review_button_walks_the_flagged_items_one_by_one(tables, monkeypatch):
    first, second = store(food="caldo"), store(food="arroz", category="guacamole")
    fresh = store(food="pizza")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)
    escalate(first)
    escalate(second)

    start = call("resolve", data="r|0")
    answered = call("resolve", data=f"t|{start['review']['tupper_id']}")
    done = call("resolve", data=f"t|{answered['review']['tupper_id']}")

    assert start["action"] == "review"
    assert start["review"]["food"] == "arroz"  # expired first
    assert start["review"]["review"] is True and start["review"]["waiting"] == 1
    assert start["review_id"] == second["item_id"]
    assert answered["review"]["food"] == "caldo" and answered["review"]["waiting"] == 0
    assert done["review_id"] == ""
    assert stored_item(tables, fresh)["status"] == "ACTIVE"
    assert call("resolve", data="r|0")["review_id"] == ""


# ------------------------------------------------- one question at a time


def test_only_one_question_is_open_per_person(tables, monkeypatch):
    first, second = store(food="caldo"), store(food="arroz")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)

    granted = call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])
    again = call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])
    denied = call("claim", item_id=second["item_id"], fridge_key=second["fridge_key"])

    assert granted == {**granted, "granted": True, "waiting": 1}
    assert again["granted"] is True
    assert denied["granted"] is False


def test_answering_frees_the_turn_and_wakes_the_item_that_waited_longest(tables, monkeypatch):
    first, second, third = store(food="caldo"), store(food="arroz"), store(food="pizza")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)
    call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])
    for waiting, token in ((third, "TURN-3"), (second, "TURN-2")):
        call("wait_turn", item_id=waiting["item_id"], fridge_key=waiting["fridge_key"],
             task_token=token)
    tables.update_item(
        Key={"chat_id": second["fridge_key"], "item_id": second["item_id"]},
        UpdateExpression="SET expires_epoch = :e",
        ExpressionAttributeValues={":e": NOW + DAY},
    )

    resolved = call("resolve", data=f"e|{first['tupper_id']}")
    turn = call("claim", item_id=second["item_id"], fridge_key=second["fridge_key"])

    assert resolved["wake_token"] == "TURN-2"
    assert resolved["review_id"] == ""
    assert turn["granted"] is True
    assert "turn_token" not in stored_item(tables, second)


def test_timeout_frees_the_turn_and_wakes_the_next_item(tables, monkeypatch):
    first, second = store(food="caldo"), store(food="arroz")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)
    call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])
    call("wait_turn", item_id=second["item_id"], fridge_key=second["fridge_key"],
         task_token="TURN-2")

    outcome = escalate(first)

    assert outcome["wake_token"] == "TURN-2"
    assert call("claim", item_id=second["item_id"], fridge_key=second["fridge_key"])["granted"]


def test_a_turn_that_was_never_released_expires_on_its_own(tables, monkeypatch):
    first, second = store(food="caldo"), store(food="arroz")
    call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])
    late = NOW + int(inventory.os.environ["ASK_TIMEOUT_SECONDS"]) + inventory.SLOT_MARGIN_SECONDS
    monkeypatch.setattr(inventory, "_now", lambda: late + 1)

    assert call("claim", item_id=second["item_id"], fridge_key=second["fridge_key"])["granted"]


def test_waiting_for_a_turn_on_a_resolved_item_saves_nothing(tables):
    result = store()
    call("consume", food="caldo")

    call("wait_turn", item_id=result["item_id"], fridge_key=result["fridge_key"],
         task_token="TURN")

    assert "turn_token" not in stored_item(tables, result)


# --------------------------------------------------------- shared fridge


def test_invited_relative_shares_the_fridge(tables):
    mine = store(food="caldo")

    linked = share_fridge()
    theirs = store(food="arroz", category="other", chat=RELATIVE, first_name="Ana")

    assert linked["linked"] is True and linked["owner_name"] == "Lupita"
    assert theirs["fridge_key"] == str(CHAT)
    assert stored_item(tables, theirs)["stored_by"] == str(RELATIVE)
    assert {item["food"] for item in call("list", chat=RELATIVE)["items"]} == {"caldo", "arroz"}
    assert call("list", chat=RELATIVE)["shared"] is True
    assert call("resolve", chat=RELATIVE, data=f"t|{mine['tupper_id']}")["action"] == "tossed"
    assert stored_item(tables, mine)["status"] == "DISCARDED"


def test_unanswered_item_notifies_the_other_members(tables):
    share_fridge()
    mine = store(food="caldo")
    theirs = store(food="arroz", category="other", chat=RELATIVE, first_name="Ana")

    assert escalate(mine)["others"] == [str(RELATIVE)]
    outcome = escalate(theirs, chat=RELATIVE)
    assert outcome["others"] == [str(CHAT)] and outcome["owner_name"] == "Ana"


def test_a_relative_answering_frees_the_turn_of_whoever_was_asked(tables, monkeypatch):
    share_fridge()
    first, second = store(food="caldo"), store(food="arroz")
    monkeypatch.setattr(inventory, "_now", lambda: NOW + 3 * DAY + 1)
    call("claim", item_id=first["item_id"], fridge_key=first["fridge_key"])

    call("resolve", chat=RELATIVE, data=f"t|{first['tupper_id']}")

    assert call("claim", item_id=second["item_id"], fridge_key=second["fridge_key"])["granted"]


def test_invite_code_works_once(tables):
    code = call("invite", first_name="Lupita")["code"]

    assert call("link", chat=RELATIVE, code=code)["linked"] is True
    assert call("link", chat=333, code=code)["linked"] is False


def test_a_member_can_invite_and_joining_another_fridge_leaves_the_previous_one(tables):
    share_fridge()
    third = call("link", chat=333, code=call("invite", chat=RELATIVE, first_name="Ana")["code"])
    other_fridge = call("invite", chat=999, first_name="Rosa")["code"]

    call("link", chat=RELATIVE, code=other_fridge)
    mine = store(food="caldo")

    assert third["linked"] is True and third["owner_name"] == "Ana"
    assert escalate(mine)["others"] == ["333"]
    assert store(food="pan", category="other", chat=RELATIVE)["fridge_key"] == "999"


def test_link_rejects_own_fridge_unknown_code_and_expired_invite(tables, monkeypatch):
    share_fridge()
    code = call("invite", first_name="Lupita")["code"]

    assert call("link", code=code)["linked"] is False
    assert call("link", chat=RELATIVE, code=code)["linked"] is False
    assert call("link", chat=333, code="nope")["linked"] is False
    assert call("link", chat=333, code="")["linked"] is False
    monkeypatch.setattr(inventory, "_now", lambda: NOW + inventory.INVITE_TTL_SECONDS + 1)
    assert call("link", chat=333, code=code)["linked"] is False
