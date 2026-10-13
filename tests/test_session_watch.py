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


def test_dead_pane_is_red():
    row=current(); row["panes_live"]=False
    assert watch.assess(row,{"observed_at":100,"identity":"owned-id","changes":[]},105)["state"]=="RED"


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
            output="0\n"*22
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


def test_boolean_restart_history_is_malformed():
    previous={"observed_at":100,"identity":"owned-id","changes":[True]}
    assert watch.assess(current(),previous,105)["state"]=="UNKNOWN"


def test_watchdog_heartbeat_precedes_slow_route(tmp_path,monkeypatch):
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
