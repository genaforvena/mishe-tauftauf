import json
from dataclasses import asdict
from types import SimpleNamespace

from mishe_tauftauf.feed import Feed, FeedEntry
from mishe_tauftauf.seed_witness_view import render
from mishe_tauftauf.task_state import TaskState


def wire(tmp_path, monkeypatch, entries):
    import mishe_tauftauf.seed_witness_view as view
    monkeypatch.setenv('MISHE_SEED_SESSION', 'test')
    monkeypatch.setattr(view.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(Feed, 'entries', lambda self: entries)


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
    monkeypatch.setattr(Feed, 'entries', lambda self: (_ for _ in ()).throw(ValueError('broken canonical frame')))
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
