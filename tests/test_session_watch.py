import pytest
from mishe_tauftauf import session_watch as watch


def current(identity="owned-id"):
    return {"identity":identity,"available":True,"panes_live":True,"dashboard_live":True}


def test_missing_session_is_red():
    row=current(); row["available"]=False
    assert watch.assess(row,{"observed_at":100,"identity":"owned-id","changes":[]},105)["state"] == "RED"


def test_identity_change_is_retained_as_red():
    previous={"observed_at":100,"identity":"old","changes":[]}
    first=watch.assess(current(),previous,105)
    assert first["state"]=="RED"
    second=watch.assess(current(),first,110)
    assert second["state"]=="RED" and second["changes"]==[105]


@pytest.mark.parametrize("previous", [None,{}, {"observed_at":0,"identity":"owned-id","changes":[]}, {"observed_at":200,"identity":"owned-id","changes":[]}])
def test_missing_stale_broken_or_future_monitoring_is_unknown(previous):
    assert watch.assess(current(),previous,105)["state"]=="UNKNOWN"


def test_healthy_fresh_observation_is_green():
    assert watch.assess(current(),{"observed_at":100,"identity":"owned-id","changes":[]},105)["state"]=="GREEN"

def test_a_slow_but_live_loop_is_not_a_stale_monitor():
    """The old 20s bound turned a legitimate slow loop into a false UNKNOWN:
    the loop writes the next row only after the sleep (5s), the observation
    budget (20s) and the tape append of a routed alert, so a previous row 30s
    old is healthy. The excluded case must be healthy (panes and dashboard
    live) and a real failure — a row older than the watchdog interval — must
    still surface as UNKNOWN."""
    slow={"observed_at":75,"identity":"owned-id","changes":[]}
    row=watch.assess(current(),slow,105)
    assert row["state"]=="GREEN" and "invalid_previous" not in row
    assert watch.assess(current(),{"observed_at":5,"identity":"owned-id","changes":[]},105)["state"]=="UNKNOWN"


def test_dead_pane_is_red():
    row=current(); row["panes_live"]=False
    assert watch.assess(row,{"observed_at":100,"identity":"owned-id","changes":[]},105)["state"]=="RED"

def test_tmux_command_failure_is_unknown_not_red():
    """When tmux list-panes fails (returncode != 0), panes_live is None.
    This must produce UNKNOWN (we don't know pane state), not RED (false alarm)."""
    row=current(); row["panes_live"]=None
    result = watch.assess(row,{"observed_at":100,"identity":"owned-id","changes":[]},105)
    assert result["state"]=="UNKNOWN"
    assert result["reason"]=="pane liveness check failed"


def test_unreadable_dashboard_is_unknown():
    row=current(); row["dashboard_live"]=False
    assert watch.assess(row,{"observed_at":100,"identity":"owned-id","changes":[]},105)["state"]=="UNKNOWN"


def test_identity_includes_server_pid_to_detect_reused_tmux_session_id(tmp_path, monkeypatch):
    import subprocess
    calls=[]
    def run(argv, **kw):
        calls.append(argv)
        if "show-option" in argv:
            output=str(tmp_path)+"\n"
        elif "display-message" in argv:
            output="456:$2:100\n"
        elif "list-panes" in argv:
            output="docs.0:0\n"*22
        else:
            output="dashboard\n"
        return subprocess.CompletedProcess(argv,0,output,"")
    monkeypatch.setattr(watch.subprocess,"run",run)
    row=watch.observe(tmp_path,"owned")
    assert row["identity"]=="456:$2:100"
    assert "#{pid}:#{session_id}:#{session_created}" in calls[1]


class _Halt(Exception):
    pass


def test_routing_failure_keeps_bounded_retry_interval(tmp_path,monkeypatch):
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    clock={"now":200,"loops":0}
    calls=[]
    monkeypatch.setattr(watch.time,"monotonic",lambda:clock["now"])
    monkeypatch.setattr(watch,"notify",lambda _:None)
    monkeypatch.setattr(watch,"observe",lambda *a:{"available":False,"identity":None,"panes_live":False,"dashboard_live":False})
    def route(*args):
        calls.append(args)
        raise OSError("controlled unavailable chat transport")
    def sleep(_):
        clock["now"]+=5
        clock["loops"]+=1
        if clock["loops"]>=3:
            raise _Halt
    monkeypatch.setattr(watch,"route",route)
    monkeypatch.setattr(watch.time,"sleep",sleep)
    with pytest.raises(_Halt):
        watch.run(tmp_path,"fixture")
    assert len(calls)==1


