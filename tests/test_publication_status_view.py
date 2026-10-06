import json
import os
import time

from mishe_tauftauf.seed_witness_view import _publication_lines
from mishe_tauftauf.wall import observation_text


def prepare(home):
    (home / 'publication-check.json').write_text(json.dumps({'command': ['/checked/worker'], 'timeout_seconds': 295}))
    reports = home / 'post-checks'
    reports.mkdir()
    return reports


def test_inflight_review_changes_full_frame_observation_without_overwriting_result(tmp_path):
    reports = prepare(tmp_path)
    completed = reports / 'completed.json'
    completed.write_text(json.dumps({'status': 'clear', 'semantic_status': 'clear', 'stage': 'post', 'source': 'seed', 'results': []}))
    os.utime(completed, (time.time() - 10, time.time() - 10))
    before, uncertain_before = _publication_lines(tmp_path)
    (reports / 'pending.json').write_text(json.dumps({'semantic_status': 'untested', 'clear': False, 'stage': 'post', 'source': 'seed', 'worker_input_bytes': 500}))
    after, uncertain_after = _publication_lines(tmp_path)
    assert not uncertain_before and not uncertain_after
    assert any('PUBLICATION ACTIVE: PENDING' in line for line in after)
    assert any('PUBLICATION RESULT: CLEAR semantic=clear' in line for line in after)
    assert observation_text('witness', '\n'.join(before)) != observation_text('witness', '\n'.join(after))


def test_abandoned_inflight_review_is_unknown_without_overwriting_last_result(tmp_path):
    reports = prepare(tmp_path)
    (reports / 'completed.json').write_text(json.dumps({'status': 'clear', 'semantic_status': 'clear', 'results': []}))
    pending = reports / 'pending.json'
    pending.write_text(json.dumps({'semantic_status': 'untested', 'clear': False, 'stage': 'post', 'source': 'seed', 'worker_input_bytes': 500}))
    # Both timestamps must keep the abandoned pending file the latest report.
    os.utime(reports / 'completed.json', (time.time() - 500, time.time() - 500))
    os.utime(pending, (time.time() - 400, time.time() - 400))
    lines, uncertain = _publication_lines(tmp_path)
    assert uncertain
    assert any('PUBLICATION ACTIVE: UNKNOWN' in line for line in lines)
    assert any('PUBLICATION RESULT: CLEAR semantic=clear' in line for line in lines)


def test_pending_review_does_not_hide_latest_refusal(tmp_path):
    reports = prepare(tmp_path)
    completed = reports / 'completed.json'
    completed.write_text(json.dumps({'status': 'suspicious', 'semantic_status': 'suspicious', 'stage': 'post', 'source': 'witness', 'results': [{'id': 'R06', 'verdict': 'suspicious'}]}))
    os.utime(completed, (time.time() - 10, time.time() - 10))
    (reports / 'pending.json').write_text(json.dumps({'semantic_status': 'untested', 'clear': False, 'stage': 'post', 'source': 'seed', 'worker_input_bytes': 500}))
    lines, uncertain = _publication_lines(tmp_path)
    assert uncertain
    assert any('PUBLICATION RESULT: REFUSED source=witness stage=post' in line for line in lines)
    assert any('R06' in line for line in lines)


def test_prose_guard_refusal_is_not_a_standing_publication_result(tmp_path):
    reports = prepare(tmp_path)
    (reports / 'prose.json').write_text(json.dumps({'status': 'suspicious', 'semantic_status': 'not a publication gate', 'stage': 'prose', 'source': 'genome', 'results': [{'id': 'D01', 'verdict': 'suspicious'}]}))
    os.utime(reports / 'prose.json', (time.time(), time.time()))
    lines, uncertain = _publication_lines(tmp_path)
    assert not any('REFUSED' in line for line in lines)
    assert not any('stage=prose' in line for line in lines)
    # A newer prose report must not hide an older publication-gate refusal.
    gate = reports / 'gate.json'
    gate.write_text(json.dumps({'status': 'suspicious', 'semantic_status': 'suspicious', 'stage': 'post', 'source': 'witness', 'results': [{'id': 'R06', 'verdict': 'suspicious'}]}))
    os.utime(gate, (time.time() - 10, time.time() - 10))
    lines, uncertain = _publication_lines(tmp_path)
    assert any('PUBLICATION RESULT: REFUSED source=witness stage=post' in line for line in lines)
    assert uncertain


def test_retired_selection_refusal_is_not_a_standing_publication_result(tmp_path):
    reports = prepare(tmp_path)
    # The ledger claim path that produced stage=selection reviews is retired, so
    # its refusal can never be superseded and must not latch as the result.
    (reports / 'selection.json').write_text(json.dumps({'status': 'unknown', 'semantic_status': 'unknown', 'stage': 'selection', 'source': 'body-research', 'results': []}))
    os.utime(reports / 'selection.json', (time.time(), time.time()))
    lines, uncertain = _publication_lines(tmp_path)
    assert not any('stage=selection' in line for line in lines)
    # A newer retired-caller refusal must not hide an older live-caller refusal.
    gate = reports / 'gate.json'
    gate.write_text(json.dumps({'status': 'suspicious', 'semantic_status': 'suspicious', 'stage': 'post', 'source': 'witness', 'results': [{'id': 'R06', 'verdict': 'suspicious'}]}))
    os.utime(gate, (time.time() - 10, time.time() - 10))
    lines, uncertain = _publication_lines(tmp_path)
    assert any('PUBLICATION RESULT: REFUSED source=witness stage=post' in line for line in lines)
    assert uncertain
