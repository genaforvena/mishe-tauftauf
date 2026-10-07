import json

import pytest

from mishe_tauftauf.inference_loop import (
    NativeJournal, NativeJournalError, drive_native, read_native_journal, recover_native,
)
from mishe_tauftauf.inference_worker import WorkerError, WorkerTurn


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


def test_worker_failure_is_durable_unknown_and_never_reopened(tmp_path):
    diagnostic = "original transport diagnostic"
    failure = WorkerError(diagnostic)

    class FailedSession:
        def turn(self, context):
            raise failure

    path = tmp_path / "failed.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError) as caught:
            drive(FailedSession(), record=journal)
    assert caught.value is failure
    recovered = read_native_journal(path)
    assert recovered["model_failure"] == {"type": "WorkerError", "message": diagnostic}
    assert recovered["status"] == "unknown"
    assert recovered["stopped"] is False
    assert recovered["context"] == CONTEXT
    assert recovered["turns"] == recovered["calls"] == 0
    with NativeJournal(tmp_path / "recovery.jsonl") as journal:
        result = recover_native(
            path, open_session=lambda **kw: pytest.fail("failed input replayed"),
            dispatch=lambda call: pytest.fail("unexpected effect"),
            record=journal, phase=lambda call: None, cancelled=lambda: False,
            authorized=lambda call: True, complete=lambda ctx: pytest.fail("false completion"))
    assert result["status"] == "unknown"
    assert read_native_journal(tmp_path / "recovery.jsonl")["model_failure"] == recovered["model_failure"]


def test_failure_recording_error_keeps_pending_input_unknown(tmp_path):
    class FailedSession:
        def turn(self, context):
            raise WorkerError("native provider fetch budget exhausted")

    path = tmp_path / "failed-record.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "model_failure":
                raise OSError("failure journal unavailable")
            journal(event)

        with pytest.raises(OSError, match="failure journal unavailable"):
            drive(FailedSession(), record=record)
    recovered = read_native_journal(path)
    assert recovered["status"] == "unknown"
    assert recovered["model_failure"] is None
    assert recovered["stopped"] is False


@pytest.mark.parametrize("mutation", ["after_failure", "wrong_turn", "missing_message",
                                    "ready_checkpoint"])
def test_invalid_failure_history_cannot_authorize_replay(tmp_path, mutation):
    events = []

    class FailedSession:
        def turn(self, context):
            raise WorkerError("native provider fetch budget exhausted")

    with pytest.raises(WorkerError):
        drive(FailedSession(), record=events.append)
    if mutation == "after_failure":
        events.append({**events[-2], "turn": 0})
    elif mutation == "wrong_turn":
        events[-1]["turn"] = 1
    elif mutation == "missing_message":
        del events[-1]["error"]["message"]
    else:
        events = [events[0]]
        events[0]["state"]["model_failure"] = {"type": "WorkerError", "message": "failed"}
    path = tmp_path / "invalid.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    with pytest.raises(NativeJournalError):
        recover_native(
            path, open_session=lambda **kw: pytest.fail("invalid input replayed"),
            dispatch=lambda call: pytest.fail("invalid effect"), record=lambda event: None,
            phase=lambda call: None, cancelled=lambda: False,
            authorized=lambda call: True, complete=lambda ctx: True)


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


@pytest.mark.parametrize("boundary", ["model_input", "model_output", "proposal", "tool_result"])
def test_reconstruction_preserves_interrupted_context_and_uncertainty(tmp_path, boundary):
    path = tmp_path / "caller.jsonl"
    effects = []
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == boundary:
                raise InterruptedError(boundary)

        with pytest.raises(InterruptedError):
            drive(Session(turn([CALL])), record=record,
                  dispatch=lambda call: effects.append(call) or {"status": "completed"})
    recovered = read_native_journal(path)
    assert recovered["obligation"] == {"source": "owned-event"}
    assert recovered["status"] == ("ready" if boundary == "tool_result" else "unknown")
    assert recovered["pending_calls"] == ([CALL] if boundary in ("model_output", "proposal") else [])
    assert effects == ([CALL] if boundary == "tool_result" else [])
    if boundary == "tool_result":
        _, baseline, _ = drive(Session(turn([CALL]), turn()))
        expected = next(row["context"] for row in baseline
                        if row["kind"] == "model_input" and row["turn"] == 1)
        assert recovered["context"] == expected
        assert recovered["used_ids"] == [CALL["id"]]
        assert (recovered["turns"], recovered["calls"]) == (1, 1)
    else:
        assert recovered["calls"] == 0


