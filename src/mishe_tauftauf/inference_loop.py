"""Native loop orchestration; caller owns session lifetime and durable effects.

record(event) must persist synchronously or raise. dispatch(call) must bind the
original operation/store and recheck current authority after its own persistence.
Neither a recorded proposal nor this loop's eligibility check permits replay.
Returned completion is an oracle result, not proof of subsequent session close.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Callable
from pathlib import Path

from .inference_worker import WorkerError


def snapshot(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def drive_native(session, context: dict, *, obligation: dict, max_turns: int,
                 max_calls: int, dispatch: Callable, record: Callable,
                 phase: Callable, cancelled: Callable, authorized: Callable,
                 complete: Callable, resume_from=None) -> dict:
    """Drive native messages without flattening IDs, tool results or terminals.

    Capability selection is model-owned. typed argument validation and execution
    remain inside dispatch; tools/results cannot widen caller-granted authority.
    No automatic resubmission after exceptions, interruption or unknown effects.
    Caller bounds callback duration; NativeSession bounds provider lifetime/I/O.
    resume_from reads a prior caller journal; only an unstopped completed-result
    boundary may call the provider. Original lifetime budgets/IDs are retained.
    Caller must restore provider state and reconcile original effect authority;
    this API neither restores the session nor retries unresolved selections.
    record must target a new journal, never append to or replace resume_from.
    """
    for value in (max_turns, max_calls):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError('positive integer turn/call budgets required')
    context = snapshot(context)
    obligation = snapshot(obligation)
    names = [tool['name'] for tool in context.get('tools', [])]
    if len(set(names)) != len(names):
        raise ValueError('duplicate capability names')
    allowed = set(names)
    messages = context['messages']
    recovery = None
    if resume_from is not None:
        recovery = read_native_journal(resume_from)
        if recovery['stopped']:
            raise NativeJournalError('stopped caller cannot continue')
        if recovery['budgets'] != {'turns': max_turns, 'calls': max_calls}:
            raise NativeJournalError('original lifetime budgets required')
        if recovery['context'] != context or recovery['obligation'] != obligation:
            raise NativeJournalError('continuation context or obligation changed')
    used_ids = set(recovery['used_ids']) if recovery else set()
    executed = recovery['calls'] if recovery else 0
    turns = recovery['turns'] if recovery else 0

    def emit(kind, **fields):
        record(snapshot({'kind': kind, 'obligation': obligation, **fields}))

    def stop(status):
        result = {'status': status, 'turns': turns, 'calls': executed,
                  'context': snapshot(context)}
        if recovery and recovery['status'] != 'ready':
            result['pending_calls'] = snapshot(recovery['pending_calls'])
            result['used_ids'] = sorted(used_ids)
        emit('stop', status=status, turns=turns, calls=executed)
        return result

    checkpoint = {
        'context': context, 'budgets': {'turns': max_turns, 'calls': max_calls},
        'turns': turns, 'calls': executed, 'used_ids': sorted(used_ids),
        'pending_calls': recovery['pending_calls'] if recovery else [],
        'status': recovery['status'] if recovery else 'ready',
        'provider_checkpoint': recovery['provider_checkpoint'] if recovery else None,
        'model_failure': recovery['model_failure'] if recovery else None,
    }
    emit('checkpoint', state=checkpoint)
    if recovery and recovery['status'] != 'ready':
        return stop('unknown')
    for index in range(turns, max_turns):
        if cancelled():
            return stop('cancelled')
        emit('model_input', turn=index, context=context)
        if cancelled():
            return stop('cancelled')
        try:
            turn = session.turn(snapshot(context))
        except WorkerError as exc:
            # Keep the transport's original diagnostic, not a cancellation
            # reclassification. No terminal/checkpoint means no safe replay.
            emit('model_failure', turn=index,
                 error={'type': type(exc).__name__, 'message': str(exc)})
            raise
        turns += 1
        emit('model_output', turn=index, response=asdict(turn))
        assistant = snapshot(turn.assistant)
        calls = [item for item in assistant['content'] if item.get('type') == 'toolCall']
        messages.append(assistant)
        if cancelled():
            return stop('cancelled')
        if turn.terminal != 'done' or assistant['stopReason'] not in ('toolUse', 'stop'):
            return stop('unknown')
        if not calls:
            if assistant['stopReason'] != 'stop':
                return stop('unknown')
            return stop('complete' if complete(snapshot(context)) else 'incomplete')
        if assistant['stopReason'] != 'toolUse':
            return stop('unknown')
        # Reject the entire malformed selection before any call in this turn.
        ids = [call['id'] for call in calls]
        if (len(set(ids)) != len(ids) or any(i in used_ids for i in ids)
                or any(call['name'] not in allowed for call in calls)):
            return stop('invalid_selection')
        if executed + len(calls) > max_calls:
            return stop('call_budget')
        used_ids.update(ids)
        for call in calls:
            emit('proposal', turn=index, call=call)
            # A synchronous observer may revoke/cancel at this exact boundary.
            phase(snapshot(call))
            if cancelled():
                return stop('cancelled')
            if not authorized(snapshot(call)):
                return stop('authority_denied')
            if cancelled():
                return stop('cancelled')
            # dispatch owns final after-persistence authority check and durable
            # operation identity. Exceptions propagate: never retry blindly.
            outcome = snapshot(dispatch(snapshot(call)))
            if not isinstance(outcome, dict) or outcome.get('status') not in (
                    'completed', 'partial', 'unknown', 'not-started'):
                raise ValueError('dispatch must return an explicit outcome status')
            executed += 1
            message = {'role': 'toolResult', 'toolCallId': call['id'],
                       'toolName': call['name'], 'content': [{'type': 'text',
                       'text': json.dumps(outcome, ensure_ascii=False, allow_nan=False)}],
                       'isError': outcome['status'] != 'completed',
                       'timestamp': assistant.get('timestamp', 0)}
            emit('tool_result', turn=index, call=call, message=message)
            messages.append(message)
            if outcome['status'] != 'completed':
                return stop('unknown')
    return stop('turn_budget')


def recover_native(path, *, open_session: Callable, dispatch: Callable,
                   record: Callable, phase: Callable, cancelled: Callable,
                   authorized: Callable, complete: Callable) -> dict:
    """Continue journal-owned completed results through a fresh native session.

    open_session(checkpoint=...) must return a context manager for a fresh session.
    Selection uses only the recorded model response, results and lifetime budgets;
    no caller-supplied result or checkpoint can replace them. The journal remains
    caller-owned evidence, not authenticated effect authority. dispatch retains
    its original store/current-authority obligations. record targets a new file.
    Unresolved histories never construct a session or redispatch pending calls.
    """
    recovery = read_native_journal(path)
    if recovery['stopped'] or recovery['budgets'] is None:
        raise NativeJournalError('unstopped lifetime-budgeted journal required')
    options = dict(obligation=recovery['obligation'],
                   max_turns=recovery['budgets']['turns'],
                   max_calls=recovery['budgets']['calls'], resume_from=path,
                   dispatch=dispatch, record=record, phase=phase,
                   cancelled=cancelled, authorized=authorized, complete=complete)
    context = recovery['context']
    if (recovery['status'] != 'ready'
            or recovery['turns'] >= recovery['budgets']['turns']):
        return drive_native(None, context, **options)
    checkpoint = recovery['provider_checkpoint']
    if not isinstance(checkpoint, dict) or not checkpoint:
        raise NativeJournalError('UNKNOWN: missing recorded provider checkpoint')
    with open_session(checkpoint=snapshot(checkpoint)) as session:
        return drive_native(session, context, **options)


class NativeJournalError(ValueError):
    """Unusable caller history; preserved bytes never authorize continuation."""



def reconcile_caller_journal(
    journal_path: Path,
    effect_store_dir: Path,
    *,
    writer_id: str,
    obligation: dict,
    authority: str,
    authorized: Callable,
    capability_version: str = "v1",
    budget: dict | None = None,
    operation_prefix: str = "effect",
) -> dict:
    """Reconcile a caller journal's pending calls against the effect store.

    After a crash between `dispatch(call)` and `emit('tool_result', ...)`, the
    effect store has a durable outcome but the caller journal is missing the
    `tool_result` row. This function reads the journal, finds pending calls,
    and for each one calls `EffectBoundary.recover(call)` to get the durable
    outcome without executing.

    If the outcome is `completed` and authority is still granted, appends a
    durable `tool_result` row to the journal. If the outcome is `unknown` or
    `None`, leaves the call pending. If authority is revoked, returns
    `authority_denied` without appending.

    Never dispatches, never constructs a provider session, never duplicates
    execution.

    Returns a dict with `status` and `reconciled` (list of call ids that were
    reconciled).
    """
    from .inference_effects import EffectBoundary

    state = read_native_journal(journal_path)
    pending = state["pending_calls"]
    if not pending:
        return {"status": "completed", "reconciled": []}

    boundary = EffectBoundary(
        effect_store_dir,
        writer_id=writer_id,
        obligation=obligation,
        execute=lambda call: None,  # never called by recover
        authority=authority,
        capability_version=capability_version,
        budget=budget,
        operation_prefix=operation_prefix,
    )

    reconciled = []
    for call in pending:
        outcome = boundary.recover(call)
        if outcome is None:
            # Never allocated: no durable state to reconcile
            continue
        if outcome["status"] != "completed":
            # Unknown or partial: leave pending, do not dispatch
            continue
        if not authorized(call):
            return {"status": "authority_denied", "reconciled": reconciled}
        # Append durable tool_result row
        message = {
            "role": "toolResult",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "content": [{"type": "text", "text": json.dumps(outcome, ensure_ascii=False, allow_nan=False)}],
            "isError": False,
            "timestamp": 0,
        }
        # Append to journal
        with open(journal_path, "a", encoding="utf-8") as f:
            event = {
                "kind": "tool_result",
                "turn": state["turns"] - 1,
                "call": call,
                "message": message,
                "obligation": obligation,
            }
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        reconciled.append(call["id"])

    if reconciled:
        return {"status": "completed", "reconciled": reconciled}
    return {"status": "unknown", "reconciled": []}

class NativeJournal:
    """Exclusive new caller journal, usable as drive_native's record callback.

    Single caller/writer only. Every callback flushes/fsyncs before returning.
    Failure poisons the writer; reopening, repair and effect reconciliation are
    deliberately not automatic. File/process-crash scope, not power-loss scope.
    This journal is not an effect store and does not allocate operation IDs.
    """

    def __init__(self, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._stream = os.fdopen(fd, 'w', encoding='utf-8')
        self._failed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._stream.close()

    def __call__(self, event):
        if self._failed or self._stream.closed:
            raise NativeJournalError('journal writer unavailable')
        try:
            line = json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n'
            self._stream.write(line)
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except Exception:
            self._failed = True
            raise


def read_native_journal(path):
    """Read/reconstruct a recorded run without opening or creating effect state.

    `ready` means only a completed-result boundary was reconstructed. Original
    lifetime budgets (None for older histories) and used IDs are returned.
    drive_native(..., resume_from=path) preserves these; provider opaque state,
    current authority and original effect-store reconciliation remain caller-owned.
    Pending selections include calls not yet proposed; their effect status is
    UNKNOWN here, not authoritative absence. A recorded stop is an author fact.
    """
    def invalid_constant(value):
        raise NativeJournalError(f'nonfinite journal value: {value}')

    raw = Path(path).read_bytes()
    if not raw or not raw.endswith(b'\n'):
        raise NativeJournalError('empty or incomplete journal')
    try:
        rows = [json.loads(line, parse_constant=invalid_constant)
                for line in raw.splitlines()]
        return _reconstruct_native(rows)
    except (KeyError, TypeError, IndexError, UnicodeError, ValueError) as exc:
        raise NativeJournalError(f'invalid native journal: {exc}') from exc


def _reconstruct_native(rows):
    obligation = rows[0]['obligation']
    context = None
    pending = []
    proposed = None
    proposed_turn = None
    used_ids = set()
    turns = calls = 0
    state = 'initial'
    status = 'unknown'
    stopped = False
    budgets = None
    provider_checkpoint = None
    model_failure = None
    for row in rows:
        if stopped or row['obligation'] != obligation:
            raise NativeJournalError('record after stop or changed obligation')
        kind = row['kind']
        if kind == 'checkpoint':
            if state != 'initial' or context is not None:
                raise NativeJournalError('checkpoint must be the first record')
            seed = row['state']
            budgets = seed['budgets']
            if (not isinstance(budgets, dict) or set(budgets) != {'turns', 'calls'}
                    or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0
                           for n in budgets.values())):
                raise NativeJournalError('invalid lifetime budgets')
            turns, calls = seed['turns'], seed['calls']
            ids = seed['used_ids']
            if (any(not isinstance(n, int) or isinstance(n, bool) or n < 0
                    for n in (turns, calls))
                    or turns > budgets['turns'] or calls > budgets['calls']
                    or not isinstance(ids, list)
                    or any(not isinstance(i, str) or not i for i in ids)
                    or len(set(ids)) != len(ids) or calls > len(ids)):
                raise NativeJournalError('invalid lifetime counters or IDs')
            context = snapshot(seed['context'])
            provider_checkpoint = snapshot(seed.get('provider_checkpoint'))
            if not isinstance(context['messages'], list):
                raise NativeJournalError('messages must be an ordered list')
            used_ids = set(ids)
            pending = snapshot(seed['pending_calls'])
            status = seed['status']
            if (not isinstance(pending, list) or status not in ('ready', 'unknown')
                    or (status == 'ready' and pending)):
                raise NativeJournalError('invalid continuation boundary')
            state = 'ready' if status == 'ready' else 'unresolved'
            model_failure = snapshot(seed.get('model_failure'))
            if model_failure is not None:
                if (status != 'unknown' or not isinstance(model_failure, dict)
                        or set(model_failure) != {'type', 'message'}
                        or any(not isinstance(v, str) for v in model_failure.values())):
                    raise NativeJournalError('invalid model failure checkpoint')
            continue
        if kind == 'stop':
            if row['turns'] != turns or row['calls'] != calls:
                raise NativeJournalError('stop counters disagree')
            status = row['status']
            if status not in ('cancelled', 'unknown', 'complete', 'incomplete',
                              'invalid_selection', 'call_budget', 'authority_denied',
                              'turn_budget'):
                raise NativeJournalError('invalid stop status')
            if status in ('complete', 'incomplete') and state != 'answer':
                raise NativeJournalError('completion without final answer')
            stopped = True
            continue
        if kind == 'model_input':
            if state not in ('initial', 'ready') or row['turn'] != turns:
                raise NativeJournalError('unexpected model input')
            if context is not None and row['context'] != context:
                raise NativeJournalError('next context disagrees with history')
            context = snapshot(row['context'])
            if not isinstance(context['messages'], list):
                raise NativeJournalError('messages must be an ordered list')
            state, status = 'input', 'unknown'
        elif kind == 'model_failure':
            if state != 'input' or row['turn'] != turns:
                raise NativeJournalError('failure without pending model input')
            error = row['error']
            if (not isinstance(error, dict) or set(error) != {'type', 'message'}
                    or any(not isinstance(v, str) for v in error.values())):
                raise NativeJournalError('invalid model failure diagnostic')
            model_failure = snapshot(error)
            state, status = 'unresolved', 'unknown'
        elif kind == 'model_output':
            if state != 'input' or row['turn'] != turns:
                raise NativeJournalError('unexpected model output')
            if budgets is not None and turns >= budgets['turns']:
                raise NativeJournalError('model output exceeds lifetime turn budget')
            response = row['response']
            provider_checkpoint = snapshot(response.get('checkpoint'))
            assistant = snapshot(response['assistant'])
            context['messages'].append(assistant)
            turns += 1
            selected = [item for item in assistant['content']
                        if item.get('type') == 'toolCall']
            ids = [item['id'] for item in selected]
            allowed = {tool['name'] for tool in context.get('tools', [])}
            valid = (response['terminal'] == 'done'
                     and assistant['stopReason'] == ('toolUse' if selected else 'stop')
                     and all(isinstance(i, str) and i for i in ids)
                     and len(set(ids)) == len(ids)
                     and not used_ids.intersection(ids)
                     and all(item['name'] in allowed for item in selected))
            valid = valid and (budgets is None
                               or calls + len(selected) <= budgets['calls'])
            state = ('selected' if selected else 'answer') if valid else 'invalid'
            pending = selected
            if valid:
                used_ids.update(ids)
        elif kind == 'proposal':
            if (state != 'selected' or proposed is not None or not pending
                    or row['turn'] != turns - 1 or row['call'] != pending[0]):
                raise NativeJournalError('proposal disagrees with selected call')
            proposed = row['call']
            proposed_turn = row['turn']
        elif kind == 'tool_result':
            if (state != 'selected' or proposed is None
                    or row['turn'] != turns - 1 or row['call'] != proposed):
                raise NativeJournalError('result without matching proposal')
            if budgets is not None and calls >= budgets['calls']:
                raise NativeJournalError('tool result exceeds lifetime call budget')
            message = row['message']
            outcome = json.loads(message['content'][0]['text'])
            outcome_status = outcome['status']
            if (message['role'] != 'toolResult'
                    or message['toolCallId'] != proposed['id']
                    or message['toolName'] != proposed['name']
                    or outcome_status not in ('completed', 'partial', 'unknown', 'not-started')
                    or message['isError'] is not (outcome_status != 'completed')):
                raise NativeJournalError('uncorrelated or invalid tool result')
            context['messages'].append(snapshot(message))
            pending.pop(0)
            proposed = None
            proposed_turn = None
            calls += 1
            if outcome_status != 'completed':
                state, status = 'unresolved', 'unknown'
            elif not pending:
                state, status = 'ready', 'ready'
        else:
            raise NativeJournalError('unknown record kind')
    return {'obligation': snapshot(obligation), 'context': context,
            'status': status, 'pending_calls': snapshot(pending),
            'proposed_call': snapshot(proposed) if proposed is not None else None,
            'proposed_turn': proposed_turn,
            'used_ids': sorted(used_ids), 'turns': turns, 'calls': calls,
            'stopped': stopped, 'budgets': budgets,
            'provider_checkpoint': provider_checkpoint,
            'model_failure': model_failure}
