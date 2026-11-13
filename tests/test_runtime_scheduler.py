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
    monkeypatch.setattr(wake, "systemd_starter", lambda role: started.append(role) or True)
    monkeypatch.setattr(owed, "check", lambda home, role, now: type("S", (), {"reasons": ["owed"]})())
    wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1000)
    wake.resume_owed(tmp_path, ["genome"], now=1100, clock=lambda: 1100)     # inside the spacing: skipped
    wake.resume_owed(tmp_path, ["genome"], now=1100.5, clock=lambda: 1100.5) # still inside: skipped
    assert started == ["genome"]
    t = 1000 + wake.START_SPACING_SECONDS
    wake.resume_owed(tmp_path, ["genome"], now=t, clock=lambda: t)
    assert started == ["genome", "genome"]


def test_resume_owed_respects_a_path_triggered_wake_start(tmp_path, monkeypatch):
    # DM 300352: a path-triggered start (debounce, 120 s) never reaches resume_owed's ledger, so a
    # resume at 150 s started a fourth unit inside 300 s. A wake records its own start in the shared
    # ledger, and resume_owed spaces against that record too.
    started = []
    monkeypatch.setattr(wake, "systemd_starter", started.append)
    monkeypatch.setattr(owed, "check", lambda home, role, now: type("R", (), {"reasons": ["owed"]})())
    wake.record_wake_start(tmp_path, "genome", now=120.0)   # what a path-triggered wake does on entry
    wake.resume_owed(tmp_path, ["genome"], now=150.0, clock=lambda: 150.0)   # 30 s after: must be skipped
    assert started == []
    t = 120.0 + wake.START_SPACING_SECONDS
    wake.resume_owed(tmp_path, ["genome"], now=t, clock=lambda: t)
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
                                    starter=lambda role: True)
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
        wake.resume_owed(tmp_path, ["genome"], now=200.0, clock=lambda: 200.0)   # 50 s after: skipped
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
                         starter=lambda role: True)
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
                                    starter=lambda role: started.append(role) or True)
    assert recorded == 300.0
    assert started == ["genome"]                  # exactly one start from the debounce
    assert slept == [0, 149.0, 150.0]


def test_debounced_start_records_no_earlier_than_the_request(tmp_path):
    # Review of 906a146e: a record taken before a slow starter let the real request land 148 s after the
    # recorded time. The record is read after systemd accepts, so it is never earlier than the request.
    started = []
    clock = {"t": 10.0}

    def starter(role):
        started.append((role, clock["t"]))
        clock["t"] += 2.0                          # the request takes 2 s to be accepted
        return True

    recorded = wake.debounced_start(tmp_path, "health", 0, clock=lambda: clock["t"], sleep=lambda s: None,
                                    starter=starter)
    assert started == [("health", 10.0)]
    assert recorded == 12.0
    assert wake.read_json(tmp_path / wake.START_LEDGER)["health"] == 12.0


def test_spacing_gap_between_real_requests_is_at_least_the_contract(tmp_path):
    # The guarantee: with the record taken after acceptance, two real request times are never closer
    # than START_SPACING_SECONDS, even when the starter is slow.
    requests = []
    clock = {"t": 0.0}

    def starter(role):
        requests.append(clock["t"])
        clock["t"] += 3.0                          # slow acceptance
        return True

    wake.debounced_start(tmp_path, "genome", 0, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                         starter=starter)
    clock["t"] = 10.0                              # a resume arrives soon after
    wake.debounced_start(tmp_path, "genome", 0, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                         starter=starter)
    assert len(requests) == 2
    assert requests[1] - requests[0] >= wake.START_SPACING_SECONDS


def test_accepted_start_survives_a_later_failure_in_the_same_pass(tmp_path, monkeypatch):
    # Review of 906a146e: the ledger was written only after the whole loop, so an exception after the
    # first accepted start dropped that start's evidence. The accepted record must persist anyway.
    from types import SimpleNamespace
    monkeypatch.setattr(wake.owed, "check", lambda home, role, now: SimpleNamespace(reasons=["inbox"]))

    def starter(role):
        if role == "senses":
            raise OSError("interrupted after the first acceptance")
        return True

    monkeypatch.setattr(wake, "systemd_starter", starter)
    import pytest
    with pytest.raises(OSError):
        wake.resume_owed(tmp_path, ["genome", "senses"], now=1000, clock=lambda: 1000)
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 1000


def test_debounced_start_refused_by_systemd_is_not_recorded_and_fails(tmp_path):
    wake.record_wake_start(tmp_path, "genome", now=0.0)   # prior start, spacing already passed
    import pytest
    with pytest.raises(RuntimeError, match="refused"):
        wake.debounced_start(tmp_path, "genome", 0, clock=lambda: 1000.0, sleep=lambda s: None,
                             starter=lambda role: False)
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 0.0   # refused start left no record


