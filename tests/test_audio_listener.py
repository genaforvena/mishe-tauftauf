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


def test_coverage_is_analysed_over_wall_and_reads_red_under_50():
    ledger = al.Ledger()
    ledger.add("wall", 700.0)
    ledger.add("captured", 700.0)
    ledger.add("analysed", 300.0)  # 0.429
    doc = al.status_doc(ledger, "LISTENING")
    assert doc["coverage"] == 0.429 and doc["coverage_state"] == "RED"
    ledger.add("analysed", 200.0)  # 0.714
    assert al.status_doc(ledger, "LISTENING")["coverage_state"] == "GREEN"


def test_heartbeat_reading_is_green_red_or_unknown_never_green_when_stale():
    now = 1_000_000.0
    fresh = {"t": now - 10, "state": "LISTENING", "coverage_state": "GREEN", "coverage": 0.9}
    assert al.reading(fresh, now) == ("GREEN", "coverage 0.9")
    low = dict(fresh, coverage_state="RED", coverage=0.3)
    assert al.reading(low, now)[0] == "RED"
    assert al.reading(dict(fresh, t=now - 500), now)[0] == "UNKNOWN"  # stale
    assert al.reading(None, now)[0] == "UNKNOWN"  # missing
    assert al.reading(dict(fresh, state="RESTARTING"), now)[0] == "UNKNOWN"
    assert al.reading(dict(fresh, state="SUSPENDED"), now)[0] == "UNKNOWN"
    assert al.reading(dict(fresh, state="STOPPED"), now)[0] == "UNKNOWN"
    assert al.reading(dict(fresh, coverage_state="WARMING", coverage=None), now)[0] == "UNKNOWN"


def test_heartbeat_exit_codes(tmp_path, monkeypatch):
    import json
    import time as _t
    site = tmp_path
    (ah.out_dir(site) / al.STATUS_NAME).write_text(json.dumps(
        {"t": _t.time(), "state": "LISTENING", "coverage_state": "GREEN", "coverage": 0.8}))
    assert al.main(["heartbeat", "--site", str(site)]) == al.EXIT_GREEN
    (ah.out_dir(site) / al.STATUS_NAME).write_text(json.dumps(
        {"t": _t.time(), "state": "LISTENING", "coverage_state": "RED", "coverage": 0.2}))
    assert al.main(["heartbeat", "--site", str(site)]) == al.EXIT_RED
    (ah.out_dir(site) / al.STATUS_NAME).unlink()
    assert al.main(["heartbeat", "--site", str(site)]) == al.EXIT_UNKNOWN


def test_hint_row_chains_call_watcher_and_silence_does_not(monkeypatch, tmp_path):
    chained = []
    monkeypatch.setattr(ah, "chain_call_watcher", lambda site: chained.append(site))
    monkeypatch.setattr(ah, "emit", lambda site, row: None)
    monkeypatch.setattr(ah, "gate_wav", lambda p: "PASS")
    monkeypatch.setattr(ah, "text_gate", lambda t: "PASS")
    monkeypatch.setattr(ah, "transcribe", lambda *a, **k: "Миша включи свет")
    monkeypatch.setattr(al, "classify", lambda wav, row, v, site, spot, confirm: row.update(status="HINT", lang="ru", text="x"))
    seg = al.Segment(1.0, b"\0" * (al.SEG_S * al.BPS), 7.0)
    al.process(seg, al.Ledger(), tmp_path, vocab(), "livingroom", "plughw:0,0")
    assert chained == [tmp_path]
    monkeypatch.setattr(al, "classify", lambda wav, row, v, site, spot, confirm: row.update(status="SILENT_OR_DROPPED"))
    al.process(seg, al.Ledger(), tmp_path, vocab(), "livingroom", "plughw:0,0")
    assert chained == [tmp_path]


def test_coverage_just_under_half_reads_red_not_rounded_green():
    ledger = al.Ledger()
    ledger.add("wall", 10000.0)
    ledger.add("captured", 10000.0)
    ledger.add("analysed", 4996.0)  # raw 0.4996 rounds to 0.5 at three places
    doc = al.status_doc(ledger, "LISTENING")
    assert doc["coverage_state"] == "RED"


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


