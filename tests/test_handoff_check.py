from pathlib import Path

from mishe_tauftauf.feed import Feed


def test_next_repeat_identifies_prior_evidence_without_mutating_feed(tmp_path):
    from mishe_tauftauf.handoff_check import check
    Feed(tmp_path).append('genome', 'Next: Inspect the supervisor observation producer and verify the live genome chat rate.')
    before = (tmp_path / 'chat.log').read_bytes()
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('# Report\n\nNext: Inspect the supervisor observation producer and verify the live genome chat rate.\n')
    report = check(tmp_path, handoff)
    assert report['state'] == 'repeat'
    assert 'revise' in report['action']
    assert 'wording' in report['action']
    assert report['matches'][0]['sequence'] == 1
    assert (tmp_path / 'chat.log').read_bytes() == before


def test_markdown_next_step_and_short_shared_ids_do_not_false_match(tmp_path):
    from mishe_tauftauf.handoff_check import check
    Feed(tmp_path).append('genome', 'task-42 owner=genome test source')
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('## Next step\n\nVerify the newly deployed source and capture the advancing pane for task-42.\n\n## Evidence\nartifact')
    assert check(tmp_path, handoff)['state'] == 'clear'


def test_missing_next_step_is_unknown(tmp_path):
    from mishe_tauftauf.handoff_check import check
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('# Nothing to do\n')
    assert check(tmp_path, handoff)['state'] == 'unknown'


def test_status_exposes_repeat_and_invalidates_changed_handoff(tmp_path):
    import json
    from mishe_tauftauf.handoff_check import check, status
    Feed(tmp_path).append('genome', 'Inspect the supervisor observation producer and verify the live genome chat rate.')
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Next: Inspect the supervisor observation producer and verify the live genome chat rate.')
    report = check(tmp_path, handoff)
    (tmp_path / 'handoff-check.json').write_text(json.dumps(report))
    assert 'REPEAT' in status(tmp_path)
    handoff.write_text('Next: A changed action that needs to be checked again.')
    assert 'UNKNOWN' in status(tmp_path)


def test_repeat_detection_stops_at_evidence_heading_without_blank_line(tmp_path):
    from mishe_tauftauf.handoff_check import check
    step = 'Inspect the supervisor observation producer and verify the live genome chat rate.'
    Feed(tmp_path).append('genome', step)
    handoff = tmp_path / 'handoff.md'
    for text in ['Next: '+step+'\n## Evidence\nA new artifact', '- Next: '+step, '## Next / rollback\n'+step]:
        handoff.write_text(text)
        report = check(tmp_path, handoff)
        assert report['next_step'] == step
        assert report['state'] == 'repeat'


def test_malformed_status_is_unknown_not_a_renderer_exception(tmp_path):
    import json,hashlib
    from mishe_tauftauf.handoff_check import status
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Next: check the newly deployed optional advisor output on the live pane.')
    (tmp_path / 'handoff-check.json').write_text(json.dumps({'state': None, 'handoff_file': str(handoff), 'handoff_sha256': hashlib.sha256(handoff.read_bytes()).hexdigest(), 'match_count': 0, 'checked_through_sequence': 0}))
    assert 'UNKNOWN' in status(tmp_path)


def test_inline_next_after_evidence_is_checked(tmp_path):
    from mishe_tauftauf.handoff_check import check
    step = 'Inspect the supervisor observation producer and verify the live genome chat rate.'
    Feed(tmp_path).append('genome', step)
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Artifact: source.md. Next: '+step)
    result = check(tmp_path, handoff)
    assert result['state'] == 'repeat'
    assert result['next_step'] == step


def test_actual_next_section_wins_over_a_quoted_prior_next(tmp_path):
    from mishe_tauftauf.handoff_check import check
    old = 'Inspect the supervisor observation producer and verify the live genome chat rate.'
    new = 'Inspect the newly delivered source manifest and measure the optional advisor latency.'
    Feed(tmp_path).append('genome', old)
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Evidence quotes prior Next: '+old+'\n\nNext: '+new)
    assert check(tmp_path, handoff)['next_step'] == new
    assert check(tmp_path, handoff)['state'] == 'clear'


def test_inline_rollback_clause_cannot_hide_a_repeat(tmp_path):
    from mishe_tauftauf.handoff_check import check
    step = 'Inspect the supervisor observation producer and verify the live genome chat rate.'
    Feed(tmp_path).append('genome', step)
    handoff = tmp_path / 'handoff.md'
    handoff.write_text('Artifact: source.md. Next: '+step+' Rollback: remove wrapper.')
    assert check(tmp_path, handoff)['next_step'] == step
    assert check(tmp_path, handoff)['state'] == 'repeat'
