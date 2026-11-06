from mishe_tauftauf import work_state as w, intentions, owed, failure, signal_store, yielding
from mishe_tauftauf.state_files import write_json


def setup(home):
    (home / "charters").mkdir()
    for role in ("health", "genome", "steward"): (home / "charters" / f"{role}.md").write_text(role)
    fault = "failed-check-service"
    calls = []
    failure.report(home, "check.service", now=10, notify=lambda *x: calls.append(x))
    wait = dict(resolver="health", escalate_to="steward", evidence_needed="new signature", deadline=1000,
                action_at_deadline="choose repair", incident=fault, paths={}, signals={})
    w.save(home, "health", dict(version=1, role="health", initiative="cause", tasks=[dict(
        id="cause", state="waiting", wait=wait, retry_intentions=["again", "reoffer-"+fault])]))
    snap = owed.check(home, "health", now=11)
    write_json(yielding.snapshot_path(home, "k"), dict(role="health", snapshot=snap.to_json()))
    yielding.settle(home, "health", "k", text="cause remains open", result="blocked", now=12,
                    wall_writer=lambda *a: None)
    return calls, fault


def test_sixty_unchanged_samples_mail_and_one_deadline(tmp_path):
    calls, fault = setup(tmp_path)
    for t in range(20, 80):
        failure.report(tmp_path, "check.service", now=t, notify=lambda *x: calls.append(x))
        w.sweep(tmp_path, repo=tmp_path, now=t, notify=lambda *x: calls.append(x))
        assert not owed.check(tmp_path, "health", now=t).reasons
    assert len(calls) == 1
    assert signal_store.read(tmp_path, fault, now=80).value == "RED"
    owed.touch_inbox(tmp_path, "health")
    assert owed.check(tmp_path, "health", now=81).reasons == ["inbox"]
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *x: calls.append(x))
    w.sweep(tmp_path, repo=tmp_path, now=1001, notify=lambda *x: calls.append(x))
    assert len(calls) == 2
    assert calls[-1][0] == "steward"
    assert not any(i["id"].startswith("reoffer-") for i in intentions.list_(tmp_path, "health"))


def test_overdue_escalation_survives_due_deduplication(tmp_path):
    setup(tmp_path)
    calls = []
    for now in (1000, 1001, 1301, 1600):
        w.sweep(tmp_path, repo=tmp_path, now=now,
                notify=lambda *args: calls.append(args))
    assert [role for role, _ in calls] == ["steward", "genome"]


def test_overdue_escalation_is_not_repeated_on_later_sweeps(tmp_path):
    setup(tmp_path)
    calls = []
    for now in (1000, 1301, 1302, 1600, 5000):
        w.sweep(tmp_path, repo=tmp_path, now=now,
                notify=lambda *args: calls.append(args))
    assert [role for role, _ in calls] == ["steward", "genome"]


def test_grace_boundary_is_strict(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=1300, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward"]
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]


def test_first_ever_sweep_after_grace_emits_both_notifications(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=5000, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]


def test_replaced_deadline_starts_a_new_overdue_cycle(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]
    record = w.load(tmp_path, "health")
    record["tasks"][0]["wait"]["deadline"] = 2000
    w.save(tmp_path, "health", record)
    calls.clear()
    w.sweep(tmp_path, repo=tmp_path, now=2000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=2301, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]


def test_resolved_wait_stops_overdue_notifications(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]
    record = w.load(tmp_path, "health")
    record["tasks"][0]["state"] = "done"
    w.save(tmp_path, "health", record)
    calls.clear()
    for now in (1400, 2000, 5000):
        w.sweep(tmp_path, repo=tmp_path, now=now, notify=lambda *a: calls.append(a))
    assert calls == []


