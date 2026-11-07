"""Continuous listener machinery for tools/audio_listener.py (no devices, no models, no site data)."""
import importlib.util
import queue
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audio_hint as ah  # noqa: E402
import audio_listener as al  # noqa: E402

BPS = al.BPS


def chunk(seconds=1):
    return b"\x01\x00" * (ah.RATE * seconds)


def vocab():
    return {
        "prompt": "x",
        "hint_words": {ah.tok(w) for w in ["mishe", "миша"]},
        "stop_words": [ah.tok("стоп")],
        "stop_phrases": [tuple(ah.tokens("stop listening"))],
    }


def test_segmenter_has_no_gap_and_one_second_overlap():
    seg = al.Segmenter()
    segs = []
    for i in range(30):
        segs += seg.feed(chunk(), float(i))
    # first segment: 8 new seconds (at second 8); then 7 new seconds at 15, 22, 29
    assert [round(s.new_s) for s in segs] == [8, 7, 7, 7]
    assert all(len(s.pcm) == al.SEG_S * BPS for s in segs)
    # new spans add up to the stream length minus the 1 s still incomplete: contiguous, no gap
    assert sum(s.new_s for s in segs) == 29


def test_queue_drops_oldest_and_counts():
    ledger = al.Ledger()
    q = queue.Queue(maxsize=2)
    for name in ["a", "b", "c", "d", "e"]:
        al.offer(q, name, ledger)
    assert ledger.drops == 3
    assert [q.get_nowait(), q.get_nowait()] == ["d", "e"]


def test_coverage_is_analysed_over_wall_and_reads_red_under_80():
    ledger = al.Ledger()
    ledger.add("wall", 700.0)
    ledger.add("captured", 700.0)
    ledger.add("analysed", 500.0)  # 0.714
    doc = al.status_doc(ledger, "LISTENING")
    assert doc["coverage"] == 0.714 and doc["coverage_state"] == "RED"
    ledger.add("analysed", 200.0)  # 0.997
    assert al.status_doc(ledger, "LISTENING")["coverage_state"] == "GREEN"


def test_coverage_warms_up_before_min_wall():
    ledger = al.Ledger()
    ledger.add("wall", 100.0)
    ledger.add("analysed", 100.0)
    doc = al.status_doc(ledger, "LISTENING")
    assert doc["coverage"] is None and doc["coverage_state"] == "WARMING"


def test_classify_silence_and_no_speech(monkeypatch, tmp_path):
    wav = tmp_path / "w.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(ah, "gate_wav", lambda p: "DROP energy: silence (RMS 1 < threshold 300)")
    row = al.classify(wav, {}, vocab(), tmp_path, al.TINY, al.BASE)
    assert row["status"] == "SILENT_OR_DROPPED" and row["lang"] is None

    monkeypatch.setattr(ah, "gate_wav", lambda p: "PASS")
    monkeypatch.setattr(ah, "transcribe", lambda *a, **k: "")
    row = al.classify(wav, {}, vocab(), tmp_path, al.TINY, al.BASE)
    assert row["status"] == "NO_SPEECH"


def test_hint_is_spotted_with_tiny_then_confirmed_with_base(monkeypatch, tmp_path):
    wav = tmp_path / "w.wav"
    wav.write_bytes(b"")
    calls = []
    monkeypatch.setattr(ah, "gate_wav", lambda p: "PASS")
    monkeypatch.setattr(ah, "text_gate", lambda t: "PASS")

    def fake_transcribe(path, prompt, lang, model=ah.MODEL):
        calls.append((lang, model.stem))
        return "Миша включи свет"

    monkeypatch.setattr(ah, "transcribe", fake_transcribe)
    row = al.classify(wav, {}, vocab(), tmp_path, al.TINY, al.BASE)
    assert row["status"] == "HINT" and row["lang"] == "ru"
    assert row["hint"] == "Миша включи свет"
    assert calls == [("ru", "ggml-tiny"), ("ru", "ggml-base")]


def test_unconfirmed_spot_keeps_no_text(monkeypatch, tmp_path):
    wav = tmp_path / "w.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(ah, "gate_wav", lambda p: "PASS")
    monkeypatch.setattr(ah, "text_gate", lambda t: "PASS")

    def fake_transcribe(path, prompt, lang, model=ah.MODEL):
        return "Миша включи свет" if model.stem == "ggml-tiny" else "Маша включи"

    monkeypatch.setattr(ah, "transcribe", fake_transcribe)
    row = al.classify(wav, {}, vocab(), tmp_path, al.TINY, al.BASE)
    assert row["status"] == "SPOT_UNCONFIRMED"
    assert "hint" not in row and "text" not in row


def test_read_chunk_returns_none_on_stall():
    proc = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                            stdout=subprocess.PIPE)
    try:
        assert al.read_chunk(proc, n=16, timeout=0.5) is None
    finally:
        al.stop_proc(proc)


def test_read_chunk_reads_exact_bytes():
    proc = subprocess.Popen([sys.executable, "-I", "-c",
                             "import sys; sys.stdout.buffer.write(b'x' * 40)"],
                            stdout=subprocess.PIPE)
    try:
        assert al.read_chunk(proc, n=16, timeout=5) == b"x" * 16
        assert al.read_chunk(proc, n=16, timeout=5) == b"x" * 16
        assert al.read_chunk(proc, n=16, timeout=5) is None  # closed before 16 bytes
    finally:
        al.stop_proc(proc)
