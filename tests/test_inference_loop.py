import hashlib
import json
from pathlib import Path

import pytest

from mishe_tauftauf.inference_loop import (
    NativeJournal, NativeJournalError, drive_native, read_native_journal,
    recover_native,
)
from mishe_tauftauf.inference_transport import NativeTurn
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


def drive(session, *, context=CONTEXT, **overrides):
    events, effects = [], []
    options = dict(obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                   dispatch=lambda call: effects.append(call) or {"status": "completed"},
                   record=events.append, phase=lambda call: None,
                   cancelled=lambda: False, authorized=lambda call: True,
                   complete=lambda context: False)
    options.update(overrides)
    result = drive_native(session, context, **options)
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


def test_turn_policy_rejects_calls_outside_current_offer(tmp_path):
    session = Session(turn([CALL]))
    result, events, effects = drive(session, before_turn=lambda state: {'tools': []})
    assert result['status'] == 'invalid_selection'
    assert effects == []
    assert session.inputs[0]['tools'] == []
    path = tmp_path / 'subset.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))
    recovered = read_native_journal(path)
    assert recovered['status'] == 'invalid_selection'
    assert recovered['used_ids'] == []


def test_reoffering_tools_does_not_restore_used_call_ids():
    context = {**CONTEXT, 'tools': [{'name': 'inspect'}, {'name': 'search'}]}
    second_call = {**CALL, 'id': 'search-id', 'name': 'search'}
    session = Session(turn([CALL]), turn([second_call]), turn([CALL]))
    offers = [['inspect'], ['search'], ['inspect']]
    result, _, effects = drive(
        session, context=context, max_calls=3,
        before_turn=lambda state: {'tools': offers[state['turn']]})
    assert result['status'] == 'invalid_selection'
    assert effects == [CALL, second_call]
    assert [ctx['tools'] for ctx in session.inputs] == [
        [{'name': 'inspect'}], [{'name': 'search'}], [{'name': 'inspect'}]]


@pytest.mark.parametrize('decision', [
    {'tools': ['ungranted']}, {'tools': ['inspect', 'inspect']},
    {'tools': [{'name': 'inspect'}]}, {'tools': ['inspect'], 'notice': {'role': 'system'}},
    {'tools': ['inspect'], 'context': {'systemPrompt': 'replacement'}},
])
def test_turn_policy_cannot_expand_grant_or_rewrite_context(decision):
    session = Session(turn())
    with pytest.raises(ValueError):
        drive(session, before_turn=lambda state: decision)
    assert session.inputs == []


def test_turn_policy_notice_history_and_original_grant_survive_recovery(tmp_path):
    from contextlib import nullcontext

    context = {**CONTEXT, 'systemPrompt': 'Fixed authority.',
               'tools': [{'name': 'inspect', 'parameters': {'type': 'object'}},
                         {'name': 'search', 'parameters': {'type': 'object'}}]}
    notice = 'Observed evidence only: λ\nNo change to authority.'
    second_call = {**CALL, 'id': 'second-id', 'name': 'search'}
    first = NativeTurn('done', turn([CALL]).assistant, None, None,
                       {'sessionId': 'recorded', 'boundaryDigest': 'boundary'})
    states = []

    def before_turn(state):
        states.append(json.loads(json.dumps(state)))
        # A caller cannot smuggle a history/system edit through its snapshot.
        state['context']['messages'][0]['content'] = 'rewritten'
        state['context']['systemPrompt'] = 'rewritten'
        return {'tools': (['inspect'] if state['turn'] == 0 else
                          ['inspect', 'search'] if state['turn'] == 1 else []),
                'notice': notice + str(state['turn'])}

    baseline = Session(first, turn([second_call]), turn())
    expected, baseline_events, baseline_effects = drive(
        baseline, context=context, before_turn=before_turn, complete=lambda ctx: True)
    assert expected['status'] == 'complete'
    assert baseline_effects == [CALL, second_call]
    assert [(s['turn'], s['calls'], s['remaining_turns'], s['remaining_calls'])
            for s in states] == [(0, 0, 3, 2), (1, 1, 2, 1), (2, 2, 1, 0)]
    assert states[1]['context']['messages'] == baseline.inputs[1]['messages'][:-1]
    assert states[1]['context']['tools'] == [context['tools'][0]]
    for index, model_input in enumerate(baseline.inputs):
        assert model_input['systemPrompt'] == context['systemPrompt']
        assert model_input['messages'][0] == context['messages'][0]
        assert model_input['messages'][-1] == {
            'role': 'user', 'content': [{'type': 'text', 'text': notice + str(index)}],
            'timestamp': 0}
    decisions = [row for row in baseline_events if row['kind'] == 'turn_policy']
    assert [row['decision']['tools'] for row in decisions] == [
        ['inspect'], ['inspect', 'search'], []]
    assert [row['decision']['notice'] for row in decisions] == [
        notice + str(index) for index in range(3)]
    assert [row['context'] for row in baseline_events if row['kind'] == 'model_input'] == (
        baseline.inputs)
    full = tmp_path / 'full.jsonl'
    full.write_text(''.join(json.dumps(row) + '\n' for row in baseline_events))
    assert read_native_journal(full)['context'] == expected['context']

    original = tmp_path / 'original.jsonl'
    with NativeJournal(original) as journal:
        def record(event):
            journal(event)
            if event['kind'] == 'tool_result':
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(Session(first), context=context, record=record, before_turn=before_turn)
    recovered = read_native_journal(original)
    assert recovered['context']['tools'] == [context['tools'][0]]
    assert recovered['original_tools'] == context['tools']
    assert recovered['context']['messages'] == baseline.inputs[1]['messages'][:-1]

    # A second interruption at the continuation checkpoint must not collapse
    # the original grant to the currently offered inspect-only subset.
    handoff = tmp_path / 'handoff.jsonl'
    with NativeJournal(handoff) as journal:
        def record_checkpoint(event):
            journal(event)
            raise InterruptedError
        with pytest.raises(InterruptedError):
            recover_native(original, open_session=lambda **kw: nullcontext(Session()),
                           before_turn=before_turn, **recovery_options(record_checkpoint))
    assert read_native_journal(handoff)['original_tools'] == context['tools']
    resumed = Session(turn([second_call]), turn())
    resumed_effects = []
    continuation = tmp_path / 'continuation.jsonl'
    with NativeJournal(continuation) as journal:
        actual = recover_native(
            handoff, open_session=lambda **kw: nullcontext(resumed),
            before_turn=before_turn, record=journal, phase=lambda call: None,
            cancelled=lambda: False, authorized=lambda call: True,
            complete=lambda ctx: True,
            dispatch=lambda call: resumed_effects.append(call) or {'status': 'completed'})
    assert resumed.inputs == baseline.inputs[1:]
    assert resumed_effects == [second_call]
    assert actual == expected
    assert read_native_journal(continuation)['context'] == actual['context']