def test_removed_wait_stops_overdue_notifications(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    record = w.load(tmp_path, "health")
    record["tasks"] = []
    w.save(tmp_path, "health", record)
    calls.clear()
    w.sweep(tmp_path, repo=tmp_path, now=5000, notify=lambda *a: calls.append(a))
    assert calls == []


def test_corrupt_overdue_state_reoffers_actionable_work(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: calls.append(a))
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["steward", "genome"]
    (tmp_path / "work/watch-overdue.json").write_text("{")
    calls.clear()
    w.sweep(tmp_path, repo=tmp_path, now=1400, notify=lambda *a: calls.append(a))
    assert "genome" in [role for role, _ in calls]


def test_failed_notification_retries_on_the_next_sweep(tmp_path):
    setup(tmp_path)
    calls = []
    def failing(role, text):
        if role == "genome": raise ValueError("offline")
        calls.append((role, text))
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=failing)
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=failing)
    assert [role for role, _ in calls] == ["steward"]
    calls.clear()
    w.sweep(tmp_path, repo=tmp_path, now=1400, notify=lambda *a: calls.append(a))
    assert [role for role, _ in calls] == ["genome"]


def test_overdue_recipient_is_owed_inbox_work(tmp_path):
    setup(tmp_path)
    owed.advance(tmp_path, "genome", owed.Snapshot())
    w.sweep(tmp_path, repo=tmp_path, now=1000, notify=lambda *a: None)
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: None)
    assert "inbox" in owed.check(tmp_path, "genome", now=1302).reasons


def test_overdue_message_reports_unresolved_work_without_asserting_a_scheduler_fault(tmp_path):
    setup(tmp_path)
    calls = []
    w.sweep(tmp_path, repo=tmp_path, now=1301, notify=lambda *a: calls.append(a))
    text = [t for role, t in calls if role == "genome"][0]
    assert "still waiting" in text
    assert "missed" not in text
    assert "scheduler" not in text


def test_new_signature_and_corrupt_wait_are_actionable(tmp_path):
    calls, fault = setup(tmp_path)
    failure.report(tmp_path, "check.service", now=20, signature="timeout", notify=lambda *x: calls.append(x))
    assert len(calls) == 2
    assert owed.check(tmp_path, "health", now=20).reasons
    (tmp_path / "work/roles/health.json").write_text("{")
    w.sweep(tmp_path, repo=tmp_path, now=21, notify=lambda *x: calls.append(x))
    assert any("UNKNOWN" in x[1] for x in calls)


def test_fake_engine_invocation_count_during_wait(tmp_path):
    from mishe_tauftauf import wake
    calls, fault = setup(tmp_path)
    class Engine:
        calls = 0
        def run(self, prompt, *, env, **kwargs):
            self.calls += 1
            yielding.settle(tmp_path, env["MISHE_ROLE"], env["MISHE_WAKE_KEY"],
                            text="mail handled", result="blocked", now=100, wall_writer=lambda *a: None)
            return 0
    engine = Engine()
    for t in range(20, 80):
        wake.run_wake(tmp_path, "health", roles=["health"], engine=engine, now=t,
                      mail_reader=lambda *a: [], starter=lambda *a: None)
    assert engine.calls == 0
    owed.touch_inbox(tmp_path, "health")
    result = wake.run_wake(tmp_path, "health", roles=["health"], engine=engine, now=100,
                           mail_reader=lambda *a: [], starter=lambda *a: None)
    assert result.outcome == "completed"
    assert engine.calls == 1


def test_systemd_failure_signature_ignores_timestamp_and_tracks_exit_class(monkeypatch):
    from mishe_tauftauf import work_boundary as b
    from types import SimpleNamespace
    state=["Result=exit-code\nExecMainCode=1\nExecMainStatus=2\n"]
    monkeypatch.setattr(b.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=0,stdout=state[0],stderr=""))
    first=b.failure_signature("check.service")
    assert b.failure_signature("check.service")==first
    state[0]="Result=timeout\nExecMainCode=2\nExecMainStatus=15\n"
    assert b.failure_signature("check.service")!=first
