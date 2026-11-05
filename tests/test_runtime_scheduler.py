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