def test_failed_turn_policy_record_never_dispatches_provider():
    session = Session(turn())

    def record(event):
        if event['kind'] == 'turn_policy':
            raise OSError('policy journal failed')

    with pytest.raises(OSError, match='policy journal failed'):
        drive(session, record=record,
              before_turn=lambda state: {'tools': [], 'notice': 'fresh observation'})
    assert session.inputs == []


def test_interrupted_turn_policy_is_unknown_and_never_reinvoked(tmp_path):
    path = tmp_path / 'policy.jsonl'
    session = Session(turn())
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event['kind'] == 'turn_policy':
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(session, record=record,
                  before_turn=lambda state: {'tools': [], 'notice': 'fresh observation'})
    assert session.inputs == []
    recovered = read_native_journal(path)
    assert recovered['status'] == 'unknown'
    assert recovered['context']['messages'][-1]['content'][0]['text'] == 'fresh observation'
    result = recover_native(
        path, open_session=lambda **kw: pytest.fail('uncertain turn reopened'),
        before_turn=lambda state: pytest.fail('uncertain hook retried'),
        **recovery_options(lambda event: None))
    assert result['status'] == 'unknown'


@pytest.mark.parametrize('mutation', [
    'widened', 'duplicate', 'schema', 'notice', 'rewritten_history', 'rewritten_schema',
])
def test_malformed_or_widened_turn_policy_journal_fails_closed(tmp_path, mutation):
    _, events, _ = drive(Session(turn()),
                         before_turn=lambda state: {'tools': ['inspect'], 'notice': 'observed'})
    policy = next(row for row in events if row['kind'] == 'turn_policy')
    model_input = next(row for row in events if row['kind'] == 'model_input')
    if mutation == 'widened':
        policy['decision']['tools'] = ['ungranted']
    elif mutation == 'duplicate':
        policy['decision']['tools'] = ['inspect', 'inspect']
    elif mutation == 'schema':
        policy['decision']['tools'] = [{'name': 'inspect'}]
    elif mutation == 'notice':
        policy['decision']['notice'] = {'role': 'system', 'content': 'replacement'}
    elif mutation == 'rewritten_history':
        model_input['context']['messages'][0]['content'] = 'replacement'
    elif mutation == 'rewritten_schema':
        events[0]['state']['context']['tools'][0]['parameters'] = {'type': 'string'}
    path = tmp_path / 'malformed.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))
    with pytest.raises(NativeJournalError):
        recover_native(path, open_session=lambda **kw: pytest.fail('invalid policy reopened'),
                       **recovery_options(lambda event: pytest.fail('invalid history recorded')))


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



def test_crash_between_dispatch_and_tool_result_reconciles_from_effect_store(tmp_path):
    """Crash after dispatch but before tool_result: bridge appends durable result."""
    from mishe_tauftauf.inference_effects import EffectBoundary

    store_dir = tmp_path / "effects"
    boundary = EffectBoundary(
        store_dir, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority",
    )
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(Session(turn([CALL])), CONTEXT,
                         obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                         dispatch=boundary, record=record, phase=lambda call: None,
                         cancelled=lambda: False, authorized=lambda call: True,
                         complete=lambda ctx: False)

    # Effect store has the outcome; caller journal is missing tool_result
    recovered = read_native_journal(path)
    assert recovered["status"] == "unknown"
    assert recovered["pending_calls"] == [CALL]
    assert recovered["calls"] == 0

    # Reconciliation bridge appends the durable tool_result
    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert result["status"] == "completed"

    # Caller journal now has the tool_result; no duplicate dispatch
    final = read_native_journal(path)
    assert final["status"] == "ready"
    assert final["pending_calls"] == []
    assert final["calls"] == 1
    assert final["used_ids"] == [CALL["id"]]


