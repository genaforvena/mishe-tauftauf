import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from mishe_tauftauf.feed import Feed, FeedEntry
from mishe_tauftauf.seed_witness_view import render
from mishe_tauftauf.task_state import TaskState


def wire(tmp_path, monkeypatch, entries):
    import mishe_tauftauf.seed_witness_view as view
    monkeypatch.setenv('MISHE_SEED_SESSION', 'test')
    monkeypatch.setattr(view.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(Feed, 'entries', lambda self, **kwargs: entries)


def entry(seq, source, body):
    return FeedEntry(seq, '2026-09-30T00:00:00Z', source, body)


def test_witness_displays_own_and_seed_task_receipt_history(tmp_path, monkeypatch):
    entries = [entry(1, 'witness', '[task] audit owner=witness acceptance=checked')]
    for seq in (2,3,4):
        data = {'channel':'witness','task':'audit','result':'verified','reason':'CI unchanged','retry_event':'ci'}
        entries.append(entry(seq, 'seed', '[work] channel=witness wake=1 observation=2 result=verified continue=0\n' + json.dumps(data)))
    wire(tmp_path, monkeypatch, entries)
    output = render(tmp_path)
    assert 'COORDINATION: RED' in output
    assert 'task=audit' in output and 'repeated-prerequisite' in output
    assert 'sequences=2,3,4' in output
    assert '1 witness:' in output
    assert '4 seed:' in output


def test_genome_work_names_current_settlements_not_retired_receipts(tmp_path, monkeypatch):
    entries = [entry(1, 'seed', '[work] channel=genome wake=9875 observation=9862 result=changed continue=0')]
    seq = 2
    for observation in (7, 8, 9):
        entries.append(entry(seq, 'seed', f'seed wake genome observation={observation}\nRead your wall.'))
        entries.append(entry(seq + 1, 'seed', f'seed yield genome wake={seq}\nTurn settled (verified); wall and handoff.'))
        entries.append(entry(seq + 2, 'seed', f'seed clear genome after={seq}\nIdle mind rotated.'))
        seq += 3
    wire(tmp_path, monkeypatch, entries)
    output = render(tmp_path)
    genome_work = next(line for line in output.splitlines() if line.startswith('GENOME WORK:'))
    assert genome_work == 'GENOME WORK: 2:verified@7, 5:verified@8, 8:verified@9'


def test_conditional_ready_is_suspicion_with_unknown_health(tmp_path, monkeypatch):
    state = asdict(TaskState('audit', 'witness', 2, next_step='When producer reports ownership, inspect latency.'))
    entries = [entry(1,'witness','[task] audit owner=witness acceptance=checked'), entry(2,'witness','[task-state] audit\n'+json.dumps(state))]
    wire(tmp_path, monkeypatch, entries)
    output = render(tmp_path)
    assert 'COORDINATION: SUSPICIOUS' in output
    assert 'conditional-ready' in output
    assert 'STATE: UNKNOWN' in output
    assert 'COORDINATION: RED' not in output


def test_corrupt_receipt_does_not_crash_or_render_green(tmp_path, monkeypatch):
    entries = [entry(1,'seed','[work] channel=witness wake=1 observation=2 result=verified continue=0\n{bad')]
    wire(tmp_path, monkeypatch, entries)
    output = render(tmp_path)
    assert 'COORDINATION: UNKNOWN' in output
    assert 'invalid-context' in output
    assert 'STATE: UNKNOWN' in output


def test_private_gate_refusal_visible_without_draft_or_inference(tmp_path, monkeypatch):
    import mishe_tauftauf.post_check as checks
    wire(tmp_path, monkeypatch, [])
    monkeypatch.setattr(checks, 'review', lambda *a, **k: (_ for _ in ()).throw(AssertionError('renderer invoked inference')))
    (tmp_path / 'publication-check.json').write_text(json.dumps({'command':['worker']}))
    private = tmp_path / 'post-checks'
    private.mkdir()
    (private / 'review.json').write_text(json.dumps({'status':'suspicious','semantic_status':'suspicious','source':'witness','stage':'handoff','body':'PRIVATE DRAFT MUST STAY PRIVATE','results':[{'id':'P18','verdict':'suspicious'}]}))
    output = render(tmp_path)
    assert 'PUBLICATION GATE: CONFIGURED' in output
    assert 'PUBLICATION RESULT: REFUSED source=witness stage=handoff' in output
    assert 'Correction questions: P18' in output
    assert 'PRIVATE DRAFT MUST STAY PRIVATE' not in output
    assert 'STATE: UNKNOWN' in output


def test_unreadable_feed_keeps_coordination_unknown(tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, [])
    monkeypatch.setattr(Feed, 'entries', lambda self, **kwargs: (_ for _ in ()).throw(ValueError('broken canonical frame')))
    output = render(tmp_path)
    assert 'COORDINATION: UNKNOWN' in output
    assert 'broken canonical frame' in output
    assert 'STATE: UNKNOWN' in output


def test_unconfigured_gate_is_explicitly_untested(tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, [])
    output = render(tmp_path)
    assert 'PUBLICATION GATE: UNTESTED' in output
    assert 'PUBLICATION RESULT: UNTESTED' in output
    assert 'STATE: UNKNOWN' in output


def test_witness_dispatch_observes_semantic_state_changes():
    from mishe_tauftauf.wall import observation_text
    base = "COORDINATION: UNKNOWN\nANOMALY: SUSPICIOUS task=repair kind=repeated-attempt id=stable\nPUBLICATION GATE: CONFIGURED\nPUBLICATION RESULT: CLEAR semantic=clear\n  Evidence: private report=first.json\nSTATE: UNKNOWN"
    assert observation_text('witness', base) != observation_text('witness', base.replace('id=stable','id=new-cause'))
    assert observation_text('witness', base) != observation_text('witness', base.replace('CLEAR semantic=clear','REFUSED source=genome stage=handoff semantic=suspicious'))


def test_overdue_clear_is_held_not_failed_while_a_live_mind_is_busy(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: False)
    output = render(tmp_path)
    assert 'CLEAR STALL: HELD audit wake=1 yield=2' in output
    assert 'CLEAR STALL: RED' not in output
    assert 'overdue clear needs checked supervisor repair' not in output


def test_overdue_clear_fails_when_the_mind_is_idle(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: True)
    output = render(tmp_path)
    assert 'CLEAR STALL: RED audit wake=1 yield=2 owner=health' in output
    assert 'STATE: RED' in output
    verdict = (tmp_path / 'observations' / 'witness').read_text()
    assert 'FAIL witness overdue clear audit needs checked supervisor repair' in verdict


def test_overdue_clear_stays_a_fault_without_a_session(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setenv('MISHE_SEED_SESSION', '')
    monkeypatch.setattr(seed, '_mind_idle',
                        lambda session, slug: (_ for _ in ()).throw(AssertionError('probe without session')))
    output = render(tmp_path)
    assert 'CLEAR STALL: RED audit wake=1 yield=2 owner=health' in output
    assert 'STATE: RED' in output


def test_overdue_clear_stays_a_fault_without_a_live_mind_pane(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: False)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: False)
    output = render(tmp_path)
    assert 'CLEAR STALL: HELD' not in output
    assert 'CLEAR STALL: RED audit wake=1 yield=2 owner=health' in output
    assert 'STATE: RED' in output


def test_stale_pend_is_held_not_failed_while_a_live_mind_is_busy(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: False)
    output = render(tmp_path)
    assert 'STALE PEND: HELD audit wake=1 pending=1' in output
    assert 'STALE PEND: RED' not in output
    assert 'overdue pending wake needs checked supervisor repair' not in output


def test_stale_pend_fails_when_the_mind_is_idle(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: True)
    output = render(tmp_path)
    assert 'STALE PEND: RED audit wake=1 pending=1 owner=health' in output
    assert 'STATE: RED' in output
    verdict = (tmp_path / 'observations' / 'witness').read_text()
    assert 'FAIL witness overdue pending wake audit needs checked supervisor repair' in verdict


def test_stale_pend_stays_a_fault_without_a_live_mind_pane(tmp_path, monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    entries = [entry(1, 'seed', 'seed wake audit observation=1')]
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: False)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: False)
    output = render(tmp_path)
    assert 'STALE PEND: HELD' not in output
    assert 'STALE PEND: RED audit wake=1 pending=1 owner=health' in output
    assert 'STATE: RED' in output

