from conftest import NOW

from guardian import inventory

CHAT = 111
DAY = 86400


def call(action, **fields):
    return inventory.handler({"action": action, "chat_id": CHAT, **fields})


def stored_item(tables, result):
    return tables.get_item(Key={"chat_id": str(CHAT), "item_id": result["item_id"]})["Item"]


def store(food="caldo de pollo", category="cooked_soup_stew"):
    return call("store", food=food, category=category, first_name="Lupita")


def test_store_uses_table_shelf_life_and_schedules_both_alerts(tables):
    result = store()

    assert result["days"] == 3
    assert result["default_used"] is False
    assert result["expires_at"] == inventory._iso(NOW + 3 * DAY)
    assert result["remind_at"] == inventory._iso(NOW + 2 * DAY)
    item = stored_item(tables, result)
    assert item["status"] == "ACTIVE"
    assert item["food"] == "caldo de pollo"
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


def test_consume_matches_ignoring_accents_and_counts_the_rescue(tables):
    result = store(food="Caldo de pollo")
    tables.update_item(
        Key={"chat_id": str(CHAT), "item_id": result["item_id"]},
        UpdateExpression="SET task_token = :t",
        ExpressionAttributeValues={":t": "TOKEN"},
    )

    consumed = call("consume", food="el caldó")

    assert consumed["found"] is True
    assert consumed["food"] == "Caldo de pollo"
    assert consumed["task_token"] == "TOKEN"
    assert consumed["action"] == "eaten"
    assert consumed["rescued_count"] == 1
    item = stored_item(tables, result)
    assert item["status"] == "CONSUMED"
    assert "task_token" not in item


def test_consume_unknown_food_changes_nothing(tables):
    result = store()

    consumed = call("consume", food="pizza")

    assert consumed["found"] is False
    assert consumed["task_token"] == ""
    assert stored_item(tables, result)["status"] == "ACTIVE"


def test_consume_without_a_name_resolves_the_only_tupper(tables):
    store()

    assert call("consume", food="")["found"] is True


def test_consume_without_a_name_is_ambiguous_with_several_tuppers(tables):
    store()
    store(food="arroz")

    assert call("consume", food="")["found"] is False


def test_list_returns_only_active_items_soonest_first(tables):
    store(food="caldo", category="cooked_soup_stew")
    store(food="guacamole", category="guacamole")
    store(food="arroz", category="other")
    call("consume", food="arroz")

    listing = call("list")

    assert listing["items"] == [
        {"food": "guacamole", "days_left": 1},
        {"food": "caldo", "days_left": 3},
    ]
    assert listing["rescued_count"] == 1


def test_resolve_eaten_closes_the_tupper_and_returns_its_token(tables):
    result = store()
    tables.update_item(
        Key={"chat_id": str(CHAT), "item_id": result["item_id"]},
        UpdateExpression="SET task_token = :t",
        ExpressionAttributeValues={":t": "TOKEN"},
    )

    resolved = call("resolve", data=f"e|{result['tupper_id']}")

    assert resolved == {
        "action": "eaten",
        "food": "caldo de pollo",
        "task_token": "TOKEN",
        "rescued_count": 1,
    }
    assert stored_item(tables, result)["status"] == "CONSUMED"


def test_resolve_tossed_does_not_count_as_rescued(tables):
    result = store()

    resolved = call("resolve", data=f"t|{result['tupper_id']}")

    assert resolved["action"] == "tossed"
    assert "rescued_count" not in resolved
    assert stored_item(tables, result)["status"] == "DISCARDED"


def test_resolve_snooze_keeps_the_tupper_active_and_clears_the_token(tables):
    result = store()
    tables.update_item(
        Key={"chat_id": str(CHAT), "item_id": result["item_id"]},
        UpdateExpression="SET task_token = :t",
        ExpressionAttributeValues={":t": "TOKEN"},
    )

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

    assert again == {"action": "already", "task_token": ""}
    assert stored_item(tables, result)["status"] == "CONSUMED"
    assert call("list")["rescued_count"] == 1


def test_resolve_rejects_malformed_button_data(tables):
    result = store()

    for data in ("", "x|abc", "e|", "e|../PROFILE", f"e|{'a' * 64}"):
        assert call("resolve", data=data)["action"] == "already"
    assert stored_item(tables, result)["status"] == "ACTIVE"


def test_resolve_cannot_touch_another_chats_tupper(tables):
    result = store()

    other = inventory.handler(
        {"action": "resolve", "chat_id": 999, "data": f"e|{result['tupper_id']}"}
    )

    assert other["action"] == "already"
    assert stored_item(tables, result)["status"] == "ACTIVE"


def test_expire_without_caregiver(tables):
    result = store()

    expired = call("expire", item_id=result["item_id"])

    assert expired["expired"] is True
    assert expired["food"] == "caldo de pollo"
    assert expired["caregiver_chat_id"] == ""
    assert expired["owner_name"] == "Lupita"
    assert stored_item(tables, result)["status"] == "EXPIRED"


def test_expire_skips_a_tupper_that_was_already_resolved(tables):
    result = store()
    call("consume", food="caldo")

    expired = call("expire", item_id=result["item_id"])

    assert expired["expired"] is False
    assert stored_item(tables, result)["status"] == "CONSUMED"


def test_invite_then_link_registers_the_caregiver_once(tables):
    code = call("invite", first_name="Lupita")["code"]

    linked = inventory.handler({"action": "link", "chat_id": 222, "code": code})
    reused = inventory.handler({"action": "link", "chat_id": 333, "code": code})

    assert linked["linked"] is True
    assert linked["owner_name"] == "Lupita"
    assert reused["linked"] is False
    result = store()
    assert call("expire", item_id=result["item_id"])["caregiver_chat_id"] == "222"


def test_link_rejects_own_invite_unknown_code_and_expired_invite(tables, monkeypatch):
    code = call("invite", first_name="Lupita")["code"]

    assert call("link", code=code)["linked"] is False
    assert inventory.handler({"action": "link", "chat_id": 222, "code": "nope"})["linked"] is False
    assert inventory.handler({"action": "link", "chat_id": 222, "code": ""})["linked"] is False

    monkeypatch.setattr(inventory, "_now", lambda: NOW + inventory.INVITE_TTL_SECONDS + 1)
    assert inventory.handler({"action": "link", "chat_id": 222, "code": code})["linked"] is False