def test_resume_owed_refused_start_is_not_recorded_so_the_next_arm_retries(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(wake.owed, "check", lambda home, role, now: SimpleNamespace(reasons=["inbox"]))
    attempts = []
    monkeypatch.setattr(wake, "systemd_starter", lambda role: attempts.append(role) or False)
    assert wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1000) == ["genome"]
    assert attempts == ["genome"]
    assert "genome" not in wake.read_json(tmp_path / wake.START_LEDGER)   # refused: no spacing recorded
    # The next arm retries at once, because nothing was recorded for the refused start.
    monkeypatch.setattr(wake, "systemd_starter", lambda role: attempts.append(role) or True)
    wake.resume_owed(tmp_path, ["genome"], now=1001, clock=lambda: 1001)
    assert attempts == ["genome", "genome"]
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 1001


def test_systemd_starter_reports_refusal_as_false(monkeypatch):
    import subprocess
    class Done:
        returncode = 1
        stderr = "Start request repeated too quickly."
    monkeypatch.setattr(wake.subprocess, "run", lambda *a, **k: Done())
    assert wake.systemd_starter("genome") is False
    Done.returncode = 0
    assert wake.systemd_starter("genome") is True


def test_late_wake_record_never_moves_the_start_clock_back(tmp_path):
    # Review of e77d2c1a: a delayed record with an older `now` overwrote an accepted start at 200, so a
    # request at 250 was spaced only 50 s after it. The record only moves forward.
    wake.record_wake_start(tmp_path, "genome", now=200)
    wake.record_wake_start(tmp_path, "genome", now=100)
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 200


def test_exit_inside_a_later_starter_keeps_the_earlier_accepted_start(tmp_path, monkeypatch):
    # Review of e77d2c1a: os._exit in the second starter skipped the single final write, so the first
    # accepted start vanished. Each role now persists before the next role's request.
    from types import SimpleNamespace
    monkeypatch.setattr(wake.owed, "check", lambda home, role, now: SimpleNamespace(reasons=["inbox"]))

    def starter(role):
        if role == "senses":
            raise SystemExit(77)
        return True

    monkeypatch.setattr(wake, "systemd_starter", starter)
    import pytest
    with pytest.raises(SystemExit):
        wake.resume_owed(tmp_path, ["genome", "senses"], now=1000, clock=lambda: 1000)
    ledger = wake.read_json(tmp_path / wake.START_LEDGER)
    assert ledger["genome"] == 1000
    # write-ahead: the in-flight request is recorded before the starter runs, at request + spacing
    assert ledger["senses"] == 1000 + wake.START_WRITE_AHEAD_SECONDS


def test_child_exit_after_acceptance_keeps_the_next_start_spaced(tmp_path, monkeypatch):
    # Review of 20f172c7 (DM 300376): systemd accepted at t=1100, the child died before the post-accept
    # write, and a request at t=1150 went out 50 s after the real start. The write-ahead value covers the
    # whole acceptance window (bound + launch slack) plus spacing, so the next request waits for that record.
    from types import SimpleNamespace
    monkeypatch.setattr(wake.owed, "check", lambda home, role, now: SimpleNamespace(reasons=["inbox"]))

    def dies_after_accept(role):
        raise SystemExit(77)  # systemd accepted at t=1100; the process exits before the post-accept record

    monkeypatch.setattr(wake, "systemd_starter", dies_after_accept)
    import pytest
    with pytest.raises(SystemExit):
        wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1000)
    ledger = wake.read_json(tmp_path / wake.START_LEDGER)
    assert ledger["genome"] == 1000 + wake.START_WRITE_AHEAD_SECONDS
    # A request at 1150 (the reviewer's case) is refused as too early: the record is still ahead of it.
    requests = []
    monkeypatch.setattr(wake, "systemd_starter", lambda role: requests.append(role) or True)
    outcome = wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1150)
    assert outcome == ["genome"] and requests == []
    ledger = wake.read_json(tmp_path / wake.START_LEDGER)
    assert ledger["genome"] == 1000 + wake.START_WRITE_AHEAD_SECONDS  # spaced-out request not sent; record unchanged


def test_caller_death_during_acceptance_keeps_the_bound(tmp_path, monkeypatch):
    # Review of 5716e061 (DM 300379): the caller died while systemctl was still in flight, so the
    # subprocess timeout never applied and a second request at 1300 landed 10 s after a delayed
    # acceptance at 1290. The write-ahead record is request + 60 + 30 slack + 150 = 1240, so a request at 1300
    # waits; the earliest allowed request is 1390, past any acceptance (at most 1060 + 30 slack) plus 150.
    assert wake.START_WRITE_AHEAD_SECONDS == (wake.START_REQUEST_TIMEOUT_SECONDS + wake.START_LAUNCH_SLACK_SECONDS
                                              + wake.START_SPACING_SECONDS)
    from types import SimpleNamespace
    monkeypatch.setattr(wake.owed, "check", lambda home, role, now: SimpleNamespace(reasons=["inbox"]))

    def dies_while_in_flight(role):
        raise SystemExit(77)

    monkeypatch.setattr(wake, "systemd_starter", dies_while_in_flight)
    import pytest
    with pytest.raises(SystemExit):
        wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1000)
    requests = []
    monkeypatch.setattr(wake, "systemd_starter", lambda role: requests.append(role) or True)
    wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1300)
    assert requests == []  # 1300 is inside the spacing after the 1240 write-ahead record
    wake.resume_owed(tmp_path, ["genome"], now=1000, clock=lambda: 1390)
    assert requests == ["genome"]  # 1240 + 150: the earliest request allowed after the bound


