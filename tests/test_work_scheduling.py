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
