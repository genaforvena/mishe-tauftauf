import json

import pytest

from mishe_tauftauf.inference_loop import drive_native
from mishe_tauftauf.inference_worker import WorkerTurn


CALL = {"type": "toolCall", "id": "call_native|fc_native", "name": "inspect",
        "arguments": {"path": "owned-evidence"}}
CONTEXT = {"messages": [{"role": "user", "content": "Inspect evidence"}],
           "tools": [{"name": "inspect"}]}


def turn(calls=(), *, terminal="done", reason=None):
    return WorkerTurn(terminal, {"role": "assistant", "content": list(calls),
                                "stopReason": reason or ("toolUse" if calls else "stop"),
                                "providerPayload": {"opaque": "retained"}})


class Session:
    def __init__(self, *turns):
        self.turns = iter(turns)
        self.inputs = []

    def turn(self, context):
        self.inputs.append(context)
        return next(self.turns)


def drive(session, **overrides):
    events, effects = [], []
    options = dict(obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                   dispatch=lambda call: effects.append(call) or {"status": "completed"},
                   record=events.append, phase=lambda call: None,
                   cancelled=lambda: False, authorized=lambda call: True,
                   complete=lambda context: False)
    options.update(overrides)
    result = drive_native(session, CONTEXT, **options)
    return result, events, effects


def test_next_model_input_preserves_correlated_result_and_opaque_history():
    session = Session(turn([CALL]), turn())
    result, events, effects = drive(session, complete=lambda ctx: True)
    assert result["status"] == "complete"
    assert effects == [CALL]
    history = session.inputs[1]["messages"]
    assert history[1] == turn([CALL]).assistant
    assert history[2]["toolCallId"] == CALL["id"]
    assert history[2]["toolName"] == CALL["name"]
    assert json.loads(history[2]["content"][0]["text"]) == {"status": "completed"}
    assert history[2]["isError"] is False
    assert events[1]["response"]["wire_usage"] is None
    assert CONTEXT["messages"] == [{"role": "user", "content": "Inspect evidence"}]
    assert session.inputs[0]["messages"] == CONTEXT["messages"]


@pytest.mark.parametrize("boundary", ["cancel", "revoke"])
def test_phase_observer_can_prevent_dispatch(boundary):
    state = {"cancelled": False, "authorized": True}

    def phase(call):
        state["cancelled" if boundary == "cancel" else "authorized"] = boundary == "cancel"

    result, _, effects = drive(Session(turn([CALL])), phase=phase,
                               cancelled=lambda: state["cancelled"],
                               authorized=lambda call: state["authorized"])
    assert result["status"] == ("cancelled" if boundary == "cancel" else "authority_denied")
    assert effects == []


@pytest.mark.parametrize("kind", ["model_input", "model_output", "proposal", "tool_result"])
def test_failed_recording_never_continues_or_retries(kind):
    session = Session(turn([CALL]), turn())
    effects = []

    def record(event):
        if event["kind"] == kind:
            raise OSError("journal failed")

    with pytest.raises(OSError, match="journal failed"):
        drive(session, record=record,
              dispatch=lambda call: effects.append(call) or {"status": "completed"})
    assert effects == ([CALL] if kind == "tool_result" else [])
    assert len(session.inputs) == (0 if kind == "model_input" else 1)


@pytest.mark.parametrize("status", ["unknown", "partial", "not-started"])
def test_unresolved_effect_stops_without_retry(status):
    session = Session(turn([CALL]), turn())
    effects = []
    result, events, _ = drive(session, dispatch=lambda call: effects.append(call) or {"status": status})
    assert result["status"] == "unknown"
    assert effects == [CALL]
    assert len(session.inputs) == 1
    assert events[-2]["message"]["isError"] is True
    assert json.loads(events[-2]["message"]["content"][0]["text"])["status"] == status


@pytest.mark.parametrize("calls", [[CALL, CALL], [CALL, {**CALL, "id": "other", "name": "ungranted"}]])
def test_invalid_selection_rejects_entire_turn(calls):
    result, _, effects = drive(Session(turn(calls)))
    assert result["status"] == "invalid_selection"
    assert effects == []


def test_repeated_id_in_later_turn_is_not_reexecuted():
    result, _, effects = drive(Session(turn([CALL]), turn([CALL])))
    assert result["status"] == "invalid_selection"
    assert effects == [CALL]


@pytest.mark.parametrize("terminal,reason", [("error", "toolUse"), ("done", "length"), ("done", "stop")])
def test_failed_or_inconsistent_terminal_cannot_dispatch(terminal, reason):
    result, _, effects = drive(Session(turn([CALL], terminal=terminal, reason=reason)))
    assert result["status"] == "unknown"
    assert effects == []


def test_unproven_completion_preserves_incomplete_obligation():
    result, _, effects = drive(Session(turn()))
    assert result["status"] == "incomplete"
    assert effects == []


def test_call_budget_rejects_whole_selection_before_effect():
    result, _, effects = drive(Session(turn([CALL, {**CALL, "id": "other"}])), max_calls=1)
    assert result["status"] == "call_budget"
    assert effects == []


def test_turn_budget_keeps_last_effect_result():
    result, _, effects = drive(Session(turn([CALL])), max_turns=1)
    assert result["status"] == "turn_budget"
    assert effects == [CALL]
    assert result["context"]["messages"][-1]["toolCallId"] == CALL["id"]


def test_cancellation_after_committed_effect_keeps_it_and_stops_next_call():
    effects = []
    result, events, _ = drive(Session(turn([CALL, {**CALL, "id": "other"}])),
                              dispatch=lambda call: effects.append(call) or {"status": "completed"},
                              cancelled=lambda: bool(effects))
    assert result["status"] == "cancelled"
    assert effects == [CALL]
    assert result["context"]["messages"][-1]["toolCallId"] == CALL["id"]
    assert any(event["kind"] == "tool_result" for event in events)