def test_persistent_red_routes_once_until_state_or_reason_changes(tmp_path,monkeypatch):
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    routes=[]
    monkeypatch.setattr(watch,"notify",lambda _:None)
    monkeypatch.setattr(watch,"route",lambda *a:routes.append(a[1]["reason"]))
    clock={"now":200,"loops":0}
    monkeypatch.setattr(watch.time,"monotonic",lambda:clock["now"])
    states=[{"available":False,"identity":None,"panes_live":False,"dashboard_live":False},
            {"available":False,"identity":None,"panes_live":False,"dashboard_live":False},
            {"available":False,"identity":None,"panes_live":False,"dashboard_live":False},
            {"available":True,"identity":"x","panes_live":False,"dashboard_live":True},
            {"available":True,"identity":"x","panes_live":False,"dashboard_live":True}]
    def observe(*a):
        return dict(states[clock["loops"]])
    def sleep(_):
        clock["now"]+=5
        clock["loops"]+=1
        if clock["loops"]>=len(states):
            raise _Halt
    monkeypatch.setattr(watch,"observe",observe)
    monkeypatch.setattr(watch.time,"sleep",sleep)
    with pytest.raises(_Halt):
        watch.run(tmp_path,"fixture")
    assert routes==["owned session missing or ownership unavailable",
                    "resident pane missing or dead"]


def test_a_second_red_episode_after_green_routes_again(tmp_path,monkeypatch):
    """GREEN ends a (state, reason) episode. Before the reset, `routed_key`
    survived the whole process lifetime, so a condition that recurred after a
    healthy spell never routed again: the 20 RED episodes between 18:17Z and
    21:34Z on 2026-10-16 shared the 16:38Z route key and produced no alert."""
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    routes=[]
    monkeypatch.setattr(watch,"notify",lambda _:None)
    monkeypatch.setattr(watch,"route",lambda *a:routes.append(a[1]["reason"]))
    clock={"now":200,"loops":0}
    monkeypatch.setattr(watch.time,"monotonic",lambda:clock["now"])
    states=[{"available":True,"identity":"x","panes_live":False,"dashboard_live":True},
            {"available":True,"identity":"x","panes_live":True,"dashboard_live":True},
            {"available":True,"identity":"x","panes_live":False,"dashboard_live":True}]
    def observe(*a):
        return dict(states[clock["loops"]])
    def sleep(_):
        clock["now"]+=5
        clock["loops"]+=1
        if clock["loops"]>=len(states):
            raise _Halt
    monkeypatch.setattr(watch,"observe",observe)
    monkeypatch.setattr(watch.time,"sleep",sleep)
    with pytest.raises(_Halt):
        watch.run(tmp_path,"fixture")
    assert routes==["resident pane missing or dead","resident pane missing or dead"]


def test_boolean_restart_history_is_malformed():
    previous={"observed_at":100,"identity":"owned-id","changes":[True]}
    assert watch.assess(current(),previous,105)["state"]=="UNKNOWN"


def test_watchdog_heartbeat_precedes_slow_route(tmp_path,monkeypatch):
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    events=[]
    monkeypatch.setattr(watch,"notify",lambda message:events.append(message))
    monkeypatch.setattr(watch,"observe",lambda *a:{"available":False,"identity":None,"panes_live":False,"dashboard_live":False})
    def route(*args):
        assert events[-1].startswith("WATCHDOG=1")
        events.append("route")
    monkeypatch.setattr(watch,"route",route)
    def _stop(_):
        raise _Halt
    monkeypatch.setattr(watch.time,"sleep",_stop)
    with pytest.raises(_Halt):
        watch.run(tmp_path,"fixture")
    assert events[0]=="READY=1"
    assert events[-1].startswith("WATCHDOG=1")


def test_dead_pane_evidence_names_the_window(tmp_path, monkeypatch):
    import subprocess
    def run(argv, **kw):
        if "show-option" in argv:
            output=str(tmp_path)+"\n"
        elif "display-message" in argv:
            output="456:$2:100\n"
        elif "list-panes" in argv:
            output="".join(f"w{i}.0:0\n" for i in range(24))+"docs.1:1\n"
        else:
            output="dashboard\n"
        return subprocess.CompletedProcess(argv,0,output,"")
    monkeypatch.setattr(watch.subprocess,"run",run)
    row=watch.observe(tmp_path,"owned")
    assert row["panes_live"] is False
    assert row["pane_evidence"]=="rc=0 panes=25 dead=docs.1"


