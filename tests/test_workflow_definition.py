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
    assert ask["Catch"] == [{"ErrorEquals": ["States.Timeout"], "Next": "Expire"}]


def test_status_is_reread_after_every_wait():
    for wait in (name for name, state in STATES.items() if state["Type"] == "Wait"):
        check = STATES[STATES[wait]["Next"]]
        assert check["Resource"] == "arn:aws:states:::dynamodb:getItem", wait


def test_last_question_is_flagged_so_it_cannot_be_snoozed():
    data = STATES["AskWhatHappened"]["Arguments"]["Payload"]["data"]
    assert "'last_chance': $asks >= 2" in data
    assert STATES["AfterAnswer"]["Choices"][0]["Condition"] == (
        "{% $answer = 'snooze' and $asks < 3 %}"
    )