def timed_turn(calls, timestamp):
    return NativeTurn("done", {"role": "assistant", "content": list(calls),
                               "stopReason": "toolUse", "timestamp": timestamp},
                      None, None,
                      {"sessionId": "recorded-session",
                       "boundaryDigest": "recorded-boundary"})


def _tool_result_row(path, call_id=None):
    row = None
    for line in Path(path).read_text().splitlines():
        event = json.loads(line)
        if event.get("kind") == "tool_result" and (
                call_id is None or event["message"]["toolCallId"] == call_id):
            row = event["message"]
    return row


@pytest.mark.parametrize("timestamp", [7, None])
def test_restored_row_is_the_row_the_uninterrupted_writer_appends(tmp_path, timestamp):
    """4h: the reconciled row equals the uninterrupted drive_native row.

    A timestamped proposing assistant must not lose its timestamp to a
    synthesized 0 on the recovery path, and an untimestamped assistant must
    stay byte-identical to today's row.
    """
    from mishe_tauftauf.inference_effects import EffectBoundary
    from mishe_tauftauf.inference_loop import reconcile_caller_journal

    def assistant():
        message = {"role": "assistant", "content": [CALL],
                   "stopReason": "toolUse"}
        if timestamp is not None:
            message["timestamp"] = timestamp
        return WorkerTurn("done", message, "toolUse")

    def run(name, crash):
        store = tmp_path / f"{name}-effects"
        boundary = EffectBoundary(
            store, writer_id="loop-1", obligation={"source": "owned-event"},
            execute=lambda call: {"marker": "done"}, authority="test:authority")
        path = tmp_path / f"{name}.jsonl"
        with NativeJournal(path) as journal:
            def record(event):
                if crash and event["kind"] == "tool_result":
                    raise InterruptedError("crash before tool_result persisted")
                journal(event)

            if crash:
                with pytest.raises(InterruptedError):
                    drive(Session(assistant()), record=record, dispatch=boundary)
            else:
                drive(Session(assistant(), turn()), record=record, dispatch=boundary)
        return path, store

    whole_path, _ = run("whole", crash=False)
    expected = _tool_result_row(whole_path)
    crashed_path, crashed_store = run("crashed", crash=True)
    result = reconcile_caller_journal(
        crashed_path, crashed_store, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True)

    assert result["status"] == "completed"
    restored = _tool_result_row(crashed_path)
    assert restored == expected
    assert restored["timestamp"] == (timestamp if timestamp is not None else 0)


def test_continued_model_input_carries_the_proposing_assistant_timestamp(tmp_path):
    """4h: the consumer-visible part — the next model input after recovery
    holds the restored row with the proposing assistant's recorded timestamp.
    """
    from contextlib import nullcontext
    from mishe_tauftauf.inference_effects import EffectBoundary
    from mishe_tauftauf.inference_loop import (reconcile_caller_journal,
                                               recover_native)
    second = {**CALL, "id": "second|native"}
    store = tmp_path / "effects"
    boundary = EffectBoundary(
        store, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority")
    path = tmp_path / "crashed.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if (event["kind"] == "tool_result"
                    and event["message"]["toolCallId"] == second["id"]):
                raise InterruptedError("crash before the second result")
            journal(event)

        with pytest.raises(InterruptedError):
            drive(Session(timed_turn([CALL], 1), timed_turn([second], 7)),
                  record=record, dispatch=boundary)

    reconciled = reconcile_caller_journal(
        path, store, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True)
    assert reconciled == {"status": "completed", "reconciled": [second["id"]]}

    session = Session(turn())
    continued = tmp_path / "continued.jsonl"
    with NativeJournal(continued) as journal:
        result = recover_native(
            path, open_session=lambda **kwargs: nullcontext(session),
            **recovery_options(journal))

    restored = session.inputs[0]["messages"][-1]
    assert restored["toolCallId"] == second["id"]
    assert restored["timestamp"] == 7
    assert result["status"] == "complete"


def test_multi_call_crash_restores_pending_call_timestamp(tmp_path):
    """4h: mid-selection crash — the last recorded message is the sibling
    call's toolResult row; the pending call still restores its proposing
    assistant's timestamp (by explicit correlation, not by position).
    """
    from mishe_tauftauf.inference_effects import EffectBoundary
    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    second = {**CALL, "id": "second|native"}
    store = tmp_path / "effects"
    boundary = EffectBoundary(
        store, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority")
    path = tmp_path / "crashed.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if (event["kind"] == "tool_result"
                    and event["message"]["toolCallId"] == second["id"]):
                raise InterruptedError("crash before the second result")
            journal(event)

        with pytest.raises(InterruptedError):
            drive(Session(timed_turn([CALL, second], 7)), record=record,
                  dispatch=boundary)

    result = reconcile_caller_journal(
        path, store, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True)

    assert result["status"] == "completed"
    assert read_native_journal(path)["pending_calls"] == []
    assert _tool_result_row(path, second["id"])["timestamp"] == 7


