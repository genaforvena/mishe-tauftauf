import json
from dataclasses import asdict
from mishe_tauftauf.feed import FeedEntry
from mishe_tauftauf.task_state import TaskState
from mishe_tauftauf.coordination_checks import anomalies, project, episode


def state(seq, task, **kwargs):
    data = asdict(TaskState(task, "senses", seq, **kwargs))
    return FeedEntry(seq, "2026-09-30T00:00:00Z", "senses", f"[task-state] {task}\n" + json.dumps(data))


def task(seq, name):
    return FeedEntry(seq, "2026-09-30T00:00:00Z", "senses", f"[task] {name} owner=senses acceptance=checked")


def work(seq, name, **kwargs):
    data = dict(task=name, channel="witness", result="verified", next_step="Check CI", reason="CI unresolved", progress="same prerequisite", retry_event="ci", **kwargs)
    return FeedEntry(seq, "2026-09-30T00:00:00Z", "seed", "[work] channel=witness wake=1 observation=2 result=verified continue=0\n" + json.dumps(data))


def bare_work(seq, name):
    # No reason/progress/retry fields: the signatures cannot distinguish these
    # attempts, so they reach the SUSPICIOUS repeated-attempt branch.
    data = dict(task=name, channel="witness", result="verified")
    return FeedEntry(seq, "now", "seed", "[work] channel=witness wake=1 observation=2 result=verified continue=0\n" + json.dumps(data))


def test_closed_producer_is_visible():
    entries = [task(1,"producer"), FeedEntry(2,"now","senses","[done] producer checked"), task(3,"consumer"), state(4,"consumer",status="waiting",retry_task="producer")]
    assert project(entries)["tasks"]["producer"]["status"] == "done"
    assert any(a["kind"] == "completed-producer" for a in anomalies(entries))


def test_interleaved_all_role_receipts_ignore_observation_hash_labels():
    entries = [task(1,"a"), task(2,"b"), work(3,"a",observation=1,task_step="one",handoff_sha256="1"), work(4,"b"), work(5,"a",observation=2,task_step="two",handoff_sha256="2"), work(6,"a",observation=3,task_step="three",handoff_sha256="3")]
    assert len(project(entries)["receipts"]["a"]) == 3
    assert any(a["kind"] == "repeated-prerequisite" for a in anomalies(entries))


def test_useful_progress_or_new_prerequisite_is_not_unchanged():
    entries = [task(1,"a"), work(2,"a"), work(3,"a"), work(4,"a",prerequisite_evidence={"ci":"passed"})]
    assert not any(a["kind"] == "repeated-prerequisite" for a in anomalies(entries))
    entries[-1] = work(4,"a",result_override="changed")
    # An explicitly changed result owns its own checked outcome.
    entries[-1] = FeedEntry(4,"now","seed",entries[-1].body.replace('"result": "verified"', '"result": "changed"'))
    assert not any(a["kind"] == "repeated-prerequisite" for a in anomalies(entries))


def test_invalid_projection_is_visible():
    assert any(a["severity"] == "UNKNOWN" for a in anomalies([task(1,"a"), FeedEntry(2,"now","senses","[task-state] a\n{bad")]))


def test_dependencies_and_conditional_are_distinct():
    entries=[task(1,"a"),task(2,"b"),state(3,"a",status="waiting",retry_task="b"),state(4,"b",status="waiting",retry_task="a"),task(5,"c"),state(6,"c",next_step="When CI passes, land")]
    findings=anomalies(entries)
    assert any(a["kind"] == "dependency-cycle" and a["severity"] == "RED" for a in findings)
    assert any(a["kind"] == "conditional-ready" and a["severity"] == "SUSPICIOUS" for a in findings)


def test_missing_and_dropped_producers():
    entries = [task(1, 'a'), state(2, 'a', status='waiting', retry_task='absent'), task(3, 'b'), FeedEntry(4, 'now', 'senses', '[dropped] b superseded'), task(5, 'c'), state(6, 'c', status='waiting', retry_task='b')]
    assert {a['kind'] for a in anomalies(entries)} >= {'missing-producer', 'dropped-producer'}


def test_historical_invalid_double_claim_is_diagnosed():
    entries = [task(1, 'a')]
    for sequence, role, wake in ((2, 'senses', 1), (3, 'witness', 2)):
        data = dict(identity='a', owner=role, attempt_wake=wake, previous_owner='senses')
        entries.append(FeedEntry(sequence, 'now', role, '[task-claim] a\n' + json.dumps(data)))
    assert any(a['kind'] == 'double-claim' and a['sequences'] == [2, 3] for a in anomalies(entries))
    assert any(a['kind'] == 'invalid-context' for a in anomalies(entries))


def test_episode_reads_record_refs_and_all_role_history(tmp_path, monkeypatch):
    from mishe_tauftauf.records import prepare
    from mishe_tauftauf.feed import Feed
    data = dict(task='a', channel='witness', result='verified', reason='CI pending', archive='checked.md')
    ref = prepare(tmp_path, data, kind='work')
    entries = [task(1, 'a'), FeedEntry(2, 'now', 'seed', '[work] channel=witness wake=1 observation=2 result=verified continue=0\nWitness checked CI; still pending.\n' + ref, tmp_path), FeedEntry(3, 'now', 'witness', 'The prerequisite is unchanged.')]
    monkeypatch.setattr(Feed, 'entries', lambda self: entries)
    result = episode(tmp_path, 'witness', 'Verify candidate CI before landing.', context={'task':'a'})
    assert result['context_complete'] is True
    assert result['semantic_episode']['receipts'][0]['reason'] == 'CI pending'
    assert [entry['sequence'] for entry in result['history']] == [1, 2, 3]
    assert result['question_episodes']['P08']['recent_attempts'][0]['channel'] == 'witness'
    assert result['evidence_references'] == ['checked.md']


