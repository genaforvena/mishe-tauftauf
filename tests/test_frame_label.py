import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import frame_label as fl


def cand(det="d1", room=fl.LIVING, probs=None, **kw):
    return {"det": det, "room": room, "camera": "c", "probs": probs or {"ilya": 0.5, "rozalia": 0.5}, **kw}


def test_parse():
    assert fl.parse_reply("Это Розалия") == "rozalia"
    assert fl.parse_reply("Илья") == "ilya"
    assert fl.parse_reply("bruno") == "bruno"
    assert fl.parse_reply("никого") == "nobody"
    assert fl.parse_reply("ilya and bruno") == "unknown"
    assert fl.parse_reply("хм не знаю") == "unknown"


def test_cap_spacing_and_release(tmp_path):
    t = 1_000_000.0
    for i in range(3):
        assert fl.reserve(tmp_path, cand(f"d{i}"), t + i * fl.MIN_GAP_S)
    t4 = t + 3 * fl.MIN_GAP_S
    assert not fl.reserve(tmp_path, cand("d9"), t4)          # cap
    assert not fl.decide(tmp_path, cand("d9"), t + 1)["send"]
    fl.record_send_failed(tmp_path, "d2", "net", t4)         # slot released
    assert fl.reserve(tmp_path, cand("d9"), t4 + 1)
    assert fl.reserve(tmp_path, cand("d10"), t + fl.WINDOW_S + 5 * fl.MIN_GAP_S) is True


def test_spacing(tmp_path):
    assert fl.reserve(tmp_path, cand("a"), 100.0)
    assert not fl.reserve(tmp_path, cand("b"), 200.0)


def test_consent(tmp_path):
    d = tmp_path / "spatial/work/household/frame_labels"; d.mkdir(parents=True)
    (d / "consent.json").write_text(json.dumps({"rozalia_frames": "no"}))
    assert not fl.decide(tmp_path, cand())["send"]
    (d / "consent.json").write_text(json.dumps({"ilya_bedroom": "yes"}))
    assert not fl.decide(tmp_path, cand(room=fl.BEDROOM))["send"]
    (d / "consent.json").write_text(json.dumps({"ilya_bedroom": "yes", "rozalia_bedroom": "yes"}))
    assert fl.decide(tmp_path, cand(room=fl.BEDROOM))["send"]


def test_not_ambiguous(tmp_path):
    fl._append(tmp_path, {"ev": "label", "room": fl.LIVING, "label": "ilya", "det": "x", "t": 1})
    assert not fl.decide(tmp_path, cand(probs={"ilya": 0.95, "rozalia": 0.05}))["send"]
    assert fl.decide(tmp_path, cand(probs={"ilya": 0.95, "rozalia": 0.05}, hypotheses_disagree=True))["send"]


def test_reply_scores_and_deletes(tmp_path):
    f = tmp_path / "f.jpg"; f.write_bytes(b"x")
    assert fl.reserve(tmp_path, cand("d1", probs={"ilya": 0.6, "rozalia": 0.4}, frame=str(f)), 10.0)
    fl.record_sent(tmp_path, "d1", 77, 11.0)
    assert fl.apply_reply(tmp_path, 5, "ilya") is None
    ev = fl.apply_reply(tmp_path, 77, "Розалия", 20.0)
    assert ev["label"] == "rozalia" and ev["guess"] == "ilya" and ev["guess_correct"] is False
    assert not f.exists()
    assert fl.apply_reply(tmp_path, 77, "ilya") is None       # one label per photo
    assert fl.accuracy(tmp_path) == {"labels": 1, "correct": 0}


def test_sweep(tmp_path):
    f = tmp_path / "f.jpg"; f.write_bytes(b"x")
    fl.reserve(tmp_path, cand("d1", frame=str(f)), 10.0)
    assert fl.sweep(tmp_path, 10.0 + 3600) == 0 and f.exists()
    assert fl.sweep(tmp_path, 10.0 + fl.RETENTION_S) == 1 and not f.exists()