def test_unproposed_second_call_remains_unresolved(tmp_path):
    second = {**CALL, "id": "second|native"}
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError

        with pytest.raises(InterruptedError):
            drive(Session(turn([CALL, second])), record=record)
    recovered = read_native_journal(path)
    assert recovered["status"] == "unknown"
    assert recovered["pending_calls"] == [second]
    assert recovered["used_ids"] == sorted([CALL["id"], second["id"]])


def test_lost_dispatch_result_never_becomes_absence(tmp_path):
    path = tmp_path / "caller.jsonl"
    effects = []
    def dispatch(call):
        effects.append(call)
        raise InterruptedError("receipt lost")

    with NativeJournal(path) as journal, pytest.raises(InterruptedError):
        drive(Session(turn([CALL])), record=journal, dispatch=dispatch)
    recovered = read_native_journal(path)
    assert effects == [CALL]
    assert recovered["status"] == "unknown"
    assert recovered["pending_calls"] == [CALL]
    assert recovered["calls"] == 0


@pytest.mark.parametrize("mutation", ["torn", "wrong_id", "changed_context", "after_stop", "changed_obligation"])
def test_damaged_history_cannot_authorize_continuation(tmp_path, mutation):
    _, events, _ = drive(Session(turn([CALL]), turn()), complete=lambda ctx: True)
    if mutation == "wrong_id":
        next(row for row in events if row["kind"] == "tool_result")["message"]["toolCallId"] = "wrong"
    elif mutation == "changed_context":
        next(row for row in events if row["kind"] == "model_input" and row["turn"] == 1)["context"]["messages"].pop()
    elif mutation == "after_stop":
        events.append(events[0])
    elif mutation == "changed_obligation":
        events[1]["obligation"] = {"source": "different-event"}
    path = tmp_path / "caller.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in events)
                    + ('{"kind":' if mutation == "torn" else ""))
    before = path.read_bytes()
    with pytest.raises(NativeJournalError):
        read_native_journal(path)
    assert path.read_bytes() == before


def test_exclusive_writer_does_not_overwrite_existing_history(tmp_path):
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        drive(Session(turn()), record=journal, complete=lambda ctx: True)
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            NativeJournal(path)
        assert path.read_bytes() == before
    recovered = read_native_journal(path)
    assert recovered["stopped"] is True
    assert recovered["status"] == "complete"
    assert recovered["context"]["messages"][-1] == turn().assistant


def test_failed_fsync_poisoned_writer_cannot_continue(tmp_path, monkeypatch):
    path = tmp_path / "caller.jsonl"
    def fail(fd):
        raise OSError("fsync failed")

    with NativeJournal(path) as journal:
        monkeypatch.setattr("mishe_tauftauf.inference_loop.os.fsync", fail)
        with pytest.raises(OSError, match="fsync failed"):
            drive(Session(turn([CALL])), record=journal)
        before = path.read_bytes()
        with pytest.raises(NativeJournalError, match="unavailable"):
            journal({"kind": "proposal"})
        assert path.read_bytes() == before


def test_proposal_journal_failure_prevents_dispatch(tmp_path):
    path = tmp_path / "proposal-failure.jsonl"
    session = Session(turn([CALL]))
    dispatched = []
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "proposal":
                raise OSError("proposal journal unavailable")
            journal(event)

        with pytest.raises(OSError, match="proposal journal unavailable"):
            drive_native(session, CONTEXT, obligation={"source": "owned-event"},
                         max_turns=3, max_calls=2, record=record,
                         dispatch=lambda call: dispatched.append(call) or {
                             "status": "completed"},
                         phase=lambda call: None, cancelled=lambda: False,
                         authorized=lambda call: True, complete=lambda ctx: False)
    assert session.inputs == [CONTEXT]
    assert dispatched == []
    assert all(row["kind"] != "proposal"
               for row in map(json.loads, path.read_text().splitlines()))


def interrupted_journal(tmp_path, *, max_turns=3, max_calls=2, boundary="tool_result",
                        calls=(CALL,), outcome="completed"):
    path = tmp_path / "original.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == boundary:
                raise InterruptedError

        with pytest.raises(InterruptedError):
            drive(Session(turn(calls)), record=record, max_turns=max_turns,
                  max_calls=max_calls, dispatch=lambda call: {"status": outcome})
    return path


def continue_journal(path, session, record, **overrides):
    state = read_native_journal(path)
    effects = []
    options = dict(obligation=state["obligation"],
                   max_turns=state["budgets"]["turns"],
                   max_calls=state["budgets"]["calls"], resume_from=path,
                   dispatch=lambda call: effects.append(call) or {"status": "completed"},
                   record=record, phase=lambda call: None,
                   cancelled=lambda: False, authorized=lambda call: True,
                   complete=lambda context: True)
    options.update(overrides)
    result = drive_native(session, state["context"], **options)
    return result, effects


