from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pytest
from mishe_tauftauf.feed import Feed


def site(tmp_path):
    now=datetime.now(timezone.utc)
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode":"wall","started":now.isoformat(),"until":(now+timedelta(hours=10)).isoformat(),"silence_seconds":10}))
    return now


def test_silence_posts_once_and_does_not_reset_on_automatic_chatter(tmp_path):
    from mishe_tauftauf import activity
    now=site(tmp_path)
    Feed(tmp_path).append("seed","seed observation health sha256="+"a"*64)
    first=activity.check(tmp_path,now=now+timedelta(seconds=11))
    assert first["silent"] and first["alert_sequence"]
    again=activity.check(tmp_path,now=now+timedelta(seconds=12))
    assert again["alert_sequence"]==first["alert_sequence"]
    alerts=[e for e in Feed(tmp_path).entries() if e.source=="silence-watch"]
    assert len(alerts)==1 and alerts[0].body.startswith("[dm] to=health")


def test_wall_edit_counts_but_display_refresh_does_not(tmp_path):
    from mishe_tauftauf import activity
    now=site(tmp_path)
    (tmp_path/"walls").mkdir()
    wall=tmp_path/"walls/docs.md";wall.write_text("Investigating")
    os.utime(wall,(now.timestamp()+8,now.timestamp()+8))
    assert activity.check(tmp_path,now=now+timedelta(seconds=11))["idle_seconds"]==3
    assert "3s" in activity.line(tmp_path,now=now+timedelta(seconds=11))


def test_settlement_rearms_next_silence_episode(tmp_path):
    from mishe_tauftauf import activity
    now=site(tmp_path)
    first=activity.check(tmp_path,now=now+timedelta(seconds=11))
    Feed(tmp_path).append("seed","seed yield docs wake=1\nPlanning done.")
    recovered=activity.check(tmp_path,now=datetime.now(timezone.utc))
    assert not recovered["silent"] and not recovered.get("alert_sequence")
    second=activity.check(tmp_path,now=datetime.now(timezone.utc)+timedelta(seconds=11))
    assert second["alert_sequence"]!=first["alert_sequence"]


def test_disabled_and_paused_do_not_alert_but_ended_notices_once(tmp_path):
    from mishe_tauftauf import activity
    now=site(tmp_path)
    path=tmp_path/"coordination-mode.json"
    for update in ({"silence_seconds":0},{"paused":True}):
        cfg=json.loads(path.read_text());cfg.update(update);path.write_text(json.dumps(cfg))
        assert not activity.check(tmp_path,now=now+timedelta(seconds=20)).get("alert_sequence")
        cfg.update(silence_seconds=10,paused=False,until=(now+timedelta(hours=10)).isoformat());path.write_text(json.dumps(cfg))
    assert not Feed(tmp_path).entries()
    cfg=json.loads(path.read_text());cfg["until"]=(now-timedelta(seconds=1)).isoformat();path.write_text(json.dumps(cfg))
    ended=activity.check(tmp_path,now=now+timedelta(seconds=20))
    assert ended["state"]=="ENDED" and ended["alert_sequence"]
    again=activity.check(tmp_path,now=now+timedelta(seconds=21))
    assert again["alert_sequence"]==ended["alert_sequence"]
    alerts=[e for e in Feed(tmp_path).entries() if e.source=="silence-watch"]
    assert len(alerts)==1 and "window ended" in alerts[0].body


def test_adjustable_threshold_validates_and_updates_only_setting(tmp_path):
    from mishe_tauftauf import activity
    site(tmp_path)
    activity.configure(tmp_path,120)
    cfg=json.loads((tmp_path/"coordination-mode.json").read_text())
    assert cfg["silence_seconds"]==120 and cfg["mode"]=="wall"
    for value in (-1,float("nan"),float("inf")):
        with pytest.raises(ValueError):activity.configure(tmp_path,value)


def test_idle_pending_health_turn_is_retried_without_new_wake(tmp_path,monkeypatch):
    from mishe_tauftauf import activity,seed,wall
    site(tmp_path)
    wake=Feed(tmp_path).append("seed","seed wake health observation=1\nRead wall.")
    calls=[]
    monkeypatch.setattr(seed,"_mind_ready",lambda *a:True)
    monkeypatch.setattr(wall,"retry",lambda home,role:calls.append(("retry",role)))
    monkeypatch.setattr(seed,"tick",lambda home,session,role,period:calls.append(("tick",role)) or "wake seed health 1")
    activity.wake_health(tmp_path,"session")
    assert calls==[("retry","health"),("tick","health")]
    assert seed._state(tmp_path,"health")[1]==wake.sequence
    calls.clear();monkeypatch.setattr(seed,"_mind_ready",lambda *a:False)
    assert "busy" in activity.wake_health(tmp_path,"session") and not calls


def test_observe_names_ok_silent_ended_and_disabled(tmp_path):
    from mishe_tauftauf import activity
    now=site(tmp_path)
    assert activity.observe(tmp_path,now=now)["state"]=="OK"
    assert activity.observe(tmp_path,now=now+timedelta(seconds=11))["state"]=="SILENT"
    path=tmp_path/"coordination-mode.json"
    cfg=json.loads(path.read_text());cfg["until"]=(now-timedelta(seconds=1)).isoformat();path.write_text(json.dumps(cfg))
    assert activity.observe(tmp_path,now=now+timedelta(seconds=11))["state"]=="ENDED"
    cfg["paused"]=True;path.write_text(json.dumps(cfg))
    assert activity.observe(tmp_path,now=now+timedelta(seconds=11))["state"]=="DISABLED"


def test_delivery_reports_commit_and_activation_ages(tmp_path):
    import subprocess
    from mishe_tauftauf import activity
    repo=tmp_path/"repo";repo.mkdir()
    subprocess.run(["git","init","-q",str(repo)],check=True)
    subprocess.run(["git","-C",str(repo),"-c","user.email=t@example.com","-c","user.name=t",
                    "commit","-q","--allow-empty","-m","seed"],check=True)
    home=tmp_path/"site";(home/"patches").mkdir(parents=True)
    (home/"patches"/"x-activate.log").write_text("ok")
    facts=activity.delivery(home,repo=repo)
    assert facts["last_commit_seconds"] is not None
    assert facts["last_activation_seconds"] is not None


def test_heartbeat_records_watcher_liveness(tmp_path):
    from mishe_tauftauf import activity
    site(tmp_path)
    activity.beat(tmp_path,"OK")
    data=json.loads((tmp_path/"checks"/"silence-heartbeat.json").read_text())
    assert data["state"]=="OK" and "at" in data