def test_render_output_is_identical_with_and_without_the_shared_payload_map(tmp_path, monkeypatch):
    import mishe_tauftauf.seed_witness_view as view
    monkeypatch.setenv('MISHE_SEED_SESSION', 'test')
    monkeypatch.setattr(view.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))
    Feed(tmp_path).append_record('witness', 'Checked evidence.', {'checked': True})
    Feed(tmp_path).append('seed', '[work] channel=witness wake=1 observation=2 result=verified continue=0')
    with_map = view.render(tmp_path)
    real_anomalies = view.anomalies
    monkeypatch.setattr(view, 'anomalies', lambda entries, **kwargs: real_anomalies(entries))
    without_map = view.render(tmp_path)
    assert with_map == without_map


def test_live_mind_pane_accepts_every_ready_engine(monkeypatch):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view

    def pane(value):
        return lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=value.encode())

    for engine in ('omp', 'codex', 'opencode'):
        monkeypatch.setattr(seed, '_tmux', pane(f'0 {engine}'))
        assert view._mind_pane_live('session', 'docs')
    monkeypatch.setattr(seed, '_tmux', pane('1 opencode'))
    assert not view._mind_pane_live('session', 'docs')
    monkeypatch.setattr(seed, '_tmux', pane('0 bash'))
    assert not view._mind_pane_live('session', 'docs')