@pytest.mark.parametrize("arm,status", [
    ("reused_id", "invalid_selection"), ("call_budget", "call_budget"),
    ("turn_budget", "turn_budget"),
])
def test_continuation_preserves_decision_changing_lifetime_limits(tmp_path, arm, status):
    path = interrupted_journal(tmp_path, max_turns=1 if arm == "turn_budget" else 3)
    before = path.read_bytes()
    calls = ([CALL] if arm == "reused_id" else
             [{**CALL, "id": "new-a"}, {**CALL, "id": "new-b"}])
    session = Session(turn(calls))
    with NativeJournal(tmp_path / "continuation.jsonl") as journal:
        result, effects = continue_journal(path, session, journal)
    assert result["status"] == status
    assert effects == []
    assert result["calls"] == 1
    assert result["turns"] == (1 if arm == "turn_budget" else 2)
    assert session.inputs == ([] if arm == "turn_budget" else
                              [read_native_journal(path)["context"]])
    assert path.read_bytes() == before
    continued = read_native_journal(tmp_path / "continuation.jsonl")
    assert continued["status"] == status
    assert continued["used_ids"] == [CALL["id"]]
    assert continued["budgets"] == {"turns": 1 if arm == "turn_budget" else 3, "calls": 2}


@pytest.mark.parametrize("boundary,outcome,calls", [
    ("model_input", "completed", [CALL]),
    ("model_output", "completed", [CALL]),
    ("proposal", "completed", [CALL]),
    ("tool_result", "unknown", [CALL]),
    ("tool_result", "partial", [CALL]),
    ("tool_result", "completed", [CALL, {**CALL, "id": "pending"}]),
])
def test_unresolved_continuation_hands_off_without_provider_or_dispatch(
        tmp_path, boundary, outcome, calls):
    path = interrupted_journal(tmp_path, boundary=boundary, outcome=outcome, calls=calls)
    original = read_native_journal(path)
    session = Session()
    with NativeJournal(tmp_path / "handoff.jsonl") as journal:
        result, effects = continue_journal(path, session, journal)
    assert result["status"] == "unknown"
    assert result["pending_calls"] == original["pending_calls"]
    assert result["used_ids"] == original["used_ids"]
    assert session.inputs == []
    assert effects == []
    handoff = read_native_journal(tmp_path / "handoff.jsonl")
    assert handoff["pending_calls"] == original["pending_calls"]
    assert handoff["context"] == original["context"]
    assert handoff["stopped"] is True


def test_second_interruption_retains_lifetime_history_and_exact_next_input(tmp_path):
    path = interrupted_journal(tmp_path)
    second = tmp_path / "second.jsonl"
    call = {**CALL, "id": "second"}
    with NativeJournal(second) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        with pytest.raises(InterruptedError):
            continue_journal(path, Session(turn([call])), record)
    reconstructed = read_native_journal(second)
    assert reconstructed["status"] == "ready"
    assert reconstructed["used_ids"] == sorted([CALL["id"], "second"])
    assert (reconstructed["turns"], reconstructed["calls"]) == (2, 2)
    session = Session(turn())
    with NativeJournal(tmp_path / "third.jsonl") as journal:
        result, effects = continue_journal(second, session, journal)
    assert result["status"] == "complete"
    assert (result["turns"], result["calls"]) == (3, 2)
    assert effects == []
    assert session.inputs == [reconstructed["context"]]
    assert session.inputs[0]["messages"][-1]["toolCallId"] == "second"
    assert session.inputs[0]["messages"][1]["providerPayload"] == {"opaque": "retained"}


def test_continuation_rejects_budget_increase_before_record_or_provider(tmp_path):
    path = interrupted_journal(tmp_path)
    events = []
    session = Session()
    with pytest.raises(NativeJournalError, match="original lifetime budgets"):
        continue_journal(path, session, events.append, max_calls=3)
    assert events == []
    assert session.inputs == []


