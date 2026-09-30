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


def _current_evidence(home, values):
    """Check current references only; historical artifacts stay in the audit.

    Missing, changed, or oversized files are explicit UNKNOWN inputs. Content is
    never clipped and paths/versions bind the checked result independently of prose.
    """
    result = []
    seen = set()
    for value in values:
        path, expected = value.get("evidence"), value.get("evidence_sha256")
        if not isinstance(path, str) or not path or (path, expected) in seen:
            continue
        seen.add((path, expected))
        target = Path(path)
        if not target.is_absolute():
            target = Path(home) / target
        row = dict(path=str(target), expected_sha256=expected, integrity="unavailable")
        try:
            if target.stat().st_size > 65536:
                raise ValueError("current evidence exceeds complete-content admission budget; supply a bounded checked artifact")
            data = target.read_bytes()
            row["actual_sha256"] = hashlib.sha256(data).hexdigest()
            row["integrity"] = "matched" if expected and expected == row["actual_sha256"] else "mismatch" if expected else "current-version-read"
            row["text"] = data.decode("utf-8")
            row["version_verified"] = bool(expected and row["integrity"] == "matched")
        except (OSError, ValueError, UnicodeError) as exc:
            row["error"] = str(exc)
        result.append(row)
    return result


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
    # Large audit/transaction fields belong only to the questions that need
    # them. This is semantic scoping, not truncation of any selected evidence.
    transaction = meaningful_proposal.pop("transaction", None)
    admitted_handoff = meaningful_proposal.pop("admitted_handoff_text", None)
    observation_details = {}
    if body.startswith('seed observation '):
        observation_details = {key: meaningful_proposal.pop(key) for key in ('snapshot', 'previous') if key in meaningful_proposal}
    current_values = [value for value in (state, dependency, context, proposed_state) if isinstance(value, dict)]
    raw_transaction = context.get("transaction", {})
    if isinstance(raw_transaction, dict):
        before = raw_transaction.get("before", {})
        if isinstance(before, dict):
            path = before.get("handoff_source") or before.get("handoff")
            digest = before.get("handoff_source_sha256") or before.get("handoff_sha256")
            if path:
                current_values.append(dict(evidence=path, evidence_sha256=digest))
    checked_evidence = _current_evidence(home, current_values)
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
    question_episodes['R06']['current_evidence'] = checked_evidence
    question_episodes['R06']['proposed'] = dict(meaningful_proposal, **observation_details)
    if observation_details:
        question_episodes['R08']['observation_comparison'] = observation_details
    event_receipt = bool(re.match(r'^(?:seed (?:observation|wake|yield|clear|redeliver)\b|\[(?:work|task(?:-[\w-]+)?|taking|done|dropped)\])', body))
    record_claim = any(line.startswith('[record] ') for line in body.splitlines())
    question_episodes['R06']['structured_event_applicability'] = dict(
        applicable=bool(context) or event_receipt or record_claim, supplied=bool(context),
        reason='Known event receipts/immutable-record claims require supplied event facts. Ordinary prose with no supplied event has no structured event to compare; that is absence of applicability, not proof of its factual claims.',
        review_scope='For ordinary prose inspect internal consistency and supplied canonical history for contradictions; do not invent a current event or require an unrelated event payload. A known event claim with missing relevant facts must remain unknown.')
    recent_events = view['events'][-8:]
    if last_source and last_source not in recent_events:
        recent_events = [last_source] + recent_events
    question_episodes['R06']['recent_canonical_events'] = [dict(sequence=e['sequence'], source=e['source'], body=e['body'], payload=_meaningful(e['payload'])) for e in recent_events]
    question_episodes['R06']['history_scope'] = dict(included_sequences=[e['sequence'] for e in recent_events], total_events=len(entries),
        meaning='Latest eight canonical events plus latest same-source event, with complete selected text/payload. Historical claims do not independently prove current facts or a different source version.')
    question_episodes['R06']['evidence_references'] = sorted(set(compact_references + [f"chat.log sequence {e['sequence']}" for e in recent_events]))
    if transaction:
        for key in ('R06', 'P16'):
            # P16 is attached after the pitfall base projection below.
            if key in question_episodes:
                question_episodes[key]['transaction'] = transaction
        question_episodes['R06']['transaction_semantics'] = 'Prepared guards are future admission conditions, not observed effects. Verified postconditions are current caller checks and must match the claimed receipt.'
    if admitted_handoff is not None:
        question_episodes['R06']['admitted_handoff_text'] = admitted_handoff
    question_episodes['R08']['previous_same_source'] = [last_source['body']] if last_source else []
    if (identity and proposed_state.get('identity') == identity and 'status' in proposed_state
            and re.match(r'^\[(?:task-state|task-add|task-close|task-reopen|task-claim)\]', body)):
        current_task = _semantic(state or {})
        proposed_task = _semantic(proposed_state)
        registration = next((event for event in view['events'] if state and event['sequence'] == state['sequence']), None)
        transition = dict(current=current_task, proposed=proposed_task,
                          changed_fields=sorted(key for key in set(current_task) | set(proposed_task)
                                                if current_task.get(key) != proposed_task.get(key)),
                          registration=registration,
                          semantics='This canonical control proposes the recorded task transition; publication commits it. Compare the current registration with the proposal, independently of earlier ordinary prose. A field or hash change alone does not establish meaningful progress or prove an external outcome; inspect the supplied checked evidence.')
        for key in ('R06', 'R08'):
            question_episodes[key]['task_transition'] = transition
    signal = re.match(r'^\[task-event\]\s+(\S+)', body)
    if signal and view['context_complete']:
        from .task_state import eligible
        by_sequence = {event['sequence']: event for event in view['events']}
        retry_targets = []
        for target in registry(entries).values():
            if target.status != 'waiting' or target.retry_event != signal[1]:
                continue
            registration = by_sequence[target.sequence]
            retry_targets.append(dict(task=_semantic(asdict(target)),
                registration=dict(sequence=registration['sequence'], source=registration['source'],
                                  body=registration['body'], payload=_meaningful(registration['payload'])),
                eligible_before_post=eligible(target, entries)))
        for key in ('R06', 'R08'):
            question_episodes[key]['retry_targets'] = retry_targets
            question_episodes[key]['retry_semantics'] = (
                'These are current canonical waits for this exact event, including their complete registration '
                'even when it lies outside recent chat. An event after the wait releases eligibility for an attempt; '
                'it does not prove work started. Already eligible targets do not gain a new retry from repeated notices.')
            question_episodes[key]['evidence_references'] = sorted(set(
                question_episodes[key]['evidence_references'] +
                [f"chat.log sequence {target['registration']['sequence']}" for target in retry_targets]))
    task_context = dict(context_complete=complete, evidence_references=compact_references,
                        task=_semantic(state or {}), producer=_semantic(dependency or {}),
                        proposed=meaningful_proposal, task_applicable=bool(identity), new_task_declaration=declaring)
    for number in range(1, 29):
        question_episodes[f'P{number:02d}'] = dict(task_context)
    question_episodes['P16']['current_evidence'] = checked_evidence
    if transaction:
        question_episodes['P16']['transaction'] = transaction
    if admitted_handoff is not None:
        question_episodes['P16']['admitted_handoff_text'] = admitted_handoff
    for key in ('P17', 'P18', 'P19', 'P20', 'P21', 'P23', 'P24', 'P27'):
        question_episodes[key]['current_evidence'] = checked_evidence
    if admitted_handoff is not None:
        question_episodes['P27']['admitted_handoff_text'] = admitted_handoff
    authorization = dict(role=source, current_task_owner=state.get('owner') if state else None,
                         scope_note='A charter supplies responsibility and granted local scope; mutation path ownership must still be named explicitly. A read-only proposal needs no mutation claim.')
    charter = Path(home) / 'charters' / (source + '.md')
    if charter.is_file():
        authorization['charter_evidence'] = _current_evidence(home, [dict(evidence=str(charter))])
    for key in ('P03', 'P13', 'P14'):
        question_episodes[key]['authorization_context'] = authorization
        question_episodes[key]['current_evidence'] = checked_evidence
    recent = semantic_receipts[-3:]
    for key in ('P08', 'P17', 'P18', 'P23', 'P24', 'P26', 'P27'):
        question_episodes[key]['recent_attempts'] = recent
        question_episodes[key]['history_scope'] = {'total_receipts': len(semantic_receipts), 'included_receipts': len(recent), 'purpose': 'last three task attempts, with no clipping of their content'}
    findings = anomalies(entries)
    for key in ('P01','P04','P05','P06','P21'):
        question_episodes[key]['deterministic_findings'] = [f for f in findings if f['task'] == identity]
    children = [_semantic(s) for s in view['tasks'].values() if s.get('parent') == identity] if identity else []
    question_episodes['P21']['children'] = children
    declarations = [e['body'] for e in relevant if identity and re.match(r'\[(?:task|task-add)\]\s+' + re.escape(identity) + r'(?:\s|$)', e['body'])]
    for key in ('P09', 'P10', 'P12', 'P21', 'P27', 'P28'):
        question_episodes[key]['task_declarations'] = declarations
    question_episodes['P28']['cause_identity'] = dict(canonical_task=identity,
        recorded_task_exists=bool(state), declaration_count=len(declarations),
        interpretation='A continuation of this canonical finding retains its identity. A new task still requires comparison against existing related findings; this fact does not declare semantic uniqueness.')
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