def wedge_scan(tmp_path, suspects, created=None):
    """Publish a discovery scan carrying a `sense.mind.wedge-suspect` reading."""
    from datetime import datetime, timezone
    directory = tmp_path / 'discovery'
    directory.mkdir(exist_ok=True)
    created = created or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    (directory / 'scan-20260930T000000Z-aaaa.json').write_text(json.dumps({
        'created': created,
        'observations': [{'id': 'sense.mind.wedge-suspect', 'state': 'verified',
                          'suspects': suspects}]}), encoding='utf-8')

def busy_mind(tmp_path, monkeypatch, entries):
    import mishe_tauftauf.seed as seed
    import mishe_tauftauf.seed_witness_view as view
    wire(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(view, '_mind_pane_live', lambda session, role: True)
    monkeypatch.setattr(view, '_mind_pane_pid', lambda session, role: 1)
    monkeypatch.setattr(seed, '_mind_idle', lambda session, slug: False)


def test_wedged_mind_is_not_rendered_as_a_turn_in_progress(tmp_path, monkeypatch):
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    busy_mind(tmp_path, monkeypatch, entries)
    wedge_scan(tmp_path, [{'window': 'audit', 'pid': 1, 'chain': 12, 'span_minutes': 110.2,
                           'sources': {'automatic-retry': 10, 'stream-stall-continue': 2},
                           'cause': 'provider-error:500'}])
    output = render(tmp_path)
    assert ('CLEAR STALL: WEDGE-SUSPECT audit wake=1 yield=2 chain=12 span=110.2min '
            'src=automatic-retry:10,stream-stall-continue:2 cause=provider-error:500') in output
    assert 'CLEAR STALL: HELD' not in output
    assert 'STATE: RED' in output
    assert (tmp_path / 'observations' / 'witness').read_text() == \
        'FAIL witness wedge-suspect mind audit needs checked recovery\n'


def test_wedged_mind_is_not_rendered_as_a_pending_turn(tmp_path, monkeypatch):
    entries = [entry(1, 'seed', 'seed wake audit observation=1')]
    busy_mind(tmp_path, monkeypatch, entries)
    wedge_scan(tmp_path, [{'window': 'audit', 'pid': 1, 'chain': 12, 'span_minutes': 22.5,
                           'sources': {'automatic-retry': 12}, 'cause': 'provider-error:500'}])
    output = render(tmp_path)
    assert ('STALE PEND: WEDGE-SUSPECT audit wake=1 pending=1 chain=12 span=22.5min '
            'src=automatic-retry:12 cause=provider-error:500') in output
    assert 'STALE PEND: HELD' not in output
    assert 'STATE: RED' in output


def test_provider_error_suspect_is_reported_without_asserting_a_wedge(tmp_path, monkeypatch):
    # The provider-error rule also fires on a pane still streaming a long turn,
    # so the pane reports it on the HELD line and does not claim a wedge.
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    busy_mind(tmp_path, monkeypatch, entries)
    wedge_scan(tmp_path, [{'window': 'audit', 'pid': 1, 'chain': 0, 'rule': 'provider-error',
                           'error_age_minutes': 8.4, 'cause': 'provider-error:403'}])
    output = render(tmp_path)
    assert 'CLEAR STALL: HELD audit' in output
    assert 'wedge-suspect=rule=provider-error' in output
    assert 'WEDGE-SUSPECT' not in output
    assert 'STATE: RED' not in output


def test_wedge_suspect_for_another_sessions_pane_is_not_surfaced(tmp_path, monkeypatch):
    # The sense enumerates every pane on the tmux server and plants share role
    # window names, so only a pid match may name this session's pane.
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    busy_mind(tmp_path, monkeypatch, entries)
    wedge_scan(tmp_path, [{'window': 'audit', 'pid': 4242, 'chain': 12, 'span_minutes': 110.2,
                           'sources': {'automatic-retry': 10}, 'cause': 'provider-error:500'}])
    output = render(tmp_path)
    assert 'CLEAR STALL: HELD audit wake=1 yield=2' in output
    assert 'WEDGE-SUSPECT' not in output


def test_chain_suspect_with_unusable_sources_is_still_reported(tmp_path, monkeypatch):
    entries = [entry(1, 'seed', 'seed wake audit observation=1'),
               entry(2, 'seed', 'seed yield audit wake=1')]
    busy_mind(tmp_path, monkeypatch, entries)
    wedge_scan(tmp_path, [{'window': 'audit', 'pid': 1, 'chain': 12, 'span_minutes': 110.2,
                           'sources': 5, 'cause': 'provider-error:500'}])
    output = render(tmp_path)
    assert ('CLEAR STALL: WEDGE-SUSPECT audit wake=1 yield=2 chain=12 span=110.2min '
            'src= cause=provider-error:500') in output


@pytest.fixture
def monitor_home(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import mishe_tauftauf.seed_witness_view as view

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 7, 10, tzinfo=timezone.utc)

    monkeypatch.setattr(view, 'datetime', Clock)
    busy_mind(tmp_path, monkeypatch, [entry(1, 'seed', 'seed wake audit observation=1')])
    monkeypatch.setattr(view.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout='audit\n'))
    (tmp_path / 'health').mkdir()
    (tmp_path / 'health' / 'windows.json').write_text('["audit"]')
    (tmp_path / 'publication-check.json').write_text('{"command": ["worker"]}')
    (tmp_path / 'post-checks').mkdir()
    (tmp_path / 'post-checks' / 'review.json').write_text(
        '{"status": "clear", "semantic_status": "clear"}')
    return tmp_path