def test_unknown_identity_context_is_incomplete(tmp_path, monkeypatch):
    from mishe_tauftauf.feed import Feed
    monkeypatch.setattr(Feed, 'entries', lambda self: [])
    assert episode(tmp_path, 'witness', 'Check missing task.', context={'task':'missing'})['context_complete'] is False


def test_scoped_report_binds_unrelated_feed_without_copying_it(tmp_path, monkeypatch):
    from mishe_tauftauf.feed import Feed
    entries = [task(1, 'a'), FeedEntry(2, 'now', 'other', 'Unrelated evidence'), FeedEntry(3, 'now', 'witness', 'Previous notice'), FeedEntry(4, 'now', 'witness', 'Latest notice')]
    monkeypatch.setattr(Feed, 'entries', lambda self: entries)
    result = episode(tmp_path, 'witness', 'Check task acceptance.', context={'task':'a'})
    assert [e['sequence'] for e in result['history']] == [1, 4]
    assert result['projection']['event_count'] == 4
    assert result['projection']['cutoff'] == 4
    assert 'events' not in result['projection']
    assert len(result['projection']['canonical_entries_sha256']) == 64
    assert result['question_episodes']['R08']['previous_same_source'] == ['Latest notice']
    assert episode(tmp_path, 'witness', '[task] new owner=witness acceptance=checked', context={'identity':'new'})['context_complete'] is True


def test_checked_done_retires_suspicious_repetition_but_preserves_history():
    entries = [task(1, 'a'), work(2, 'a'), work(3, 'a', observation=2)]
    entries.append(FeedEntry(4, 'now', 'seed', '[work] channel=witness wake=1 observation=3 result=verified continue=0\n' + json.dumps(dict(task='a', channel='witness', result='verified', progress='Different inspected candidate'))))
    assert any(f['kind'] == 'repeated-attempt-review' for f in anomalies(entries))
    data = asdict(TaskState('a', 'senses', 5, status='done', evidence='checked.md', evidence_sha256='a'*64))
    entries.append(FeedEntry(5, 'now', 'senses', '[task-close] a\n' + json.dumps(data)))
    assert not any(f['kind'] == 'repeated-attempt-review' for f in anomalies(entries))
    assert len(project(entries)['receipts']['a']) == 3


def test_frozen_waiting_repetition_is_not_an_attempt_review():
    entries = [task(1, "a"), state(2, "a", status="waiting", retry_event="never-fires"),
               bare_work(3, "a"), bare_work(4, "a"), bare_work(5, "a")]
    assert not any(f["kind"] == "repeated-attempt-review" for f in anomalies(entries))
    # Once the predicate fires after the task's own state, the same historical
    # receipts are an actionable review prompt again.
    entries.append(FeedEntry(6, "now", "seed", "[task-event] never-fires"))
    assert any(f["kind"] == "repeated-attempt-review" for f in anomalies(entries))


def test_current_evidence_integrity_and_cause_identity_are_supplied(tmp_path, monkeypatch):
    import hashlib
    from mishe_tauftauf.feed import Feed
    proof = tmp_path / "proof.txt"; proof.write_text("Read-only source check passed; no mutation was authorized.")
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    entries = [task(1, "a"), state(2, "a", evidence=str(proof), evidence_sha256=digest)]
    monkeypatch.setattr(Feed, "entries", lambda self: entries)
    result = episode(tmp_path, "senses", "Continue the same read-only check.", context={"task":"a"})
    evidence = result["question_episodes"]["P16"]["current_evidence"]
    assert evidence[0]["integrity"] == "matched" and evidence[0]["text"] == proof.read_text()
    assert result["question_episodes"]["P28"]["cause_identity"]["canonical_task"] == "a"
    assert result["question_episodes"]["P28"]["task_declarations"] == [entries[0].body]
    proof.write_text("tampered proof")
    assert episode(tmp_path,"senses","Check.",context={"task":"a"})["question_episodes"]["P16"]["current_evidence"][0]["integrity"] == "mismatch"
    proof.unlink()
    assert episode(tmp_path,"senses","Check.",context={"task":"a"})["question_episodes"]["P16"]["current_evidence"][0]["integrity"] == "unavailable"


def test_transaction_is_narrow_and_prepared_guards_are_not_effect_proofs(tmp_path, monkeypatch):
    from mishe_tauftauf.feed import Feed
    monkeypatch.setattr(Feed, "entries", lambda self: [])
    transaction = {"event":"yield", "phase":"prepared", "required_commit_guards":["archive matches"], "before":{"handoff_source_read":True}}
    result=episode(tmp_path,"seed","Settlement follows guarded archive installation.",context={"transaction":transaction,"admitted_handoff_text":"Actual checked outcome."})
    episodes=result["question_episodes"]
    assert episodes["R06"]["transaction"]["phase"] == "prepared"
    assert "admitted_handoff_text" in episodes["R06"]
    assert "transaction" not in episodes["R01"] and "admitted_handoff_text" not in episodes["P01"]
    assert result["proposed"]["transaction"] == transaction
