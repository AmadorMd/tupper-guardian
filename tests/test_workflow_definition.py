"""Structural checks on the state machine and template that need no AWS account."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFINITION = json.loads((ROOT / "statemachine" / "guardian.asl.json").read_text("utf-8"))
RAW_DEFINITION = (ROOT / "statemachine" / "guardian.asl.json").read_text("utf-8")
TEMPLATE = (ROOT / "template.yaml").read_text("utf-8")
STATES = DEFINITION["States"]


def _targets(state):
    targets = [state.get("Next"), state.get("Default")]
    targets += [choice["Next"] for choice in state.get("Choices", [])]
    targets += [catch["Next"] for catch in state.get("Catch", [])]
    return [target for target in targets if target]


def test_every_transition_points_to_an_existing_state():
    assert DEFINITION["StartAt"] in STATES
    for name, state in STATES.items():
        for target in _targets(state):
            assert target in STATES, f"{name} -> {target}"


def test_every_state_is_reachable_and_can_finish():
    reachable, pending = set(), [DEFINITION["StartAt"]]
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(_targets(STATES[name]))
    assert reachable == set(STATES)
    for name, state in STATES.items():
        assert _targets(state) or state["Type"] in {"Succeed", "Fail"}, name


def test_definition_substitutions_match_the_template():
    used = set(re.findall(r"\$\{(\w+)\}", RAW_DEFINITION))
    block = TEMPLATE.split("DefinitionSubstitutions:")[1].split("Policies:")[0]
    declared = set(re.findall(r"^\s+(\w+):", block, re.MULTILINE))
    assert used == declared


def test_variables_are_assigned_before_any_state_reads_them():
    assigned = {key for state in STATES.values() for key in state.get("Assign", {})}
    assigned |= {
        key
        for state in STATES.values()
        for catch in state.get("Catch", [])
        for key in catch.get("Assign", {})
    }
    read = set(re.findall(r"\$([a-z_]+)\b(?!\()", RAW_DEFINITION)) - {"states"}
    assert read <= assigned, read - assigned


def test_question_state_waits_for_a_human_and_escalates_on_timeout():
    ask = STATES["AskWhatHappened"]
    assert ask["Resource"].endswith(".waitForTaskToken")
    assert "$states.context.Task.Token" in ask["Arguments"]["Payload"]["task_token"]
    assert ask["Catch"][0] == {"ErrorEquals": ["States.Timeout"], "Next": "Escalate"}
    # Any other failure must also release the question turn.
    assert ask["Catch"][1] == {"ErrorEquals": ["States.ALL"], "Next": "Escalate"}


def test_status_is_reread_after_every_wait():
    for wait in (name for name, state in STATES.items() if state["Type"] == "Wait"):
        check = STATES[STATES[wait]["Next"]]
        assert check["Resource"] == "arn:aws:states:::dynamodb:getItem", wait


def test_last_question_is_flagged_so_it_cannot_be_snoozed():
    data = STATES["AskWhatHappened"]["Arguments"]["Payload"]["data"]
    assert "'last_chance': $asks >= 2" in data and "'waiting': $turn.waiting" in data
    assert STATES["AfterAnswer"]["Choices"][0]["Condition"] == (
        "{% $answer = 'snooze' and $asks < 3 %}"
    )


def test_a_question_is_asked_only_after_taking_the_turn():
    assert STATES["StillActiveForAsk"]["Choices"][0]["Next"] == "ClaimTurn"
    assert STATES["ClaimTurn"]["Arguments"]["Payload"]["action"] == "claim"
    has_turn = STATES["HasTurn"]
    assert has_turn["Choices"] == [{"Condition": "{% $turn.granted %}", "Next": "AskWhatHappened"}]
    assert has_turn["Default"] == "WaitTurn"


def test_waiting_for_the_turn_is_woken_by_a_token_or_polls_again():
    wait = STATES["WaitTurn"]
    assert wait["Resource"].endswith(".waitForTaskToken")
    assert wait["Arguments"]["Payload"]["action"] == "wait_turn"
    assert wait["TimeoutSeconds"] == "{% $t.turn_poll_seconds %}"
    assert wait["Next"] == "CheckBeforeAsk"
    assert wait["Catch"] == [
        {"ErrorEquals": ["States.Timeout"], "Next": "CheckBeforeAsk"},
        # A failure while waiting must not leave the item unasked and unflagged.
        {"ErrorEquals": ["States.ALL"], "Next": "Escalate"},
    ]


def test_every_answer_path_wakes_the_next_question():
    assert STATES["HasToken"]["Default"] == "HasNext"
    assert STATES["ReleaseToken"]["Next"] == "HasNext"
    assert [choice["Next"] for choice in STATES["HasNext"]["Choices"]] == ["WakeNext", "AskReview"]
    assert STATES["AskReview"]["Arguments"]["Payload"]["data"] == "{% $r.review %}"
    for name in ("NotifyUnanswered", "NotifyOthers"):
        assert STATES[name]["Catch"][0]["ErrorEquals"] == ["States.ALL"], name
    assert STATES["HasWaiting"]["Choices"][0]["Next"] == "WakeWaiting"


def test_unanswered_items_are_escalated_to_every_other_member_not_expired():
    assert "Expire" not in STATES and "NotifyCaregiver" not in STATES
    assert STATES["Escalate"]["Arguments"]["Payload"]["action"] == "escalate"
    assert STATES["NotifyOthers"]["Arguments"]["Payload"]["chat_ids"] == "{% $e.others %}"
    assert STATES["AfterAnswer"]["Choices"][1]["Next"] == "Escalate"


def test_items_are_read_from_the_fridge_partition():
    for name in ("CheckBeforeReminder", "CheckBeforeAsk"):
        key = STATES[name]["Arguments"]["Key"]["chat_id"]["S"]
        assert key == "{% $t.fridge_key %}", name


def test_shopping_list_starts_one_tracked_execution_per_food():
    assert {"Condition": "{% $ctx.intent = 'store_list' %}", "Next": "StoreList"} in STATES[
        "ByIntent"
    ]["Choices"]
    assert STATES["StoreList"]["Arguments"]["Payload"]["action"] == "store_list"
    assert STATES["StoreList"]["Next"] == "ConfirmList"
    assert STATES["ConfirmList"]["Arguments"]["Payload"]["kind"] == "stored_list"
    track = STATES["TrackEach"]
    assert track["Type"] == "Map" and track["Items"] == "{% $t.items %}"
    start = track["ItemProcessor"]["States"]["StartTracking"]
    assert start["Resource"] == "arn:aws:states:::aws-sdk:sfn:startExecution"
    assert start["Arguments"]["StateMachineArn"] == "{% $states.context.StateMachine.Id %}"
    # Named after the parent execution, so a retry cannot track a food twice.
    assert "$states.context.Execution.Name" in start["Arguments"]["Name"]
    assert any(
        catch["ErrorEquals"] == ["Sfn.ExecutionAlreadyExistsException"]
        for catch in start.get("Catch", [])
    )


def test_tracked_food_execution_skips_straight_to_its_lifecycle():
    first = STATES["Route"]["Choices"][0]
    assert first == {"Condition": "{% $exists($states.input.lifecycle) %}", "Next": "StartLifecycle"}
    begin = STATES["StartLifecycle"]
    assert set(begin["Assign"]) == {"t", "chat", "asks", "cb"}
    assert begin["Next"] == "HasReminder"


def test_state_machine_may_start_only_its_own_executions():
    block = TEMPLATE.split("GuardianStateMachine:")[1].split("# ----")[0]
    assert "Action: states:StartExecution" in block
    assert ":stateMachine:GuardianStateMachine-*" in block


def test_shopping_list_storage_is_keyed_by_the_execution_so_retries_are_safe():
    payload = STATES["StoreList"]["Arguments"]["Payload"]
    assert payload["request_id"] == "{% $states.context.Execution.Name %}"
    assert payload["dropped"] == "{% $ctx.dropped %}"