def test_systemd_starter_argv_is_bounded_by_a_detached_timeout(monkeypatch):
    seen = {}
    class Done:
        returncode = 0
        stderr = ""
    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Done()
    monkeypatch.setattr(wake.subprocess, "run", fake_run)
    assert wake.systemd_starter("genome") is True
    assert seen["argv"][:5] == ["timeout", "-s", "KILL", "60", "systemctl"]
    assert seen["argv"][-1] == "mishe-wake@genome.service"


def test_killed_start_request_is_unresolved_not_refused(tmp_path, monkeypatch, capsys):
    # timeout's exit 124/137 means the request was killed at its bound: acceptance is unknown. That is
    # not a refusal, so the spacing record stays at the post-request clock instead of being restored.
    class Killed:
        returncode = 137
        stderr = ""
    monkeypatch.setattr(wake.subprocess, "run", lambda *a, **k: Killed())
    assert wake.systemd_starter("genome") is None
    assert "unresolved" in capsys.readouterr().err


def test_exit_inside_a_debounced_starter_still_leaves_the_record(tmp_path):
    import pytest
    def starter(role):
        raise SystemExit(77)
    with pytest.raises(SystemExit):
        wake.debounced_start(tmp_path, "genome", 0, clock=lambda: 1000, sleep=lambda s: None, starter=starter)
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 1000 + wake.START_WRITE_AHEAD_SECONDS


def test_refused_start_restores_the_previous_record(tmp_path):
    wake._ledger_update(tmp_path, lambda ledger: ledger.__setitem__("genome", 0.0))
    import pytest
    with pytest.raises(RuntimeError):
        wake.debounced_start(tmp_path, "genome", 0, clock=lambda: 1000, sleep=lambda s: None,
                             starter=lambda role: False)
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 0.0


def test_signal_killed_start_request_is_unresolved_not_refused(tmp_path, monkeypatch, capsys):
    # Field review of 22f51b67 (DM 300385): `timeout -s KILL` also signals its own process group, so the
    # caller sees returncode -9, not 124/137. That must be unresolved, or the write-ahead record is restored.
    class SignalKilled:
        returncode = -9
        stderr = ""
    monkeypatch.setattr(wake.subprocess, "run", lambda *a, **k: SignalKilled())
    assert wake.systemd_starter("genome") is None
    assert "unresolved" in capsys.readouterr().err


def test_unresolved_start_keeps_the_forward_write_ahead_record(tmp_path):
    # Field review of 9f1c9c1e: an unresolved request (timeout or signal, None) at 1001 replaced its
    # write-ahead record with 1001, so a request at 1151 was allowed while acceptance could still land
    # after the client died. The record must stay at request + START_WRITE_AHEAD_SECONDS.
    ledger = {}
    result = wake._start_once(tmp_path, ledger, "genome", lambda role: None, lambda: 1000.0)
    assert result == ("start", 1000.0 + wake.START_WRITE_AHEAD_SECONDS)
    assert ledger["genome"] == 1000.0 + wake.START_WRITE_AHEAD_SECONDS
    assert wake.read_json(tmp_path / wake.START_LEDGER)["genome"] == 1000.0 + wake.START_WRITE_AHEAD_SECONDS
    # A second request inside the spacing of that record waits instead of starting.
    kind, left = wake._start_once(tmp_path, ledger, "genome", lambda role: True, lambda: 1151.0)
    assert kind == "wait" and left > 0


def test_run_wake_successor_start_goes_through_the_ledger(tmp_path):
    # Review of 8799918d: run_wake's successor start called the raw starter, so it neither spaced
    # against nor recorded in the shared ledger. The successor must be recorded, and a second
    # successor start inside the spacing must not be sent.
    calls = []
    wake._ledger_update(tmp_path, lambda ledger: ledger.update({"other": 1.0}))
    assert wake._ledger_start(tmp_path, "genome", lambda role: calls.append(role) or True) == "start"
    assert wake._ledger_start(tmp_path, "genome", lambda role: calls.append(role) or True) == "wait"
    assert calls == ["genome"]
    assert wake._ledger_update(tmp_path, None)["genome"] > 0