def test_recovered_effect_reaches_fresh_model_turn_without_redispatch(tmp_path):
    """Recovered input reaches a new session; completion is forced by its test predicate."""
    from contextlib import nullcontext
    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    from mishe_tauftauf.inference_effects import EffectBoundary
    from mishe_tauftauf.inference_transport import NativeTurn

    store_dir = tmp_path / "effects"
    boundary = EffectBoundary(
        store_dir, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority",
    )
    response = turn([CALL])
    native = NativeTurn(
        response.terminal, response.assistant, None, None,
        {"sessionId": "recorded-session", "boundaryDigest": "recorded-boundary"},
    )
    path = tmp_path / "interrupted.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(
                Session(native), CONTEXT, obligation={"source": "owned-event"},
                max_turns=3, max_calls=2, dispatch=boundary, record=record,
                phase=lambda call: None, cancelled=lambda: False,
                authorized=lambda call: True, complete=lambda context: False,
            )

    reconciled = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert reconciled == {"status": "completed", "reconciled": [CALL["id"]]}

    session = Session(turn())
    continued = tmp_path / "continued.jsonl"
    with NativeJournal(continued) as journal:
        result = recover_native(
            path, open_session=lambda **kwargs: nullcontext(session),
            **recovery_options(journal),
        )

    tool_result = session.inputs[0]["messages"][-1]
    assert tool_result["toolCallId"] == CALL["id"]
    assert json.loads(tool_result["content"][0]["text"])["result"] == {"marker": "done"}
    assert result["status"] == "complete"
    assert read_native_journal(continued)["status"] == "complete"


def test_reconcile_never_dispatches_for_unknown_outcome(tmp_path):
    """Unknown outcome in effect store: bridge leaves call pending, no dispatch."""
    from mishe_tauftauf.inference_effects import EffectBoundary

    store_dir = tmp_path / "effects"
    boundary = EffectBoundary(
        store_dir, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority",
    )
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(Session(turn([CALL])), CONTEXT,
                         obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                         dispatch=lambda call: {"status": "unknown"},
                         record=record, phase=lambda call: None,
                         cancelled=lambda: False, authorized=lambda call: True,
                         complete=lambda ctx: False)

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert result["status"] == "unknown"

    final = read_native_journal(path)
    assert final["status"] == "unknown"
    assert final["pending_calls"] == [CALL]
    assert final["calls"] == 0


def test_reconcile_rejects_revoked_authority(tmp_path):
    """Completed outcome but authority revoked: bridge leaves call pending."""
    from mishe_tauftauf.inference_effects import EffectBoundary

    store_dir = tmp_path / "effects"
    boundary = EffectBoundary(
        store_dir, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority",
    )
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(Session(turn([CALL])), CONTEXT,
                         obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                         dispatch=boundary, record=record, phase=lambda call: None,
                         cancelled=lambda: False, authorized=lambda call: True,
                         complete=lambda ctx: False)

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: False,
    )
    assert result["status"] == "authority_denied"

    final = read_native_journal(path)
    assert final["status"] == "unknown"
    assert final["pending_calls"] == [CALL]
    assert final["calls"] == 0


def test_reconcile_no_effect_store_leaves_pending(tmp_path):
    """No effect store: bridge returns unknown, no dispatch."""
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(Session(turn([CALL])), CONTEXT,
                         obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                         dispatch=lambda call: {"status": "completed"},
                         record=record, phase=lambda call: None,
                         cancelled=lambda: False, authorized=lambda call: True,
                         complete=lambda ctx: False)

    store_dir = tmp_path / "nonexistent"
    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert result["status"] == "unknown"

    final = read_native_journal(path)
    assert final["status"] == "unknown"
    assert final["pending_calls"] == [CALL]
    assert final["calls"] == 0

def test_reconcile_uses_journal_obligation_not_caller_obligation(tmp_path):
    """Caller passes a different obligation: bridge uses the journal's own."""
    from mishe_tauftauf.inference_effects import EffectBoundary

    store_dir = tmp_path / "effects"
    boundary = EffectBoundary(
        store_dir, writer_id="loop-1", obligation={"source": "owned-event"},
        execute=lambda call: {"marker": "done"}, authority="test:authority",
    )
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            if event["kind"] == "tool_result":
                raise InterruptedError("crash before tool_result persisted")
            journal(event)

        with pytest.raises(InterruptedError):
            drive_native(Session(turn([CALL])), CONTEXT,
                         obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                         dispatch=boundary, record=record, phase=lambda call: None,
                         cancelled=lambda: False, authorized=lambda call: True,
                         complete=lambda ctx: False)

    # Caller passes a DIFFERENT obligation; bridge must use the journal's
    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert result["status"] == "completed"

    # Journal remains readable and carries its own obligation
    final = read_native_journal(path)
    assert final["status"] == "ready"
    assert final["obligation"] == {"source": "owned-event"}
    assert final["pending_calls"] == []
    assert final["calls"] == 1


def test_reconcile_stopped_journal_leaves_pending(tmp_path):
    """Stopped journal with pending calls: bridge returns unknown, appends nothing."""
    path = tmp_path / "caller.jsonl"
    with NativeJournal(path) as journal:
        drive_native(Session(turn([CALL])), CONTEXT,
                     obligation={"source": "owned-event"}, max_turns=3, max_calls=2,
                     dispatch=lambda call: {"status": "unknown"},
                     record=journal, phase=lambda call: None,
                     cancelled=lambda: False, authorized=lambda call: False,
                     complete=lambda ctx: False)

    state = read_native_journal(path)
    assert state["stopped"] is True
    assert state["pending_calls"] == [CALL]

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, tmp_path / "nonexistent", writer_id="loop-1",
        authority="test:authority", authorized=lambda call: True,
    )
    assert result["status"] == "unknown"
    assert result["reconciled"] == []

    # Journal unchanged: still stopped, still pending, no tool_result appended
    final = read_native_journal(path)
    assert final["stopped"] is True
    assert final["pending_calls"] == [CALL]
    assert final["calls"] == 0

