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