def monitor_scan(created='2026-10-07T10:00:00Z', state='verified', suspects=None):
    return {'created': created, 'observations': [
        {'id': 'sense.mind.wedge-suspect', 'state': state, 'suspects': suspects or []}]}


@pytest.mark.parametrize('payload', [
    None,
    '{not json',
    '[]',
    json.dumps(monitor_scan(created='2026-10-07T10:00:01Z')),
    json.dumps(monitor_scan(created='2026-10-07T09:29:59Z')),
    json.dumps(monitor_scan(created='2026-10-07T10:00:00')),
    json.dumps(monitor_scan(state='unknown', suspects=[{'window': 'audit', 'pid': 1}])),
    '{"created": "2026-10-07T10:00:00Z", "observations": []}',
    '{"created": "2026-10-07T10:00:00Z", "observations": 5}',
    json.dumps(monitor_scan(suspects=[5])),
    json.dumps(monitor_scan(suspects=[{'window': 'audit', 'pid': True}])),
])
def test_unavailable_monitor_does_not_accuse_or_clear_busy_mind(monitor_home, payload):
    if payload is not None:
        (monitor_home / 'discovery').mkdir()
        (monitor_home / 'discovery' / 'scan-a.json').write_text(payload)
    output = render(monitor_home)
    assert 'MIND MONITOR: UNKNOWN' in output
    assert 'owner=health' in output
    assert 'STATE: UNKNOWN' in output
    assert 'STALE PEND: HELD audit' in output
    assert 'WEDGE-SUSPECT' not in output
    assert (monitor_home / 'observations' / 'witness').read_text().startswith('UNKNOWN')


