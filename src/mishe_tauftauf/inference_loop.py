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
from .native_pane import read_prompt


def read_pane_prompt(stream, *, limit: int = 1 << 20, on_ready=None) -> str:
    """Read one submitted prompt using the explicit native pane boundary.

    TTY pastes are bracketed and wait for Enter regardless of delivery delay;
    pipes use the same frames for multiline input or one plain line per prompt.
    EOF returns ''; a blank submission returns '\\n'. ``limit`` bounds encoded
    payload bytes, including the submitting newline and excluding frame markers.
    A malformed/oversize frame raises; its stream must not be reused.
    ``on_ready`` publishes readiness only after input framing is prepared.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError('positive integer byte limit required')
    return read_prompt(stream, limit=limit, on_ready=on_ready)


def snapshot(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _tool_names(tools):
    if (not isinstance(tools, list)
            or any(not isinstance(tool, dict)
                   or not isinstance(tool.get('name'), str) or not tool['name']
                   for tool in tools)):
        raise ValueError('tools must be named schemas')
    names = [tool['name'] for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError('duplicate capability names')
    return names


def _validate_tool_offer(context, original_tools):
    names = _tool_names(context.get('tools', []))
    granted = dict(zip(_tool_names(original_tools), original_tools))
    if any(name not in granted or tool != granted[name]
           for name, tool in zip(names, context.get('tools', []))):
        raise ValueError('offered tools exceed original grant')


def _apply_turn_policy(context, original_tools, decision):
    if decision is None:
        return
    if (not isinstance(decision, dict)
            or set(decision) not in ({'tools'}, {'tools', 'notice'})
            or not isinstance(decision['tools'], list)
            or any(not isinstance(name, str) for name in decision['tools'])
            or len(set(decision['tools'])) != len(decision['tools'])
            or ('notice' in decision and not isinstance(decision['notice'], str))):
        raise ValueError('turn policy requires tool names and an optional string notice')
    granted = dict(zip(_tool_names(original_tools), original_tools))
    if any(name not in granted for name in decision['tools']):
        raise ValueError('turn policy exceeds original grant')
    context['tools'] = [snapshot(granted[name]) for name in decision['tools']]
    if 'notice' in decision:
        context['messages'].append({
            'role': 'user', 'content': [{'type': 'text', 'text': decision['notice']}],
            'timestamp': 0})


def drive_native(session, context: dict, *, obligation: dict, max_turns: int,
                 max_calls: int, dispatch: Callable, record: Callable,
                 phase: Callable, cancelled: Callable, authorized: Callable,
                 complete: Callable, resume_from=None, before_turn=None) -> dict:
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
    before_turn receives a detached {turn, calls, remaining_turns,
    remaining_calls, context}; turn is zero-based and remaining_turns includes
    this turn. Return None to keep the offer, or {tools: [original grant names],
    notice?: str}. Only exact original schemas may be re-offered. A notice is
    appended verbatim as user observation, never system authority or a rewrite
    of history. Its decision is journaled before provider input. Interrupted
    policy/input boundaries cannot be retried by recovery.
    """
    for value in (max_turns, max_calls):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError('positive integer turn/call budgets required')
    context = snapshot(context)
    obligation = snapshot(obligation)
    original_tools = snapshot(context.get('tools', []))
    _tool_names(original_tools)
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
        original_tools = snapshot(recovery['original_tools'])
    used_ids = set(recovery['used_ids']) if recovery else set()
    executed = recovery['calls'] if recovery else 0
    turns = recovery['turns'] if recovery else 0
    # Per-turn provider wire usage, aligned with turn index. None means
    # UNKNOWN (worker never saw a correlated terminal), never a synthesized
    # zero. Carried across resume_from so cost evidence survives continuation.
    wire_usages = list(recovery.get('wire_usage') or []) if recovery else []
    # Per-turn provider dispatch evidence (the worker's budget row), one entry
    # per attempted turn and aligned with the completed turns' rows. None means
    # UNKNOWN (the worker emitted no correlated row), never a synthesized zero.
    # Carried across resume_from so grant/claim evidence survives continuation.
    fetch_ledgers = list(recovery.get('fetch_ledger') or []) if recovery else []

    def emit(kind, **fields):
        record(snapshot({'kind': kind, 'obligation': obligation, **fields}))

    def stop(status):
        result = {'status': status, 'turns': turns, 'calls': executed,
                  'context': snapshot(context), 'wire_usage': snapshot(wire_usages),
                  'fetch_ledger': snapshot(fetch_ledgers)}
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
        'wire_usage': snapshot(wire_usages),
        'original_tools': original_tools,
        'fetch_ledger': snapshot(fetch_ledgers),
    }
    emit('checkpoint', state=checkpoint)
    if recovery and recovery['status'] != 'ready':
        return stop('unknown')
    for index in range(turns, max_turns):
        if cancelled():
            return stop('cancelled')
        if before_turn is not None:
            decision = snapshot(before_turn(snapshot({
                'turn': index, 'calls': executed,
                'remaining_turns': max_turns - index,
                'remaining_calls': max_calls - executed, 'context': context})))
            # Validate on a detached context before persisting the decision;
            # neither callback mutation nor failed persistence changes history.
            selected_context = snapshot(context)
            _apply_turn_policy(selected_context, original_tools, decision)
            emit('turn_policy', turn=index, decision=decision)
            context = selected_context
            messages = context['messages']
        allowed = set(_tool_names(context.get('tools', [])))
        emit('model_input', turn=index, context=context)
        if cancelled():
            return stop('cancelled')
        try:
            turn = session.turn(snapshot(context))
        except WorkerError as exc:
            # Keep the transport's original diagnostic, not a cancellation
            # reclassification. No terminal/checkpoint means no safe replay.
            # A failed turn's dispatch evidence (the worker's budget row, when
            # it emitted one) is recorded beside the diagnostic: the claims
            # already made stay auditable instead of living only in prose.
            emit('model_failure', turn=index,
                 error={'type': type(exc).__name__, 'message': str(exc)},
                 fetch_ledger=snapshot(getattr(exc, 'fetch_ledger', None)))
            raise
        turns += 1
        emit('model_output', turn=index, response=asdict(turn))
        # Cost evidence only: a failed journal write above already raised, so a
        # usage entry here can never mask a lost model-output row.
        wire_usages.append(snapshot(getattr(turn, 'wire_usage', None)))
        # One dispatch-evidence row per completed turn, beside the usage entry;
        # a failed turn's row is recorded on its own model_failure record.
        fetch_ledgers.append(snapshot(getattr(turn, 'fetch_ledger', None)))
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
            # dispatch owns durable operation identity; the execute callback it
            # invokes owns the final after-persistence authority check. Exceptions
            # propagate: never retry blindly.
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
                   authorized: Callable, complete: Callable, before_turn=None) -> dict:
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
                   cancelled=cancelled, authorized=authorized, complete=complete,
                   before_turn=before_turn)
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