def real_vocab():
    """The operator's vocabulary as written in the household site file (copied, not read from the site)."""
    prompt = ("Миша, Мише, Мишка, Mishe, Mishi, Meshie, Misha, Бруно, Розалия, Rozalia, Роза, Илья, "
              "стоп, хватит слушать, stop listening.")
    return {
        "prompt": prompt,
        "hint_words": {ah.tok(w) for w in ["mishe", "миша", "мише", "мишка", "misha", "meshie", "mishi"]},
        "stop_words": [ah.tok(w) for w in ["стоп", "stop"]],
        "stop_phrases": [tuple(ah.tokens(p)) for p in ["хватит слушать", "stop listening"]],
    }


def test_prompt_echo_from_quiet_segment_is_not_a_call():
    # DM 297993: the 09:58Z hint row was the prompt echoed back on a quiet segment
    echo = "Миша, Мише, Мише, Мише, Мишка, Mishe, Бруно,"
    assert al.hint_match(echo, real_vocab())[0] is None
    idx, _, toks = ah.find_hint(echo, real_vocab())
    assert al.call_rejection(echo, idx, toks, real_vocab()).startswith("prompt echo: a word repeats")


def test_bare_name_with_no_command_is_not_a_call():
    # DM 297993: the 10:26Z row "Миша," alone
    assert al.hint_match("Миша,", real_vocab())[0] is None


def test_full_prompt_echo_is_rejected():
    full = real_vocab()["prompt"]
    assert al.hint_match(full, real_vocab())[0] is None


def test_real_call_with_command_is_accepted():
    assert al.hint_match("Миша включи свет", real_vocab())[0] is not None
    assert al.hint_match("Mishe turn on the light", real_vocab())[0] is not None


def test_short_stop_command_is_still_a_stop():
    v = real_vocab()
    found, reason = al.hint_match("Миша стоп", v)
    assert found is not None and reason is None
    assert ah.is_stop(found[2][found[0] + 1:], v)


def test_echo_at_spot_stage_is_spot_unconfirmed(monkeypatch, tmp_path):
    wav = tmp_path / "w.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(ah, "gate_wav", lambda p: "PASS")
    monkeypatch.setattr(ah, "text_gate", lambda t: "PASS")
    monkeypatch.setattr(ah, "transcribe", lambda *a, **k: "Миша, Мише, Мише, Мише, Мишка, Mishe, Бруно,")
    row = al.classify(wav, {}, real_vocab(), tmp_path, al.TINY, al.BASE)
    assert row["status"] == "SPOT_UNCONFIRMED"
    assert row["reject"].startswith("prompt echo")
    assert "hint" not in row and "text" not in row


def test_hint_reading_observes_through_listener_rows_only(tmp_path):
    import json
    import time as _t
    now = _t.time()
    assert al.hint_reading(None, now)[0] == "UNKNOWN"
    assert al.hint_reading({"mode": "listen", "t": now - 200, "status": "NO_HINT"}, now)[0] == "UNKNOWN"  # stale
    assert al.hint_reading({"mode": "listen", "t": now, "status": "UNKNOWN"}, now)[0] == "UNKNOWN"
    assert al.hint_reading({"mode": "listen", "t": now, "status": "SUSPENDED"}, now)[0] == "UNKNOWN"
    assert al.hint_reading({"mode": "listen", "t": now, "status": "NO_HINT"}, now)[0] == "GREEN"
    site = tmp_path
    path = ah.out_dir(site) / "audio.jsonl"
    path.write_text(
        json.dumps({"mode": "window", "t": now, "status": "UNKNOWN"}) + "\n"
        + json.dumps({"mode": "listen", "t": now - 5, "status": "NO_SPEECH"}) + "\n"
        + "{torn line\n"
        + json.dumps({"mode": "window", "t": now, "status": "UNKNOWN"}) + "\n")
    row = al.last_listen_row(site)
    assert row["status"] == "NO_SPEECH"  # window rows and torn lines are ignored
    assert al.main(["hint", "--site", str(site)]) == al.EXIT_GREEN
    path.unlink()
    assert al.main(["hint", "--site", str(site)]) == al.EXIT_HINT_UNKNOWN
