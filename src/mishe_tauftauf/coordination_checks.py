"""Auditable task episodes and deterministic cross-role coordination findings.

The compact semantic view selects fields, never clips text. Full records remain
in the report; missing/corrupt committed context explicitly makes it incomplete.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from .feed import Feed
from .records import payload
from .task_state import registry


def _data(entry):
    lines = entry.body.splitlines()
    if any(line.startswith('[record]') for line in lines) or (len(lines) > 1 and lines[1].lstrip().startswith('{')):
        return payload(entry)
    if entry.body.startswith('[work] '):
        return dict(re.findall(r'(\w+)=([^\s]+)', lines[0]))
    return {}


def project(entries):
    """Keep terminal producers, all-role receipts, and parse errors visible."""
    errors, events, receipts = [], [], {}
    try:
        tasks = {key: asdict(value) for key, value in registry(entries).items()}
    except (ValueError, TypeError, KeyError) as exc:
        tasks = {}
        errors.append({'sequence': None, 'message': str(exc)})
    by_sequence = {}
    for entry in entries:
        try:
            data = _data(entry)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append({'sequence': entry.sequence, 'message': str(exc)})
            data = {}
        event = dict(sequence=entry.sequence, source=entry.source, body=entry.body, payload=data)
        events.append(event)
        by_sequence[entry.sequence] = event
        if entry.body.startswith('[work] '):
            referenced = data.get('task_state')
            try:
                prior = by_sequence.get(int(referenced)) if referenced is not None else None
            except (ValueError, TypeError):
                prior = None
            if referenced is not None and (prior is None or prior['payload'].get('identity') != data.get('task')):
                errors.append({'sequence': entry.sequence, 'message': 'Receipt task_state reference is missing or belongs to another task.'})
            elif prior:
                # The exact historical state, not the task's current state.
                event['task_state'] = prior['payload']
            if '\nHANDOFF:\n' in entry.body:
                event['handoff'] = entry.body.split('\nHANDOFF:\n', 1)[1]
            identity = data.get('task')
            if identity:
                receipts.setdefault(identity, []).append(event)
    return dict(tasks=tasks, events=events, receipts=receipts, errors=errors, context_complete=not errors)


def _finding(task, kind, severity, message, sequences, evidence=None):
    identity = f'{kind}:{task or "feed"}'
    return dict(id=identity, task=task, kind=kind, severity=severity, message=message,
                sequences=sorted(set(s for s in sequences if s is not None)), evidence=evidence or [])


def anomalies(entries):
    view = project(entries)
    findings = [_finding(None, 'invalid-context', 'UNKNOWN', error['message'], [error['sequence']]) for error in view['errors']]
    tasks = view['tasks']
    for identity, state in tasks.items():
        if state['status'] in {'done', 'dropped'}:
            continue
        producer = state.get('retry_task')
        if producer:
            dependency = tasks.get(producer)
            if dependency is None:
                findings.append(_finding(identity, 'missing-producer', 'RED', f'Producer {producer} has no canonical task.', [state['sequence']]))
            elif dependency['status'] == 'dropped':
                findings.append(_finding(identity, 'dropped-producer', 'RED', f'Producer {producer} was dropped; reconcile this prerequisite.', [state['sequence'], dependency['sequence']]))
            elif dependency['status'] == 'done':
                findings.append(_finding(identity, 'completed-producer', 'RED', f'Producer {producer} is complete; reconcile its evidence before another wait.', [state['sequence'], dependency['sequence']], [dependency.get('evidence', '')]))
            chain, cursor = [], identity
            while cursor in tasks and cursor not in chain:
                chain.append(cursor)
                cursor = tasks[cursor].get('retry_task')
            if cursor in chain:
                findings.append(_finding(identity, 'dependency-cycle', 'RED', 'Dependency cycle: ' + ' -> '.join(chain + [cursor]), [tasks[t]['sequence'] for t in chain]))
        if state['status'] == 'ready' and re.match(r'\s*(when|once|after|if|until)\b', state['next_step'], re.I):
            findings.append(_finding(identity, 'conditional-ready', 'SUSPICIOUS', 'Ready step describes a condition; verify that its prerequisite holds.', [state['sequence']]))
    # Claims survive until their exact yield, independent of intervening tasks.
    claims = {}
    for event in view['events']:
        body, data = event['body'], event['payload']
        if body.startswith('[task-claim] ') and data.get('identity') and data.get('attempt_wake'):
            key = data['identity']
            prior = claims.get(key)
            current = (data.get('owner'), data['attempt_wake'], event['sequence'])
            if prior and prior[:2] != current[:2]:
                findings.append(_finding(key, 'double-claim', 'RED', 'Two unsettled wakes claim this task.', [prior[2], current[2]]))
            claims[key] = current
        elif match := re.match(r'seed yield (\S+) wake=(\d+)', body):
            claims = {key: claim for key, claim in claims.items() if claim[:2] != (match[1], int(match[2]))}
    for identity, history in view['receipts'].items():
        recent = history[-3:]
        if len(recent) < 3:
            continue
        data = [{**event.get('task_state', {}), **event['payload']} for event in recent]
        # Observation/step/archive hashes do not establish prerequisite progress.
        fields = ('reason', 'progress', 'retry_event', 'retry_task', 'retry_at', 'prerequisite_evidence')
        signatures = [json.dumps({k: item.get(k) for k in fields}, sort_keys=True) for item in data]
        meaningful = any(data[0].get(k) for k in fields)
        if meaningful and len(set(signatures)) == 1 and all(item.get('result') in {'verified', 'blocked', 'unspecified'} for item in data):
            findings.append(_finding(identity, 'repeated-prerequisite', 'RED', 'Three receipts repeat the same structured prerequisite and outcome; investigate before repeating.', [event['sequence'] for event in recent], [item.get('archive', '') for item in data]))
        elif all(item.get('result') in {'verified', 'blocked', 'unspecified'} for item in data):
            current = tasks.get(identity, {})
            if (current.get('status') == 'done' and current.get('evidence')
                    and re.fullmatch(r'[0-9a-f]{64}', current.get('evidence_sha256', ''))):
                # A checked terminal outcome retires this semantic candidate;
                # all historical receipts remain available to private analysis.
                continue
            findings.append(_finding(identity, 'repeated-attempt-review', 'SUSPICIOUS', 'Three task attempts need semantic review of actual progress and prerequisites; hashes and observation labels alone cannot decide usefulness.', [event['sequence'] for event in recent], [item.get('archive', '') for item in data]))
    return findings


def _semantic(state):
    fields = ('identity', 'owner', 'status', 'next_step', 'reason', 'progress', 'retry_task', 'retry_event', 'retry_at', 'activity')
    return {key: state[key] for key in fields if key in state}


def _meaningful(data):
    """Opaque transport fields stay in the private report, not model input."""
    omitted = {'evidence', 'archive', 'offer_evidence', 'sequence', 'task_state', 'task_step',
               'task_outcome', 'observation', 'attempt_observation', 'handoff_sha256'}
    return {key: (_meaningful(value) if isinstance(value, dict) else value)
            for key, value in data.items() if key not in omitted and not key.endswith('_sha256')}


def episode(home, source, body, context=None, identity=None):
    """Build full evidence plus unclipped question inputs for private gates."""
    context = dict(context or {})
    feed = Feed(Path(home))
    entries = feed.entries()
    view = project(entries)
    identity = identity or context.get('task') or context.get('identity')
    if not identity:
        match = re.match(r'\[(?:task|taking|done|dropped|task-state|task-claim|task-close|task-add|task-reopen)\]\s+(\S+)', body)
        identity = match[1] if match else None
    state = view['tasks'].get(identity)
    declaring = bool(re.match(r'\[(task|task-add)\]\s+', body)) and state is None
    proposed_state = context.get('state') if isinstance(context.get('state'), dict) else context
    dependency_id = proposed_state.get('retry_task') or (state.get('retry_task') if state else None)
    dependency = view['tasks'].get(dependency_id)
    relevant = []
    last_source = None
    identities = {value for value in (identity, dependency_id) if value}
    for event in view['events']:
        data = event['payload']
        if event['source'] == source:
            last_source = event
        if (data.get('identity') in identities or data.get('task') in identities
                or any(re.search(r'(?<!\S)' + re.escape(value) + r'(?!\S)', event['body']) for value in identities)):
            relevant.append(event)
    if last_source and last_source not in relevant:
        relevant.append(last_source)
        relevant.sort(key=lambda event: event['sequence'])
    references = sorted({value for event in relevant for key, value in event['payload'].items() if key in {'evidence', 'archive', 'offer_evidence'} and isinstance(value, str) and value})
    complete = view['context_complete'] and (not identity or state is not None or declaring) and (not dependency_id or dependency is not None)
    meaningful_proposal = _meaningful(context)
    if state and isinstance(context.get('state'), dict) and _semantic(context['state']) == _semantic(state):
        meaningful_proposal.pop('state', None)
    semantic_receipts = []
    for event in view['receipts'].get(identity, []):
        data = {**event.get('task_state', {}), **event['payload']}
        receipt = {k: data[k] for k in ('channel','result','next_step','reason','progress','retry_task','retry_event','prerequisite_evidence') if k in data}
        receipt['sequence'] = event['sequence']
        if event.get('handoff'):
            receipt['handoff'] = event['handoff']
        semantic_receipts.append(receipt)
    semantic = dict(task=_semantic(state or {}), producer=_semantic(dependency or {}), proposed=meaningful_proposal,
                    context_complete=complete, evidence_references=references,
                    receipts=semantic_receipts)
    # Readability questions inspect the draft supplied separately by the worker.
    # Each narrow episode carries only the facts needed for that question.
    question_episodes = {}
    current_sequences = {s['sequence'] for s in (state, dependency) if s}
    if last_source:
        current_sequences.add(last_source['sequence'])
    compact_references = [f'chat.log sequence {number}' for number in sorted(current_sequences)]
    event_context = dict(context_complete=complete, evidence_references=compact_references,
                         proposed=meaningful_proposal, task_identity=identity)
    for number in range(1, 9):
        question_episodes[f'R{number:02d}'] = dict(event_context)
    question_episodes['R08']['previous_same_source'] = [last_source['body']] if last_source else []
    task_context = dict(context_complete=complete, evidence_references=compact_references,
                        task=_semantic(state or {}), producer=_semantic(dependency or {}),
                        proposed=meaningful_proposal, task_applicable=bool(identity), new_task_declaration=declaring)
    for number in range(1, 29):
        question_episodes[f'P{number:02d}'] = dict(task_context)
    recent = semantic_receipts[-3:]
    for key in ('P08', 'P17', 'P18', 'P23', 'P24', 'P26', 'P27'):
        question_episodes[key]['recent_attempts'] = recent
        question_episodes[key]['history_scope'] = {'total_receipts': len(semantic_receipts), 'included_receipts': len(recent), 'purpose': 'last three task attempts, with no clipping of their content'}
    findings = anomalies(entries)
    for key in ('P01','P04','P05','P06','P21'):
        question_episodes[key]['deterministic_findings'] = [f for f in findings if f['task'] == identity]
    children = [_semantic(s) for s in view['tasks'].values() if s.get('parent') == identity] if identity else []
    question_episodes['P21']['children'] = children
    declarations = [e['body'] for e in relevant if identity and re.match(r'\[task\]\s+' + re.escape(identity) + r'(?:\s|$)', e['body'])]
    for key in ('P09', 'P10', 'P12', 'P21', 'P27'):
        question_episodes[key]['task_declarations'] = declarations
    available = [s for s in view['tasks'].values() if s['status'] == 'ready' and s['identity'] != identity]
    # A ready count establishes possible alternatives, never their usefulness.
    # Keep the question small on a long-running plant; usefulness still needs
    # source inspection rather than model inference from opaque task names.
    question_episodes['P26']['alternative_work'] = dict(ready_task_count=len(available),
        useful_alternative_verified=False, scope='canonical ready declarations; reservations and semantic usefulness require verification')
    # Bind omitted unrelated history by canonical entry content, path and cutoff.
    # The digest is explicitly of decoded entries, not claimed as raw file bytes.
    canonical_digest = hashlib.sha256()
    for entry in entries:
        canonical_digest.update(json.dumps([entry.sequence,entry.timestamp,entry.source,entry.body], ensure_ascii=False, separators=(',',':')).encode())
        canonical_digest.update(b'\n')
    projection = dict(tasks={key: value for key, value in view['tasks'].items() if key in identities},
                      errors=view['errors'], context_complete=view['context_complete'],
                      task_count=len(view['tasks']), event_count=len(entries),
                      source_log=str(feed.path), cutoff=entries[-1].sequence if entries else 0,
                      canonical_entries_sha256=canonical_digest.hexdigest(),
                      digest_format='SHA256 of UTF-8 JSON [sequence,timestamp,source,body] with compact separators, ensure_ascii=false, followed by LF for every entry through cutoff')
    return dict(source=source, identity=identity, draft=body, proposed=context, task=state,
                producer=dependency, history=relevant, projection=projection, anomalies=findings,
                context_complete=complete, evidence_references=references, semantic_episode=semantic,
                question_episodes=question_episodes)
