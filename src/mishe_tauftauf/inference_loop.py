"""Native loop orchestration; caller owns session lifetime and durable effects.

record(event) must persist synchronously or raise. dispatch(call) must bind the
original operation/store and recheck current authority after its own persistence.
Neither a recorded proposal nor this loop's eligibility check permits replay.
Returned completion is an oracle result, not proof of subsequent session close.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable


def snapshot(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def drive_native(session, context: dict, *, obligation: dict, max_turns: int,
                 max_calls: int, dispatch: Callable, record: Callable,
                 phase: Callable, cancelled: Callable, authorized: Callable,
                 complete: Callable) -> dict:
    """Drive native messages without flattening IDs, tool results or terminals.

    Capability selection is model-owned. typed argument validation and execution
    remain inside dispatch; tools/results cannot widen caller-granted authority.
    No automatic resubmission after exceptions, interruption or unknown effects.
    Caller bounds callback duration; NativeSession bounds provider lifetime/I/O.
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
    used_ids = set()
    executed = 0
    turns = 0

    def emit(kind, **fields):
        record(snapshot({'kind': kind, 'obligation': obligation, **fields}))

    def stop(status):
        result = {'status': status, 'turns': turns, 'calls': executed,
                  'context': snapshot(context)}
        emit('stop', status=status, turns=turns, calls=executed)
        return result

    for index in range(max_turns):
        if cancelled():
            return stop('cancelled')
        emit('model_input', turn=index, context=context)
        if cancelled():
            return stop('cancelled')
        turn = session.turn(snapshot(context))
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
