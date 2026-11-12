from mishe_tauftauf import runtime_cli, arm, owed, intentions, signal_store, wake


def test_heartbeat_publishes_expiry_and_routes_owed_role_despite_legacy_overdue_target(tmp_path,monkeypatch):
    (tmp_path/"charters").mkdir();(tmp_path/"charters/docs.md").write_text("docs")
    (tmp_path/"units-installed").write_text("installed")
    signal_store.register(tmp_path,"reading",max_age=1,subscribers=["docs"])
    signal_store.observe(tmp_path,"reading","GREEN",now=10,source="test")
    owed.advance(tmp_path,"docs",owed.check(tmp_path,"docs",now=10))
    intentions.add(tmp_path,"retired","legacy",note="unmapped history",due=1)
    started=[];scheduled=[]
    monkeypatch.setattr(runtime_cli.time,"time",lambda:12)
    monkeypatch.setattr(runtime_cli.shutil,"which",lambda *a:"systemd-run")
    monkeypatch.setattr(arm,"systemd_scheduler",lambda at,**kw:scheduled.append(at))
    monkeypatch.setattr(wake,"systemd_starter",started.append)
    runtime_cli.scheduler(tmp_path)(1)
    assert signal_store.rev(tmp_path,"reading")==2
    assert started==["docs"] and intentions.list_(tmp_path,"retired")[0]["id"]=="legacy"
    owed.advance(tmp_path,"docs",owed.check(tmp_path,"docs",now=12))
    runtime_cli.scheduler(tmp_path)(1)
    assert started==["docs"]
    (tmp_path/"hold").write_text("hold")
    owed.touch_inbox(tmp_path,"docs")
    runtime_cli.scheduler(tmp_path)(1)
    assert started==["docs"]


def test_parse_when_reads_a_state_word_as_the_state_condition(tmp_path):
    # A state word is never a reading (signal_store.observe refuses to store one), so `=UNKNOWN`
    # can only mean the state.  Before this, it became {equals: UNKNOWN}, a subscription that
    # could never be met even while the signal was UNKNOWN.
    assert runtime_cli.parse_when("signal:egress=UNKNOWN")=={"signal":"egress","state":"UNKNOWN"}
    assert runtime_cli.parse_when("signal:egress@UNKNOWN")=={"signal":"egress","state":"UNKNOWN"}
    assert runtime_cli.parse_when("signal:doctor=RED")=={"signal":"doctor","equals":"RED"}
    signal_store.register(tmp_path,"egress",max_age=900,subscribers=["health"])
    signal_store.observe_unknown(tmp_path,"egress",now=10,reason="no route",source="test")
    owed.advance(tmp_path,"health",owed.check(tmp_path,"health",now=1))
    intentions.add(tmp_path,"health","when-egress",note="woken on the state",
                   when=runtime_cli.parse_when("signal:egress=UNKNOWN"))
    snap=owed.check(tmp_path,"health",now=20)
    assert "when-egress" in snap.consumed      # the subscription is live, not dead


def test_resume_owed_spaces_starts_of_one_role(tmp_path, monkeypatch):
    # Each arm (every producer finish) calls resume_owed. Unspaced, a refused genome start was
    # retried on each finish and tripped the protected host start limit (2026-11-11 genome loop).
    started = []
    monkeypatch.setattr(wake, "systemd_starter", started.append)
    monkeypatch.setattr(owed, "check", lambda home, role, now: type("S", (), {"reasons": ["owed"]})())
    wake.resume_owed(tmp_path, ["genome"], now=1000)
    wake.resume_owed(tmp_path, ["genome"], now=1100)     # inside the spacing: skipped
    wake.resume_owed(tmp_path, ["genome"], now=1100.5)   # still inside: skipped
    assert started == ["genome"]
    wake.resume_owed(tmp_path, ["genome"], now=1000 + wake.START_SPACING_SECONDS)
    assert started == ["genome", "genome"]


def test_resume_owed_respects_a_path_triggered_wake_start(tmp_path, monkeypatch):
    # DM 300352: a path-triggered start (debounce, 120 s) never reaches resume_owed's ledger, so a
    # resume at 150 s started a fourth unit inside 300 s. A wake records its own start in the shared
    # ledger, and resume_owed spaces against that record too.
    started = []
    monkeypatch.setattr(wake, "systemd_starter", started.append)
    monkeypatch.setattr(owed, "check", lambda home, role, now: type("R", (), {"reasons": ["owed"]})())
    wake.record_wake_start(tmp_path, "genome", now=120.0)   # what a path-triggered wake does on entry
    wake.resume_owed(tmp_path, ["genome"], now=150.0)        # 30 s after that start: must be skipped
    assert started == []
    wake.resume_owed(tmp_path, ["genome"], now=120.0 + wake.START_SPACING_SECONDS)
    assert started == ["genome"]
