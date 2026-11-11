import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import frame_label as fl
import frame_label_tick as t


def _site(tmp_path, delta):
    h = tmp_path / "spatial/work/household"
    h.mkdir(parents=True)
    (h / "frame_labels").mkdir()
    (h / "frame_labels/consent.json").write_text(json.dumps({"ilya_frames": "yes", "rozalia_frames": "yes"}))
    rows = [{"status": "ok", "room": "living room", "captured_at_epoch_s": 1000.0 + 150 * i,
             "grid": [100 + (delta if i else 0)] * 48} for i in range(2)]
    (h / "signatures.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return tmp_path


def test_send_cap_and_reply(tmp_path, monkeypatch):
    site = _site(tmp_path, 40)
    monkeypatch.setattr(t, "capture", lambda room, out: out.write_bytes(b"x") or True)
    monkeypatch.setattr(t, "send_photo", lambda p, c: (777, ""))
    monkeypatch.setattr(t, "REPLIES", tmp_path / "replies.jsonl")
    r = t.tick(site, now=2000.0)
    assert r["sent"] == {"det": "livingroom-1150", "msg": 777}
    assert t.tick(site, now=2100.0)["sent"] is None  # already handled
    (tmp_path / "replies.jsonl").write_text(json.dumps({"reply_to": 777, "text": "это я"}) + "\n")
    assert t.tick(site, now=2200.0)["labels"] == 1
    assert not list((fl._dir(site) / "frames").glob("*.jpg"))  # frame deleted on label


def test_no_change_no_send(tmp_path, monkeypatch):
    site = _site(tmp_path, 0)
    monkeypatch.setattr(t, "send_photo", lambda p, c: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(t, "REPLIES", tmp_path / "none.jsonl")
    assert t.tick(site, now=2000.0)["sent"] is None


def test_failed_send_releases_slot(tmp_path, monkeypatch):
    site = _site(tmp_path, 40)
    monkeypatch.setattr(t, "capture", lambda room, out: out.write_bytes(b"x") or True)
    monkeypatch.setattr(t, "send_photo", lambda p, c: (None, "boom"))
    monkeypatch.setattr(t, "REPLIES", tmp_path / "none.jsonl")
    t.tick(site, now=2000.0)
    assert fl.reserved(fl.read_ledger(site), 2001.0) == []


def test_reply_receipts(tmp_path, monkeypatch):
    site = _site(tmp_path, 0)
    monkeypatch.setattr(t, "REPLIES", tmp_path / "gone.jsonl")
    assert t.apply_replies(site, 1.0) == 0
    monkeypatch.setattr(t, "REPLIES", tmp_path / "r.jsonl")
    (tmp_path / "r.jsonl").write_text('not json\n' + json.dumps({"reply_to": 5, "message_id": 9, "text": "x"}) + "\n")
    t.apply_replies(site, 2.0)
    rows = [json.loads(l) for l in (fl._dir(site) / "replies-consumed.jsonl").read_text().splitlines()]
    assert [r["result"] for r in rows] == ["input_missing", "malformed", "unmatched"]


def test_main_exits_2_while_a_label_is_stale(tmp_path, monkeypatch):
    import runpy, pytest
    import frame_label as fl
    fl._append(tmp_path, {"ev": "label", "room": fl.BEDROOM, "label": "unknown", "det": "o", "t": 1,
                          "text": "никого нет, Илья только ушёл", "guess": "ilya"})
    monkeypatch.setenv("MISHE_TAUFTAUF_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["frame_label_tick.py", "--dry"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path("tools/frame_label_tick.py", run_name="__main__")
    assert e.value.code == 2
    fl.correct_label(tmp_path, "o", "nobody", "fix", 2.0)
    with pytest.raises(SystemExit) as e:
        runpy.run_path("tools/frame_label_tick.py", run_name="__main__")
    assert e.value.code == 0