@pytest.mark.parametrize('created', ['2026-10-07T10:00:00Z', '2026-10-07T09:30:00Z'])
def test_available_empty_aggregate_never_claims_role_coverage(monitor_home, created):
    wedge_scan(monitor_home, [], created=created)
    output = render(monitor_home)
    assert 'MIND MONITOR: AVAILABLE' in output
    assert 'per-role coverage UNKNOWN; useful progress not established' in output
    assert 'STATE: GREEN' in output
    assert 'STALE PEND: HELD audit' in output


def test_monitor_selects_created_time_not_filename(monitor_home):
    directory = monitor_home / 'discovery'
    directory.mkdir()
    (directory / 'scan-a.json').write_text(json.dumps(monitor_scan()))
    (directory / 'scan-z.json').write_text(json.dumps(monitor_scan(
        created='2026-10-07T09:55:00Z', suspects=[{'window': 'audit', 'pid': 1}])))
    output = render(monitor_home)
    assert 'MIND MONITOR: AVAILABLE' in output
    assert 'STATE: GREEN' in output
    assert 'WEDGE-SUSPECT' not in output


def test_ambiguous_newest_monitor_scan_cannot_accuse_mind(monitor_home):
    directory = monitor_home / 'discovery'
    directory.mkdir()
    (directory / 'scan-a.json').write_text(json.dumps(monitor_scan()))
    (directory / 'scan-z.json').write_text(json.dumps(monitor_scan(
        suspects=[{'window': 'audit', 'pid': 1}])))
    output = render(monitor_home)
    assert 'MIND MONITOR: UNKNOWN' in output
    assert 'STATE: UNKNOWN' in output
    assert 'WEDGE-SUSPECT' not in output


def test_missing_monitor_does_not_hide_independent_receipt_failure(monitor_home, monkeypatch):
    import mishe_tauftauf.seed_witness_view as view
    monkeypatch.setattr(view, '_mind_not_idle', lambda *args: False)
    output = render(monitor_home)
    assert 'MIND MONITOR: UNKNOWN' in output
    assert 'STALE PEND: RED audit' in output
    assert 'STATE: RED' in output
    assert (monitor_home / 'observations' / 'witness').read_text().startswith(
        'FAIL witness overdue pending wake audit')
