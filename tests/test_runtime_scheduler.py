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


def test_debounced_path_start_waits_for_the_shared_spacing_and_records_itself(tmp_path):
    # DM 300359: resume at 0 starts genome; a path trigger at 1 with a 120 s window used to start the
    # wake at 121, inside the 150 s spacing. The debounced start waits out the spacing against the
    # ledger and records its own start, so a resume right after it is skipped.
    wake.record_wake_start(tmp_path, "genome", now=0.0)
    clock = {"t": 1.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    recorded = wake.debounced_start(tmp_path, "genome", 120, clock=lambda: clock["t"], sleep=sleep,
                                    starter=lambda role: None)
    assert slept == [120, 150.0 - (121.0 - 0.0)]  # window, then the rest of the 150 s spacing
    assert recorded == 150.0
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 150.0

    started = []
    import mishe_tauftauf.owed as owed
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(wake, "systemd_starter", started.append)
        mp.setattr(owed, "check", lambda home, role, now: type("R", (), {"reasons": ["owed"]})())
        wake.resume_owed(tmp_path, ["genome"], now=200.0)   # 50 s after the path start: skipped
    finally:
        mp.undo()
    assert started == []


def test_debounced_start_without_a_prior_start_only_waits_the_window(tmp_path):
    slept = []
    clock = {"t": 0.0}

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    wake.debounced_start(tmp_path, "senses", 120, clock=lambda: clock["t"], sleep=sleep,
                         starter=lambda role: None)
    assert slept == [120]


def test_debounced_start_checks_and_records_in_one_ledger_step(tmp_path):
    # Review of 52b50ab5 (DM 300362): the debounce read the ledger, then a resume recorded the same role
    # before the debounce wrote its own record, so both started at 150. Here a resume records genome at
    # 150 while the debounce sleeps out the spacing: the debounce must re-check and wait for 300.
    wake.record_wake_start(tmp_path, "genome", now=0.0)
    clock = {"t": 1.0}
    slept, started = [], []

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds
        if len(slept) == 2:                       # the spacing sleep: a competing resume records here
            wake.record_wake_start(tmp_path, "genome", now=clock["t"])

    recorded = wake.debounced_start(tmp_path, "genome", 0, clock=lambda: clock["t"], sleep=sleep,
                                    starter=started.append)
    assert recorded == 300.0
    assert started == ["genome"]                  # exactly one start from the debounce
    assert slept == [0, 149.0, 150.0]


def test_debounced_start_starts_the_wake_only_after_recording(tmp_path):
    started = []
    wake.debounced_start(tmp_path, "health", 0, clock=lambda: 10.0, sleep=lambda s: None,
                         starter=lambda role: started.append((role, wake.read_json(tmp_path / wake.START_LEDGER)[role])))
    assert started == [("health", 10.0)]