def test_real_subprocess_crash_during_execute_reconciles_without_redispatch(tmp_path):
    """A real process killed during execute (after start, before outcome):
    reconcile_caller_journal reports unknown and never re-dispatches.

    Bridges the gap between the synthetic in-process exception injection and the
    throwaway live canary: real subprocess death through `drive_native` +
    `EffectBoundary` + `reconcile_caller_journal` in a permanent test.
    """
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    store = Path(__file__).resolve().parent.parent / "src"
    journal_path = tmp_path / "caller.jsonl"
    store_dir = tmp_path / "effects"
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        sys.path.insert(0, %r)
        from mishe_tauftauf.inference_effects import EffectBoundary
        from mishe_tauftauf.inference_loop import NativeJournal, drive_native
        from mishe_tauftauf.inference_worker import WorkerTurn

        call = %r
        context = %r

        class Session:
            def turn(self, context):
                return WorkerTurn("done", {"role": "assistant",
                    "content": [call], "stopReason": "toolUse"}, None)

        def execute(ledger_call):
            print(json.dumps({"started": True}), flush=True)
            time.sleep(600)

        dispatch = EffectBoundary(Path(%r), writer_id="loop-1",
            obligation={"source": "owned-event"}, execute=execute,
            authority="test:authority")
        with NativeJournal(Path(%r)) as journal:
            drive_native(Session(), context, obligation={"source": "owned-event"},
                max_turns=2, max_calls=1, dispatch=dispatch, record=journal,
                phase=lambda call: None, cancelled=lambda: False,
                authorized=lambda call: True, complete=lambda context: False)
        """
        % (str(store), CALL, CONTEXT, str(store_dir), str(journal_path))))
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline()
        if not line:
            raise AssertionError(
                f"child produced no receipt; stderr:\n{child.stderr.read()}")
        assert json.loads(line) == {"started": True}
    finally:
        child.kill()
        child.wait(timeout=10)

    # The caller journal holds the proposal but no tool_result: the call is
    # still pending, its effect status UNKNOWN.
    state = read_native_journal(journal_path)
    assert state["stopped"] is False
    assert state["status"] == "unknown"
    assert state["pending_calls"] == [CALL]
    assert state["calls"] == 0

    # The effect store has an intent and a start record but no outcome.
    assert (store_dir / "intents.jsonl").read_text().strip() != ""
    assert (store_dir / "starts.jsonl").read_text().strip() != ""
    assert (store_dir / "outcomes.jsonl").read_text().strip() == ""

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    # Reconciliation reports unknown and appends nothing: no re-dispatch, no
    # provider session, no second execution.
    result = reconcile_caller_journal(
        journal_path, store_dir, writer_id="loop-1", authority="test:authority",
        authorized=lambda call: True,
    )
    assert result == {"status": "unknown", "reconciled": []}

    final = read_native_journal(journal_path)
    assert final["stopped"] is False
    assert final["pending_calls"] == [CALL]
    assert final["calls"] == 0


# -- Experiment 4f: no success claim for a receipt whose evidence drifted ------

def store_bytes(store):
    return {name: (store / name).read_bytes()
            for name in ("intents.jsonl", "starts.jsonl", "outcomes.jsonl")}


@pytest.mark.parametrize("damage,failure", [
    ("replace", "effect-evidence-mismatch"),
    ("delete", "effect-evidence-unreadable"),
])
def test_reconcile_refuses_the_success_row_for_drifted_receipt_evidence(
        tmp_path, receipt_recovery_case, damage, failure):
    """The acting successor read path fails closed when evidence stopped re-reading.

    The store still says completed; the bytes the receipt named no longer
    re-read, so the successor must not turn that record into its own durable
    success claim. Nothing is dispatched, nothing is written, and the verdict is
    reported for whoever restores the evidence.
    """
    case = receipt_recovery_case(tmp_path)
    if damage == "replace":
        case["artifact"].write_bytes(b"replaced bytes\n")
        observed = hashlib.sha256(b"replaced bytes\n").hexdigest()
    else:
        case["artifact"].unlink()
        observed = None
    path = tmp_path / "drift.jsonl"
    path.write_bytes(case["journal"])
    before_store = store_bytes(case["store"])

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, case["store"], writer_id="loop-3", authority="test:authority",
        authorized=lambda call: True)

    assert result["status"] == "unknown"
    assert result["reconciled"] == []
    entry = result["evidence_drift"][0]
    assert entry["call"] == case["call"]["id"]
    assert entry["failure"] == failure
    assert entry["recorded_sha256"] == case["digest"]
    assert entry["observed_sha256"] == observed
    assert path.read_bytes() == case["journal"]          # no row appended
    assert store_bytes(case["store"]) == before_store    # receipt untouched
    assert read_native_journal(path)["pending_calls"] == [case["call"]]


def test_reconcile_appends_the_record_again_once_the_evidence_is_restored(
        tmp_path, receipt_recovery_case):
    """The refusal is a function of the bytes: restore them and the claim lands."""
    case = receipt_recovery_case(tmp_path)
    original = case["artifact"].read_bytes()
    case["artifact"].write_bytes(b"replaced bytes\n")

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    path = tmp_path / "refused.jsonl"
    path.write_bytes(case["journal"])
    refused = reconcile_caller_journal(
        path, case["store"], writer_id="loop-3", authority="test:authority",
        authorized=lambda call: True)
    assert refused["status"] == "unknown"

    case["artifact"].write_bytes(original)
    restored = tmp_path / "restored.jsonl"
    restored.write_bytes(case["journal"])
    result = reconcile_caller_journal(
        restored, case["store"], writer_id="loop-3", authority="test:authority",
        authorized=lambda call: True)

    assert result == {"status": "completed", "reconciled": [case["call"]["id"]]}
    rows = [json.loads(line) for line in restored.read_text().splitlines()]
    event = [row for row in rows if row["kind"] == "tool_result"][-1]
    assert event["call"] == case["call"]
    assert event["message"]["isError"] is False
    record = json.loads(event["message"]["content"][0]["text"])
    assert record["status"] == "completed"
    # The read-side verdict is a fact about this read, not part of the durable
    # record: the appended row is the receipt exactly as a reader would see it.
    assert "evidence_verdict" not in record
    assert read_native_journal(restored)["pending_calls"] == []
    assert read_native_journal(restored)["calls"] == 1


def test_reconcile_reports_revoked_authority_before_receipt_drift(
        tmp_path, receipt_recovery_case):
    """A revoked grant is answered as such even when the evidence also drifted."""
    case = receipt_recovery_case(tmp_path)
    case["artifact"].write_bytes(b"replaced bytes\n")
    path = tmp_path / "revoked.jsonl"
    path.write_bytes(case["journal"])

    from mishe_tauftauf.inference_loop import reconcile_caller_journal
    result = reconcile_caller_journal(
        path, case["store"], writer_id="loop-3", authority="test:authority",
        authorized=lambda call: False)

    assert result == {"status": "authority_denied", "reconciled": []}
    assert path.read_bytes() == case["journal"]


def test_wire_usage_none_means_unknown_not_zero(tmp_path):
    events = []
    result, _, _ = drive(Session(turn()), record=events.append,
                         complete=lambda context: True)
    assert result["status"] == "complete"
    assert result["wire_usage"] == [None]
    outputs = [row for row in events if row["kind"] == "model_output"]
    assert len(outputs) == 1
    # asdict serializes missing usage as explicit None: UNKNOWN, never 0.
    assert outputs[0]["response"]["wire_usage"] is None


def test_wire_usage_survives_journal_round_trip_and_continuation(tmp_path):
    from mishe_tauftauf.inference_transport import NativeTurn
    usage = {"input": 16, "output": 169, "total": 185}
    response = turn([CALL])
    native = NativeTurn(response.terminal, response.assistant, dict(usage), None,
                        {"sessionId": "usage-session", "boundaryDigest": "usage-boundary"})
    path = tmp_path / "usage.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record)
    state = read_native_journal(path)
    assert state["wire_usage"] == [usage]
    continued_turn = turn()
    continued_turn = type(continued_turn)(continued_turn.terminal,
                                          continued_turn.assistant, dict(usage))
    second = tmp_path / "usage-continued.jsonl"
    with NativeJournal(second) as journal:
        result, _ = continue_journal(path, Session(continued_turn), journal)
    assert result["status"] == "complete"
    assert result["wire_usage"] == [usage, usage]
    # The continuation journal replays the checkpoint prefix, so a resume of
    # this file (chained continuation) keeps per-turn alignment end to end.
    assert read_native_journal(second)["wire_usage"] == [usage, usage]


def test_wire_usage_chained_continuation_keeps_alignment(tmp_path):
    from mishe_tauftauf.inference_transport import NativeTurn
    usage = {"input": 16, "output": 169, "total": 185}
    first = tmp_path / "chain-first.jsonl"
    with NativeJournal(first) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        response = turn([CALL])
        native = NativeTurn(response.terminal, response.assistant, dict(usage),
                            None, {"sessionId": "chain", "boundaryDigest": "chain"})
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record)
    second = tmp_path / "chain-second.jsonl"
    with NativeJournal(second) as journal:
        def record_second(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        call_b = {**CALL, "id": "chain-b"}
        continued = turn([call_b])
        continued = type(continued)(continued.terminal, continued.assistant,
                                    dict(usage))
        with pytest.raises(InterruptedError):
            continue_journal(first, Session(continued), record_second)
    third = tmp_path / "chain-third.jsonl"
    with NativeJournal(third) as journal:
        result, _ = continue_journal(second, Session(turn()), journal,
                                     complete=lambda context: True)
    assert result["status"] == "complete"
    assert result["turns"] == 3
    # Third turn is a synthetic no-usage turn: None stays UNKNOWN, never 0.
    assert result["wire_usage"] == [usage, usage, None]
    assert read_native_journal(third)["wire_usage"] == [usage, usage, None]


def test_fetch_ledger_survives_journal_round_trip(tmp_path):
    from mishe_tauftauf.inference_transport import NativeTurn
    ledger = {"allowance": 2, "workClass": "experiment-2-read-only-ci-observation",
              "dispatches": 2, "upstream_failure": {"kind": "status", "status": 429,
                                                    "statusText": "Too Many Requests"}}
    response = turn([CALL])
    native = NativeTurn(response.terminal, response.assistant, None, None, None, ledger)
    path = tmp_path / "ledger.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record)
    state = read_native_journal(path)
    # One durable row per turn: the granted allowance, the claimed dispatches
    # and the preserved upstream failure, readable without the session process.
    assert state["fetch_ledger"] == [ledger]
    row = [row for row in (json.loads(line) for line in
                           path.read_text().splitlines())
           if row["kind"] == "model_output"][0]
    assert row["response"]["fetch_ledger"] == ledger


def test_fetch_ledger_absent_stays_unknown_per_turn(tmp_path):
    path = tmp_path / "no-ledger.jsonl"
    with NativeJournal(path) as journal:
        drive(Session(turn()), record=journal, complete=lambda context: True)
    assert read_native_journal(path)["fetch_ledger"] == [None]


EXHAUSTION_LEDGER = {"allowance": 2,
                     "workClass": "experiment-2-read-only-ci-observation",
                     "dispatches": 2,
                     "upstream_failure": {"kind": "status", "status": 429,
                                          "statusText": "Too Many Requests"}}


def exhausted(message="native provider fetch budget exhausted after 2 of 2 fetches "
                      "permitted for experiment-2-read-only-ci-observation; "
                      "upstream HTTP 429 Too Many Requests (transient)",
              ledger=EXHAUSTION_LEDGER):
    error = WorkerError(message)
    error.fetch_ledger = ledger
    return error


def test_failed_turn_records_its_fetch_ledger_row_beside_the_diagnostic(tmp_path):
    # An exhausted turn fails closed and still claimed real dispatches: the
    # worker's budget row is recorded on the failure record, so the dispatch
    # count stays auditable without trusting the diagnostic prose.
    class FailedSession:
        def turn(self, context):
            raise exhausted()

    path = tmp_path / "exhausted.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError):
            drive(FailedSession(), record=journal)
    row = [row for row in (json.loads(line) for line in
                           path.read_text().splitlines())
           if row["kind"] == "model_failure"][0]
    assert row["fetch_ledger"] == EXHAUSTION_LEDGER
    state = read_native_journal(path)
    assert state["fetch_ledger"] == [EXHAUSTION_LEDGER]
    assert state["model_failure"]["type"] == "WorkerError"
    assert state["status"] == "unknown"


def test_failed_turn_without_a_ledger_row_stays_unknown(tmp_path):
    class FailedSession:
        def turn(self, context):
            raise WorkerError("native session exited 1")

    path = tmp_path / "exited.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError):
            drive(FailedSession(), record=journal)
    state = read_native_journal(path)
    assert state["fetch_ledger"] == [None]


def test_failure_ledger_row_keeps_per_turn_alignment(tmp_path):
    # The failed turn's row lands at its own turn index, so a journal with a
    # completed turn followed by an exhausted one reads one row per attempt.
    class TurnThenExhausted:
        def __init__(self):
            self.turns = iter([turn([CALL])])

        def turn(self, context):
            try:
                return next(self.turns)
            except StopIteration:
                raise exhausted() from None

    path = tmp_path / "alignment.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError):
            drive(TurnThenExhausted(), record=journal)
    # Turn 0 completed without a ledger (the plain WorkerTurn carries none);
    # turn 1 failed closed holding the budget row for its two claims.
    assert read_native_journal(path)["fetch_ledger"] == [None, EXHAUSTION_LEDGER]


def test_malformed_failure_ledger_row_is_refused(tmp_path):
    events = []

    class FailedSession:
        def turn(self, context):
            raise exhausted()

    with pytest.raises(WorkerError):
        drive(FailedSession(), record=events.append)
    events[-1]["fetch_ledger"] = 7
    path = tmp_path / "invalid.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    with pytest.raises(NativeJournalError):
        read_native_journal(path)


SECOND_LEDGER = {"allowance": 2,
                 "workClass": "experiment-2-read-only-ci-observation",
                 "dispatches": 1, "upstream_failure": None}


def test_fetch_ledger_survives_resume_from_continuation(tmp_path):
    # A resumed run must keep the prior turns' dispatch evidence: the
    # continuation journal's checkpoint replays the prefix so the rows stay
    # aligned, one per attempted turn, without trusting the earlier process.
    from mishe_tauftauf.inference_transport import NativeTurn
    ledger = {"allowance": 2, "workClass": "experiment-2-read-only-ci-observation",
              "dispatches": 2, "upstream_failure": {"kind": "status", "status": 429,
                                                    "statusText": "Too Many Requests"}}
    response = turn([CALL])
    native = NativeTurn(response.terminal, response.assistant, None, None, None, ledger)
    path = tmp_path / "ledger.jsonl"
    with NativeJournal(path) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record)
    continued = turn()
    second_native = NativeTurn(continued.terminal, continued.assistant,
                               None, None, None, dict(SECOND_LEDGER))
    second = tmp_path / "ledger-continued.jsonl"
    with NativeJournal(second) as journal:
        result, _ = continue_journal(path, Session(second_native), journal)
    assert result["status"] == "complete"
    assert result["fetch_ledger"] == [ledger, SECOND_LEDGER]
    # The continuation journal replays the checkpoint prefix, so a resume of
    # this file (chained continuation) keeps per-turn alignment end to end.
    assert read_native_journal(second)["fetch_ledger"] == [ledger, SECOND_LEDGER]
    seed = [row for row in map(json.loads, second.read_text().splitlines())
            if row["kind"] == "checkpoint"][0]["state"]
    assert seed["fetch_ledger"] == [ledger]


def test_fetch_ledger_chained_continuation_keeps_alignment(tmp_path):
    from mishe_tauftauf.inference_transport import NativeTurn
    ledger = {"allowance": 2, "workClass": "experiment-2-read-only-ci-observation",
              "dispatches": 2, "upstream_failure": {"kind": "status", "status": 429,
                                                    "statusText": "Too Many Requests"}}
    first = tmp_path / "chain-first.jsonl"
    with NativeJournal(first) as journal:
        def record(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        response = turn([CALL])
        native = NativeTurn(response.terminal, response.assistant, None, None,
                            None, ledger)
        with pytest.raises(InterruptedError):
            drive(Session(native), record=record)
    second = tmp_path / "chain-second.jsonl"
    with NativeJournal(second) as journal:
        def record_second(event):
            journal(event)
            if event["kind"] == "tool_result":
                raise InterruptedError
        call_b = {**CALL, "id": "chain-b"}
        continued = turn([call_b])
        continued = NativeTurn(continued.terminal, continued.assistant, None,
                               None, None, dict(SECOND_LEDGER))
        with pytest.raises(InterruptedError):
            continue_journal(first, Session(continued), record_second)
    third = tmp_path / "chain-third.jsonl"
    with NativeJournal(third) as journal:
        result, _ = continue_journal(second, Session(turn()), journal,
                                     complete=lambda context: True)
    assert result["status"] == "complete"
    assert result["turns"] == 3
    # Third turn is a synthetic turn with no ledger row: None stays UNKNOWN.
    assert result["fetch_ledger"] == [ledger, SECOND_LEDGER, None]
    assert read_native_journal(third)["fetch_ledger"] == [ledger, SECOND_LEDGER, None]


def test_failed_turn_row_survives_resume_into_the_unknown_handoff(tmp_path):
    # An unresolved history never constructs a provider, and its checkpoint
    # still carries the failed turn's budget row (one more attempted turn
    # than completed turns) into the handoff journal.
    class TurnThenExhausted:
        def __init__(self):
            self.turns = iter([turn([CALL])])

        def turn(self, context):
            try:
                return next(self.turns)
            except StopIteration:
                raise exhausted() from None

    path = tmp_path / "exhausted-history.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError):
            drive(TurnThenExhausted(), record=journal)
    second = tmp_path / "handoff.jsonl"
    with NativeJournal(second) as journal:
        result, effects = continue_journal(path, Session(turn()), journal)
    assert result["status"] == "unknown"
    assert effects == []
    # Turn 0 completed without a ledger row; the exhausted turn 1 row rides
    # the failure record and the carried prefix into the handoff journal.
    assert result["fetch_ledger"] == [None, EXHAUSTION_LEDGER]
    assert read_native_journal(second)["fetch_ledger"] == [None, EXHAUSTION_LEDGER]
    # Wire usage counts completed turns, so the failed attempt adds none,
    # while the ledger prefix counts it: the two views keep their own scope.
    assert read_native_journal(second)["wire_usage"] == [None]


@pytest.mark.parametrize("prefix", [
    7,                                   # not a list at all
    [7, None],                           # entry neither dict nor None
    [],                                  # fewer rows than turns
    [None, EXHAUSTION_LEDGER, None],     # more rows than attempted turns
])
def test_malformed_checkpoint_fetch_ledger_prefix_is_refused(tmp_path, prefix):
    class TurnThenExhausted:
        def __init__(self):
            self.turns = iter([turn([CALL])])

        def turn(self, context):
            try:
                return next(self.turns)
            except StopIteration:
                raise exhausted() from None

    path = tmp_path / "exhausted-history.jsonl"
    with NativeJournal(path) as journal:
        with pytest.raises(WorkerError):
            drive(TurnThenExhausted(), record=journal)
    second = tmp_path / "handoff.jsonl"
    with NativeJournal(second) as journal:
        continue_journal(path, Session(turn()), journal)
    rows = [json.loads(line) for line in second.read_text().splitlines()]
    rows[0]["state"]["fetch_ledger"] = prefix
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(NativeJournalError):
        read_native_journal(tampered)


@pytest.mark.parametrize("context", [None, [], 0, "bad"])
@pytest.mark.parametrize("boundary", ["checkpoint", "model_input"])
def test_nonobject_journal_context_is_classified_as_corrupt(tmp_path, context, boundary):
    _, events, _ = drive(Session(turn()))
    if boundary == "checkpoint":
        rows = [events[0]]
        rows[0]["state"]["context"] = context
    else:
        rows = [next(row for row in events if row["kind"] == "model_input")]
        rows[0]["context"] = context
    path = tmp_path / "malformed-context.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(NativeJournalError):
        read_native_journal(path)
