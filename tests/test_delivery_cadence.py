"""The tracked delivery cadence: its timing gate starts one promotion, and it never starts one from inside a wake."""
import importlib.util
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "delivery-cadence.py"

spec = importlib.util.spec_from_file_location("delivery_cadence", CHECK)
cadence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cadence)

PIN = "a" * 40
HEAD = "b" * 40


def test_starts_when_the_pin_trails_a_settled_head():
    start, reason = cadence.should_start(PIN, HEAD, newest_at=1000.0, last=None, now=2000.0)
    assert start and reason == "deliver"


def test_waits_while_the_newest_commit_is_fresh():
    start, reason = cadence.should_start(PIN, HEAD, newest_at=9990.0, last=None, now=10000.0)
    assert not start and "< 60s" in reason


def test_does_nothing_when_the_pin_is_head():
    start, reason = cadence.should_start(HEAD, HEAD, newest_at=0.0, last=None, now=10000.0)
    assert not start and "pin is HEAD" in reason


def test_does_not_retry_an_attempted_head_before_the_retry_bound():
    last = (HEAD, 9900.0)
    start, reason = cadence.should_start(PIN, HEAD, newest_at=0.0, last=last, now=10000.0)
    assert not start and "wait for HEAD to move" in reason
    start, _ = cadence.should_start(PIN, HEAD, newest_at=0.0, last=(PIN, 9900.0), now=10000.0)
    assert start, "a different sha than the one attempted is not blocked"
    start, _ = cadence.should_start(PIN, HEAD, newest_at=0.0, last=last, now=9900.0 + cadence.RETRY_S + 1)
    assert start, "an old attempt past the retry bound is retried"


def test_run_skips_inside_a_wake_without_anything(monkeypatch):
    called = []
    monkeypatch.setenv("MISHE_WAKE_KEY", "wake-1")
    monkeypatch.setattr(cadence, "gate_running", lambda: called.append("gate") or False)
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: called.append("run"))
    assert cadence.run(Path("."), Path("."), now=0.0) == 0
    assert called == [], "nothing is inspected or started from inside a wake"


def test_run_does_not_start_a_promotion_when_the_pin_is_head(monkeypatch):
    def fake_git(repo, *args):
        out = HEAD + "\n" if args[:1] == ("rev-parse",) else "0\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: HEAD)
    monkeypatch.setattr(cadence, "_git", fake_git)
    started = []
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: started.append(a))
    assert cadence.run(Path("."), Path("."), now=0.0) == 0
    assert started == [], "the installed promotion is not started when the pin already names HEAD"
