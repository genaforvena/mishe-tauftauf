from mishe_tauftauf import work_trial as t
from mishe_tauftauf.state_files import write_json, read_json


def test_six_and_twentyfour_hour_reports_once_under_clock(tmp_path):
    root=tmp_path/"work/experiment"
    write_json(root/"manifest.json",dict(version=1,start=100,end=86500,sha="abc",roles=["steward"]))
    calls=[]
    assert t.tick(tmp_path,now=101,notify=lambda *a:calls.append(a))==0
    assert not (root/"6h.json").exists()
    t.tick(tmp_path,now=21700,notify=lambda *a:calls.append(a))
    t.tick(tmp_path,now=21701,notify=lambda *a:calls.append(a))
    assert len(calls)==1 and calls[0][0]=="steward"
    assert read_json(root/"6h.json")["end"]==21700
    t.tick(tmp_path,now=86500,notify=lambda *a:calls.append(a))
    t.tick(tmp_path,now=86501,notify=lambda *a:calls.append(a))
    assert len(calls)==2
    assert read_json(root/"24h.json")["end"]==86500
    assert read_json(root/"manifest.json")["state"]=="AWAITING_DISPOSITION"


def test_corrupt_manifest_or_missing_due_report_is_unknown(tmp_path):
    root=tmp_path/"work/experiment";root.mkdir(parents=True)
    (root/"manifest.json").write_text("{")
    assert t.tick(tmp_path,now=100,notify=lambda *a:None)==3


def test_lost_completed_checkpoint_is_unknown_and_not_recreated(tmp_path):
    root=tmp_path/"work/experiment"
    write_json(root/"manifest.json",dict(version=1,start=100,end=86500))
    t.tick(tmp_path,now=86500,notify=lambda *a:None)
    (root/"24h.json").unlink()
    assert t.tick(tmp_path,now=86501,notify=lambda *a:None)==3
    assert not (root/"24h.json").exists()


def test_lost_manifest_after_checkpoint_is_unknown(tmp_path):
    root=tmp_path/"work/experiment"
    write_json(root/"manifest.json",dict(version=1,start=100,end=86500))
    t.tick(tmp_path,now=86500,notify=lambda *a:None)
    (root/"manifest.json").unlink()
    assert t.tick(tmp_path,now=86501,notify=lambda *a:None)==3
