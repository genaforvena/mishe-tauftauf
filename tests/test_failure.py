from mishe_tauftauf import failure, owed, signal_store as store

NOW = 5000.0


def test_failed_wake_goes_red_and_owes_the_resolver(tmp_path):
    sent = []
    failure.report(tmp_path, "mishe-wake@docs.service", now=NOW, notify=lambda *a: sent.append(a))
    sig = store.read(tmp_path, "failed-mishe-wake-docs-service", now=NOW)
    assert (sig.state, sig.value) == ("OK", "RED")
    assert owed.check(tmp_path, "health", now=NOW).reasons == ["inbox", "signal:failed-mishe-wake-docs-service"]
    assert sent and sent[0][0] == "health" and "mishe-wake@docs.service" in sent[0][1]


def test_health_failing_is_routed_to_genome_not_itself(tmp_path):
    failure.report(tmp_path, "mishe-wake@health.service", now=NOW, notify=lambda *a: None)
    assert owed.check(tmp_path, "genome", now=NOW).reasons[0] == "inbox"
    assert owed.check(tmp_path, "health", now=NOW).reasons == []


def test_resolver_override_file(tmp_path):
    (tmp_path / "resolvers.json").write_text('{"mishe-producer-devto.service": "pub"}')
    failure.report(tmp_path, "mishe-producer-devto.service", now=NOW, notify=lambda *a: None)
    assert owed.check(tmp_path, "pub", now=NOW).reasons[0] == "inbox"


def test_red_failure_is_reoffered_until_cleared(tmp_path):
    failure.report(tmp_path, "mishe-wake@docs.service", now=NOW, notify=lambda *a: None)
    from mishe_tauftauf import intentions
    ids = [i["id"] for i in intentions.list_(tmp_path, "health")]
    assert "reoffer-failed-mishe-wake-docs-service" in ids       # level, not just edge


def test_stale_failure_record_reads_unknown_not_green(tmp_path):
    failure.report(tmp_path, "mishe-wake@docs.service", now=NOW, notify=lambda *a: None)
    sig = store.read(tmp_path, "failed-mishe-wake-docs-service", now=NOW + 10 * 86400)
    assert sig.state == "UNKNOWN"


def _settle(tmp_path, key, at):
    from mishe_tauftauf import yielding
    from mishe_tauftauf.state_files import write_json
    write_json(yielding.snapshot_path(tmp_path, key),
               {"role": "health", "snapshot": owed.check(tmp_path, "health", now=at).to_json()})
    yielding.settle(tmp_path, "health", key, text="w", result="blocked", now=at, wall_writer=lambda *a: None)


def test_yield_reoffers_only_while_the_signal_is_still_red(tmp_path):
    from mishe_tauftauf import intentions
    ids = lambda: [i["id"] for i in intentions.list_(tmp_path, "health")]
    failure.report(tmp_path, "mishe-wake@docs.service", now=NOW, notify=lambda *a: None)
    _settle(tmp_path, "k1", NOW + 4000)               # the first offer is due and consumed here
    assert "reoffer-failed-mishe-wake-docs-service" in ids()      # re-added: still RED
    store.observe(tmp_path, "failed-mishe-wake-docs-service", "GREEN", now=NOW + 4100, source="t")
    _settle(tmp_path, "k2", NOW + 9000)               # re-offer consumed; signal now GREEN
    assert "reoffer-failed-mishe-wake-docs-service" not in ids()


# --- a failing-every-minute producer must not become a message per failure -------------------------------------

def _count(tmp_path, unit, times):
    sent = []
    for at in times:
        failure.report(tmp_path, unit, now=at, notify=lambda *a: sent.append(a))
    return sent


def test_a_standing_failure_is_announced_once_not_on_every_run(tmp_path):
    # 2026-11-05: host-capacity and omp-db failed every minute; each failure appended a DM to Health (80 of the last
    # 120 tape entries) and woke it every two minutes, holding the one wake slot while other minds waited.
    sent = _count(tmp_path, "mishe-producer-host-capacity.service", [NOW + 60 * n for n in range(30)])
    assert len(sent) == 1
    from mishe_tauftauf import intentions
    reoffer = [i for i in intentions.list_(tmp_path, "health") if i["id"] == "reoffer-failed-mishe-producer-host-capacity-service"]
    assert len(reoffer) == 1 and reoffer[0]["due"] == NOW + failure.REOFFER_SECONDS       # not pushed back by each failure


def test_the_failure_is_announced_again_when_the_reoffer_has_come_due(tmp_path):
    unit = "mishe-producer-host-capacity.service"
    assert len(_count(tmp_path, unit, [NOW])) == 1
    assert len(_count(tmp_path, unit, [NOW + failure.REOFFER_SECONDS + 1])) == 1


def test_a_cleared_failure_that_returns_is_announced_again(tmp_path):
    unit = "mishe-producer-host-capacity.service"
    _count(tmp_path, unit, [NOW])
    store.observe(tmp_path, "failed-mishe-producer-host-capacity-service", "GREEN", now=NOW + 100, source="t")
    assert len(_count(tmp_path, unit, [NOW + 200])) == 1


def test_a_different_failing_unit_is_still_announced(tmp_path):
    sent = _count(tmp_path, "mishe-producer-host-capacity.service", [NOW, NOW + 60])
    sent += _count(tmp_path, "mishe-producer-omp-db.service", [NOW + 61, NOW + 120])
    assert len(sent) == 2 and "omp-db" in sent[1][1]


def test_repeated_failures_do_not_keep_touching_the_resolvers_inbox(tmp_path):
    unit = "mishe-wake@docs.service"
    failure.report(tmp_path, unit, now=NOW, notify=lambda *a: None)
    from mishe_tauftauf import yielding
    owed.advance(tmp_path, "health", owed.check(tmp_path, "health", now=NOW + 1))       # health has read it
    failure.report(tmp_path, unit, now=NOW + 60, notify=lambda *a: None)
    assert "inbox" not in owed.check(tmp_path, "health", now=NOW + 61).reasons


def test_optional_work_accounting_failure_preserves_primary_red_and_routes_gap(tmp_path,monkeypatch):
    def denied(*a,**kw):raise PermissionError("work accounting denied")
    monkeypatch.setattr(failure,"write_json",denied)
    sent=[]
    failure.report(tmp_path,"check.service",now=NOW,notify=lambda *a:sent.append(a))
    assert store.read(tmp_path,"failed-check-service",now=NOW).value=="RED"
    assert store.read(tmp_path,"work-accounting",now=NOW).value=="RED"
    assert any(role=="genome" and "UNKNOWN" in text for role,text in sent)
    assert any(role=="health" for role,text in sent)