@pytest.mark.parametrize("change", ["obligation", "context", "stopped", "legacy"])
def test_noncontinuable_history_refuses_before_callbacks(tmp_path, change):
    path = interrupted_journal(tmp_path)
    if change == "stopped":
        events = []
        drive(Session(turn()), record=events.append, complete=lambda context: True)
        path.write_text("".join(json.dumps(row) + "\n" for row in events))
    elif change == "legacy":
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows[1:]))
    state = read_native_journal(path)
    original = path.read_bytes()
    context = state["context"]
    obligation = state["obligation"]
    if change == "context":
        context["tools"].append({"name": "new-authority"})
    elif change == "obligation":
        obligation = {"source": "different"}
    session = Session()
    events = []
    with pytest.raises(NativeJournalError):
        drive_native(session, context, obligation=obligation, max_turns=3,
                     max_calls=2, resume_from=path, record=events.append,
                     dispatch=lambda call: pytest.fail("unexpected dispatch"),
                     phase=lambda call: pytest.fail("unexpected phase"),
                     cancelled=lambda: pytest.fail("unexpected cancellation check"),
                     authorized=lambda call: pytest.fail("unexpected authority check"),
                     complete=lambda context: pytest.fail("unexpected completion check"))
    assert events == []
    assert session.inputs == []
    assert path.read_bytes() == original


def checkpoint_journal(tmp_path, **options):
    from mishe_tauftauf.inference_transport import NativeTurn
    response = turn([CALL])
    native = NativeTurn(response.terminal, response.assistant, None, None,
                        {"sessionId": "recorded-session", "boundaryDigest": "recorded-boundary"})
    path = tmp_path / "checkpoint.jsonl"
    boundary = options.pop("boundary", "tool_result")
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == boundary:
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record, **options)
    return path


def recovery_options(record):
    return dict(record=record, dispatch=lambda call: pytest.fail("redispatch"),
                phase=lambda call: pytest.fail("new proposal"),
                cancelled=lambda: False, authorized=lambda call: True,
                complete=lambda context: True)


def test_recovery_selects_exact_recorded_checkpoint_results_and_budgets(tmp_path):
    from contextlib import contextmanager
    path = checkpoint_journal(tmp_path)
    original = path.read_bytes()
    state = read_native_journal(path)
    session = Session(turn())
    opened, closed = [], []
    @contextmanager
    def open_session(*, checkpoint):
        opened.append(checkpoint)
        try:
            yield session
        finally:
            closed.append(True)
    events = []
    result = recover_native(path, open_session=open_session, **recovery_options(events.append))
    assert opened == [state["provider_checkpoint"]]
    assert session.inputs == [state["context"]]
    assert json.loads(session.inputs[0]["messages"][-1]["content"][0]["text"]) == {
        "status": "completed"}
    assert result["status"] == "complete"
    assert (result["turns"], result["calls"]) == (2, 1)
    assert events[0]["state"]["budgets"] == {"turns": 3, "calls": 2}
    assert closed == [True]
    assert path.read_bytes() == original


@pytest.mark.parametrize("boundary,outcome", [
    ("model_input", "completed"), ("model_output", "completed"),
    ("proposal", "completed"), ("tool_result", "unknown"), ("tool_result", "partial"),
])
def test_recovery_unknown_never_constructs_provider(tmp_path, boundary, outcome):
    path = checkpoint_journal(tmp_path, boundary=boundary,
                              dispatch=lambda call: {"status": outcome})
    result = recover_native(path, open_session=lambda **kw: pytest.fail("provider opened"),
                            **recovery_options(lambda event: None))
    assert result["status"] == "unknown"
    assert result["pending_calls"] == read_native_journal(path)["pending_calls"]


def test_recovery_requires_checkpoint_from_same_output(tmp_path):
    path = interrupted_journal(tmp_path)
    with pytest.raises(NativeJournalError, match="missing recorded provider checkpoint"):
        recover_native(path, open_session=lambda **kw: pytest.fail("provider opened"),
                       **recovery_options(lambda event: pytest.fail("recorded")))


def test_recovery_seed_preserves_checkpoint_when_interrupted_before_next_input(tmp_path):
    path = checkpoint_journal(tmp_path)
    second = tmp_path / "second.jsonl"
    from contextlib import nullcontext
    with NativeJournal(second) as journal:
        def record(event):
            journal(event)
            raise InterruptedError
        with pytest.raises(InterruptedError):
            recover_native(path, open_session=lambda **kw: nullcontext(Session()),
                           **recovery_options(record))
    assert read_native_journal(second)["provider_checkpoint"] == (
        read_native_journal(path)["provider_checkpoint"])
    session = Session(turn())
    result = recover_native(second, open_session=lambda **kw: nullcontext(session),
                            **recovery_options(lambda event: None))
    assert result["status"] == "complete"
    assert session.inputs == [read_native_journal(path)["context"]]


def test_recovery_exhausted_budget_never_constructs_provider(tmp_path):
    path = checkpoint_journal(tmp_path, max_turns=1)
    result = recover_native(path, open_session=lambda **kw: pytest.fail("provider opened"),
                            **recovery_options(lambda event: None))
    assert result["status"] == "turn_budget"
    assert (result["turns"], result["calls"]) == (1, 1)

