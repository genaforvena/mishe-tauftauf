"""The call watcher answers only recent, answerable calls, once, and never bypasses the responder's gates."""
import importlib.util
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("household_responder", TOOLS / "household_responder.py")
resp = importlib.util.module_from_spec(spec)
sys.modules["household_responder"] = resp
spec.loader.exec_module(resp)
wspec = importlib.util.spec_from_file_location("household_call_watcher", TOOLS / "household_call_watcher.py")
cw = importlib.util.module_from_spec(wspec)
sys.modules["household_call_watcher"] = cw
wspec.loader.exec_module(cw)

NOW = 1794069300.0


def _site(tmp_path):
    (tmp_path / resp.WORK).mkdir(parents=True)
    return tmp_path


def _write_rows(site, rows):
    (site / resp.WORK / "audio.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def _hint(t, text, lang="ru"):
    return {"status": "HINT", "t": t, "hint": text, "text": text, "lang": lang, "room": "livingroom"}


def test_strip_hint_and_intent():
    assert cw.strip_hint("Миша, который час?") == ("который час?", True)
    assert cw.strip_hint("Mishe, what time is it?") == ("what time is it?", True)
    assert cw.intent(cw.strip_hint("Миша, который час?")[0]) == "time"
    assert cw.intent(cw.strip_hint("Миша, мы смотрим футбол.")[0]) is None


def test_media_conversation_is_never_answered(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 5, "Миша, мы с тобой смотрим футбол.")])
    spoke = []
    monkeypatch.setattr(resp, "speak", lambda *a, **k: spoke.append(a) or 0)
    out = cw.once(site, now=NOW)
    assert spoke == [] and out == [{"t": NOW - 5, "action": "skip", "reason": "not an answerable call"}]


def test_answerable_call_speaks_once(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 5, "Миша, который час?")])
    spoke = []
    monkeypatch.setattr(resp, "speak", lambda text, lang, s: spoke.append((text, lang)) or 0)
    cw.once(site, now=NOW)
    cw.once(site, now=NOW + 1)  # same row again: must not repeat
    assert len(spoke) == 1 and spoke[0][1] == "ru" and "Сейчас" in spoke[0][0]


def test_stale_call_is_dropped(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - cw.CALL_MAX_AGE_S - 30, "Миша, который час?")])
    spoke = []
    monkeypatch.setattr(resp, "speak", lambda *a, **k: spoke.append(a) or 0)
    assert cw.once(site, now=NOW) == []
    assert spoke == []


def test_quiet_flag_refuses_and_is_not_retried(tmp_path, monkeypatch):
    site = _site(tmp_path)
    (site / resp.WORK / "speech-quiet.json").write_text("{}\n")
    _write_rows(site, [_hint(NOW - 5, "Миша, который час?")])
    called = []
    monkeypatch.setattr(resp.subprocess, "run", lambda *a, **k: called.append(a))
    out = cw.once(site, now=NOW)
    assert called == [] and out[0]["action"] == "refused" and out[0]["rc"] == 3
    assert cw.once(site, now=NOW + 1) == []  # handled, no retry


def test_busy_lock_keeps_call_for_retry(tmp_path, monkeypatch):
    site = _site(tmp_path)
    (site / resp.WORK / "speaking.lock").write_text(str(NOW + 50))
    _write_rows(site, [_hint(NOW - 5, "Миша, который час?")])
    monkeypatch.setattr(resp, "speak", lambda *a, **k: 4)
    out = cw.once(site, now=NOW)
    assert out[0]["rc"] == 4
    assert cw.load_state(site)["handled"] == []


def test_dry_run_speaks_nothing_and_keeps_state(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 5, "Миша, который час?")])
    monkeypatch.setattr(resp, "speak", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spoke")))
    out = cw.once(site, dry_run=True, now=NOW)
    assert out[0]["action"] == "dry-run" and "Сейчас" in out[0]["reply"]
    assert not (site / resp.WORK / cw.STATE).exists()


def test_greeting_plus_room_talk_is_not_a_call():
    # collision guard: a greeting followed by talk about the room is not addressed to Mishe
    rest, had = cw.strip_hint("Миша, Добрый день, мы смотрим футбол.")
    assert had and cw.intent(rest) is None
    assert cw.intent(cw.strip_hint("Миша, добрый день.")[0]) == "greet"


def test_stop_word_sets_room_quiet_and_answers_nothing(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 3, "Миша, стоп.")])
    spoke = []
    monkeypatch.setattr(resp, "speak", lambda *a, **k: spoke.append(a) or 0)
    out = cw.once(site, now=NOW)
    flag = site / resp.WORK / "speech-quiet.json"
    assert spoke == [] and flag.exists() and out[0]["action"] == "quiet"
    assert resp.quiet(site) is True
    flag.unlink()


def test_dry_run_writes_no_quiet_flag(tmp_path):
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 3, "Миша, стоп.")])
    cw.once(site, dry_run=True, now=NOW)
    assert not (site / resp.WORK / "speech-quiet.json").exists()


def test_quiet_flag_is_respected_by_a_chained_pass(tmp_path, monkeypatch):
    # a HINT row appended after a window, one pass, and the humans' quiet flag: nothing is spoken
    site = _site(tmp_path)
    (site / resp.WORK / "speech-quiet.json").write_text('{"by": "test"}\n')
    _write_rows(site, [_hint(NOW - 2, "Миша, который час?")])
    spoke = []
    monkeypatch.setattr(resp.subprocess, "run", lambda *a, **k: spoke.append(a) or 0)
    out = cw.once(site, now=NOW)
    assert spoke == [] and out[0]["action"] == "refused"


def test_overlapping_pass_is_skipped_while_lock_is_held(tmp_path, monkeypatch):
    import fcntl
    site = _site(tmp_path)
    _write_rows(site, [_hint(NOW - 5, "Миша, который час?")])
    spoke = []
    monkeypatch.setattr(resp, "speak", lambda *a, **k: spoke.append(a) or 0)
    held = (site / resp.WORK / cw.LOCK).open("a")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert cw.once(site, now=NOW) == []
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    assert spoke == []
    # the lock is released afterwards: the same call is answered by the next pass
    assert cw.once(site, now=NOW)[0]["action"] == "spoke"


def test_latin_transcriber_variants_are_hint_words():
    assert cw.strip_hint("Meshe, what time is it?") == ("what time is it?", True)
    assert cw.strip_hint("Mishy, where is Ilya") == ("where is Ilya", True)
    assert cw.strip_hint("Mishi, what time is it?") == ("what time is it?", True)
    # one edit only, Latin only, five letters or more: no false hints
    assert cw.is_hint_word("mise") is False
    assert cw.is_hint_word("mishes") is True  # one insertion from mishe: a transcriber variant
    assert cw.is_hint_word("mishaps") is False  # two edits from misha
    assert cw.is_hint_word("маша") is False
    assert cw.strip_hint("Маша, который час?") == ("Маша, который час?", False)
    assert cw.strip_hint("Миша, который час?") == ("который час?", True)
