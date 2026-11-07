"""The responder never speaks while any quiet flag exists, and plays through the Note3 script."""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("household_responder", TOOLS / "household_responder.py")
resp = importlib.util.module_from_spec(spec)
sys.modules["household_responder"] = resp
spec.loader.exec_module(resp)


def _site(tmp_path):
    work = tmp_path / resp.WORK
    work.mkdir(parents=True)
    return tmp_path, work


def test_human_quiet_flag_blocks_speech(tmp_path, monkeypatch):
    site, work = _site(tmp_path)
    assert resp.quiet(site) is False
    (work / "speech-quiet.json").write_text("{}\n")
    assert resp.quiet(site) is True
    called = []
    monkeypatch.setattr(resp.subprocess, "run", lambda *a, **k: called.append(a))
    assert resp.speak("hello", "en", site) == 3 and not called


def test_speak_plays_through_note3_and_logs(tmp_path, monkeypatch):
    site, work = _site(tmp_path)
    seen = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(resp.subprocess, "run", lambda cmd, **k: seen.append(cmd) or Done())
    assert resp.speak("привет", "ru", site) == 0
    assert str(resp.NOTE3_SAY) == seen[0][0] and seen[0][1] == "привет"
    assert (work / "responder.jsonl").exists()