def proposing_assistant(messages, call_id):
    """The recorded assistant message that proposed `call_id`, or None.

    A restored `tool_result` row must carry the timestamp of the assistant
    that proposed the call — the same value `drive_native` writes — so the
    recovered context stays message-identical to the uninterrupted
    continuation's. `messages[-1]` is not a safe source: after a crash mid
    selection it is the sibling call's `toolResult` row, and its timestamp is
    only coincidentally the same value.
    """
    for message in reversed(messages):
        if message.get('role') != 'assistant':
            continue
        if any(item.get('type') == 'toolCall' and item.get('id') == call_id
               for item in message.get('content', [])):
            return message
    return None


def reconcile_caller_journal(
    journal_path: Path,
    effect_store_dir: Path,
    *,
    writer_id: str,
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
    `authority_denied` without appending. A completion recorded by
    `record_receipt` is appended only while the evidence it named still
    re-reads: a receipt whose artifact was altered or deleted returns `unknown`
    with `evidence_drift` naming the verdict, leaves that call pending and
    dispatches nothing, because a lost artifact is not a lost effect and is
    never a reason to run the capability again.

    Never dispatches, never constructs a provider session, never duplicates
    execution.

    The appended row is the row the uninterrupted writer would have written
    for the same call — including the proposing assistant's recorded
    timestamp — so a continued session's next model input is message-identical
    to the uninterrupted continuation's.

    Returns a dict with `status` and `reconciled` (list of call ids that were
    reconciled).
    """
    from .inference_effects import EffectBoundary, recovery_evidence_drift

    state = read_native_journal(journal_path)
    pending = state["pending_calls"]
    if not pending:
        return {"status": "completed", "reconciled": []}
    if state["stopped"]:
        return {"status": "unknown", "reconciled": []}

    boundary = EffectBoundary(
        effect_store_dir,
        writer_id=writer_id,
        obligation=state["obligation"],
        execute=lambda call: None,  # never called by recover
        authority=authority,
        capability_version=capability_version,
        budget=budget,
        operation_prefix=operation_prefix,
    )

    reconciled = []
    drift: list[dict[str, Any]] = []
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
        evidence = recovery_evidence_drift(outcome)
        if evidence is not None:
            # The store still says completed, but the bytes the receipt named
            # no longer re-read, so this successor refuses to make the success
            # claim durable. Nothing is dispatched and the call stays pending
            # with the verdict preserved for whoever restores the evidence.
            drift.append({"call": call["id"], **evidence})
            continue
        # Append durable tool_result row. The read-side verdict is a fact about
        # this read, not part of the durable record, so it is excluded here.
        # The row is the row the uninterrupted writer would have appended for
        # this call: the proposing assistant's recorded timestamp, not a
        # synthesized one, so a continued session's next model input is
        # message-identical to the uninterrupted continuation's.
        assistant = proposing_assistant(state['context']['messages'], call['id'])
        message = {
            "role": "toolResult",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "content": [{"type": "text", "text": json.dumps(
                {key: value for key, value in outcome.items()
                 if key != "evidence_verdict"},
                ensure_ascii=False, allow_nan=False)}],
            "isError": False,
            "timestamp": assistant.get("timestamp", 0) if assistant else 0,
        }
        # Append to journal
        with open(journal_path, "a", encoding="utf-8") as f:
            event = {
                "kind": "tool_result",
                "turn": state["turns"] - 1,
                "call": call,
                "message": message,
                "obligation": state["obligation"],
            }
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        reconciled.append(call["id"])

    if drift:
        return {"status": "unknown", "reconciled": reconciled,
                "evidence_drift": drift}
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
    original_tools = None
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
    wire_usage = []
    fetch_ledgers = []
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
            if not isinstance(context, dict):
                raise NativeJournalError('context must be an object')
            original_tools = snapshot(seed.get('original_tools', context.get('tools', [])))
            _validate_tool_offer(context, original_tools)
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
            prefixed = seed.get('wire_usage', [])
            if (not isinstance(prefixed, list)
                    or any(not isinstance(u, dict) and u is not None for u in prefixed)
                    or len(prefixed) != turns):
                raise NativeJournalError('invalid checkpoint wire usage prefix')
            wire_usage = snapshot(prefixed)
            ledger_prefixed = seed.get('fetch_ledger', [])
            # One row per attempted turn: the completed turns' rows plus at
            # most one trailing failed-turn row. A prefix that cannot align
            # with the counters is refused, never silently re-indexed.
            if (not isinstance(ledger_prefixed, list)
                    or any(not isinstance(row, dict) and row is not None
                           for row in ledger_prefixed)
                    or not (len(prefixed) <= len(ledger_prefixed)
                            <= len(prefixed) + 1)):
                raise NativeJournalError('invalid checkpoint fetch ledger prefix')
            fetch_ledgers = snapshot(ledger_prefixed)
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
        if kind == 'turn_policy':
            if (state != 'ready' or type(row['turn']) is not int or row['turn'] != turns
                    or original_tools is None or budgets is None
                    or turns >= budgets['turns']):
                raise NativeJournalError('unexpected turn policy')
            _apply_turn_policy(context, original_tools, row['decision'])
            # The caller may have crashed after dispatching but before recording
            # model_input. Never re-run the hook or replay this uncertain turn.
            state, status = 'policy', 'unknown'
        elif kind == 'model_input':
            if state not in ('initial', 'ready', 'policy') or row['turn'] != turns:
                raise NativeJournalError('unexpected model input')
            if context is not None and row['context'] != context:
                raise NativeJournalError('next context disagrees with history')
            context = snapshot(row['context'])
            if not isinstance(context, dict):
                raise NativeJournalError('context must be an object')
            if original_tools is None:
                original_tools = snapshot(context.get('tools', []))
            _validate_tool_offer(context, original_tools)
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
            failure_ledger = row.get('fetch_ledger')
            if failure_ledger is not None and not isinstance(failure_ledger, dict):
                raise NativeJournalError('invalid failure fetch ledger row')
            model_failure = snapshot(error)
            # The failed turn's dispatch evidence keeps one row per attempted
            # turn, aligned with the completed turns' rows; a failure whose
            # worker emitted no row stays an explicit None, never a zero.
            fetch_ledgers.append(snapshot(failure_ledger))
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
            wire_usage.append(snapshot(response.get('wire_usage')))
            fetch_ledgers.append(snapshot(response.get('fetch_ledger')))
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
            'original_tools': snapshot(original_tools),
            'status': status, 'pending_calls': snapshot(pending),
            'proposed_call': snapshot(proposed) if proposed is not None else None,
            'proposed_turn': proposed_turn,
            'used_ids': sorted(used_ids), 'turns': turns, 'calls': calls,
            'stopped': stopped, 'budgets': budgets,
            'provider_checkpoint': provider_checkpoint,
            'model_failure': model_failure, 'wire_usage': snapshot(wire_usage),
            'fetch_ledger': snapshot(fetch_ledgers)}