def test_failed_pane_command_keeps_the_returncode_in_evidence(tmp_path, monkeypatch):
    import subprocess
    def run(argv, **kw):
        if "show-option" in argv:
            output=str(tmp_path)+"\n"
        elif "display-message" in argv:
            output="456:$2:100\n"
        elif "list-panes" in argv:
            return subprocess.CompletedProcess(argv,1,"","no server running")
        else:
            output="dashboard\n"
        return subprocess.CompletedProcess(argv,0,output,"")
    monkeypatch.setattr(watch.subprocess,"run",run)
    row=watch.observe(tmp_path,"owned")
    assert row["panes_live"] is None
    assert row["pane_evidence"]=="rc=1 panes=0 dead=none"


def test_pane_evidence_change_logs_new_event_without_a_second_route(tmp_path,monkeypatch):
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    routes=[]
    monkeypatch.setattr(watch,"notify",lambda _:None)
    monkeypatch.setattr(watch,"route",lambda *a:routes.append(a[1]))
    clock={"now":200,"loops":0}
    monkeypatch.setattr(watch.time,"monotonic",lambda:clock["now"])
    states=[{"available":True,"identity":"x","panes_live":False,"dashboard_live":True,
             "pane_evidence":"rc=0 panes=25 dead=docs.1"},
            {"available":True,"identity":"x","panes_live":False,"dashboard_live":True,
             "pane_evidence":"rc=0 panes=25 dead=docs.1"},
            {"available":True,"identity":"x","panes_live":False,"dashboard_live":True,
             "pane_evidence":"rc=0 panes=25 dead=genome.1"}]
    def observe(*a):
        return dict(states[clock["loops"]])
    def sleep(_):
        clock["now"]+=5
        clock["loops"]+=1
        if clock["loops"]>=len(states):
            raise _Halt
    monkeypatch.setattr(watch,"observe",observe)
    monkeypatch.setattr(watch.time,"sleep",sleep)
    with pytest.raises(_Halt):
        watch.run(tmp_path,"fixture")
    events=(tmp_path/"health/session-watch-events.jsonl").read_text().splitlines()
    assert [row["reason"] for row in routes]==["resident pane missing or dead"]
    assert len(events)==2 and "genome.1" in events[-1]


def test_route_appends_an_in_process_dm_to_health(tmp_path, monkeypatch):
    """The detector routes in-process: a guarded CLI call rejected the
    unregistered 'session-watch' source, so every RED alert was dropped. The
    alert file stays as the detector's durable record, and the routing checker
    compares its mtime with the newest routed DM."""
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    watch.route(tmp_path, {"state": "RED",
                           "reason": "session identity changed within the last hour",
                           "pane_evidence": "rc=0 panes=25 dead=none"})
    feed = (tmp_path / "chat.log").read_text()
    assert "session-watch" in feed
    assert "[dm] to=health" in feed
    assert "session identity changed within the last hour" in feed
    alert = (tmp_path / "health/session-watch-alert.md").read_text()
    assert "session identity changed within the last hour" in alert
    assert "rc=0 panes=25 dead=none" in alert


def test_observation_reads_share_one_bounded_budget(monkeypatch, tmp_path):
    """Four sequential reads must never add up past the watchdog's heartbeat.

    The loop's heartbeat is 30s apart, so the whole observation is bounded by
    one budget: the dashboard read may take the larger share, but no read may
    claim more than what is left of that budget, and a spent budget fails the
    observation instead of overrunning the watchdog.
    """
    clock = {"now": 0.0}
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(argv, capture_output=None, text=None, timeout=None):
        calls.append((argv[0], timeout))
        clock["now"] += 4.0
        return Result()

    monkeypatch.setattr(watch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    watch.observe(tmp_path, "session")
    assert [command for command, _ in calls] == [
        "tmux", "tmux", "tmux", str(tmp_path / "bin/mishe-tauftauf")]
    assert [timeout for _, timeout in calls] == [15.0, 15.0, 12.0, 8.0]


def test_a_spent_observation_budget_fails_instead_of_reading_on(monkeypatch, tmp_path):
    clock = {"now": 0.0}
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(argv, capture_output=None, text=None, timeout=None):
        calls.append(argv[0])
        clock["now"] += 20.0
        return Result()

    monkeypatch.setattr(watch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    with pytest.raises(watch.subprocess.TimeoutExpired):
        watch.observe(tmp_path, "session")
    assert len(calls) == 1
