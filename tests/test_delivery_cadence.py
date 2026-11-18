"""The tracked delivery cadence: its timing gate starts one promotion, and it never starts one from inside a wake."""
import importlib.util
import subprocess
import sys
import time
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
    monkeypatch.setattr(cadence, "gate_running", lambda home: called.append("gate") or False)
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: called.append("run"))
    assert cadence.run(Path("."), Path("."), now=0.0) == 0
    assert called == [], "nothing is inspected or started from inside a wake"


def test_run_does_not_start_a_promotion_when_the_pin_is_head(monkeypatch):
    def fake_git(repo, *args):
        out = HEAD + "\n" if args[:1] == ("rev-parse",) else "0\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: HEAD)
    monkeypatch.setattr(cadence, "_git", fake_git)
    started = []
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: started.append(a))
    assert cadence.run(Path("."), Path("."), now=0.0) == 0
    assert started == [], "the installed promotion is not started when the pin already names HEAD"


def test_missing_delivery_launcher_reports_unknown_and_does_not_suppress_retry(monkeypatch, tmp_path):
    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: PIN)
    monkeypatch.setattr(cadence, "_remote_ci", lambda *args: "unknown")
    def fake_git(repo, *args):
        out = {"rev-parse": HEAD, "log": "0", "merge-base": PIN, "rev-list": "1"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cadence, "_git", fake_git)
    assert cadence.run(tmp_path, tmp_path, now=10000) == 3
    assert not (tmp_path / "delivery-cadence.last").exists()


def test_failed_delivery_launcher_reports_unknown(monkeypatch, tmp_path):
    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: PIN)
    monkeypatch.setattr(cadence, "_remote_ci", lambda *args: "unknown")
    def fake_git(repo, *args):
        out = {"rev-parse": HEAD, "log": "0", "merge-base": PIN, "rev-list": "1"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cadence, "_git", fake_git)
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "launch failed"))
    assert cadence.run(tmp_path, tmp_path, now=10000) == 3


def test_uncertain_launch_remains_unknown_during_retry_bound(monkeypatch, tmp_path):
    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: PIN)
    monkeypatch.setattr(cadence, "_remote_ci", lambda *args: "unknown")
    def fake_git(repo, *args):
        out = {"rev-parse": HEAD, "log": "0", "merge-base": PIN, "rev-list": "1"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cadence, "_git", fake_git)
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "launch failed"))
    assert cadence.run(tmp_path, tmp_path, now=10000) == 3
    assert cadence.run(tmp_path, tmp_path, now=10001) == 3


def test_timed_out_launcher_preserves_uncertain_attempt(monkeypatch, tmp_path):
    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: PIN)
    monkeypatch.setattr(cadence, "_remote_ci", lambda *args: "unknown")
    def fake_git(repo, *args):
        out = {"rev-parse": HEAD, "log": "0", "merge-base": PIN, "rev-list": "1"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cadence, "_git", fake_git)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 120)
    monkeypatch.setattr(cadence.subprocess, "run", timeout)
    assert cadence.run(tmp_path, tmp_path, now=10000) == 3
    assert cadence._read_state(tmp_path) == (HEAD, 10000, "unknown")
    assert cadence.run(tmp_path, tmp_path, now=10001) == 3


def test_successful_launcher_records_started_attempt(monkeypatch, tmp_path):
    monkeypatch.delenv("MISHE_WAKE_KEY", raising=False)
    monkeypatch.setattr(cadence, "gate_running", lambda home: False)
    monkeypatch.setattr(cadence, "pin_sha", lambda home: PIN)
    monkeypatch.setattr(cadence, "_remote_ci", lambda *args: "unknown")
    def fake_git(repo, *args):
        out = {"rev-parse": HEAD, "log": "0", "merge-base": PIN, "rev-list": "1"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cadence, "_git", fake_git)
    monkeypatch.setattr(cadence.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "started", ""))
    assert cadence.run(tmp_path, tmp_path, now=10000) == 0
    assert cadence._read_state(tmp_path) == (HEAD, 10000, "started")


def test_gate_sees_a_delivery_request_holding_admission(monkeypatch, tmp_path):
    """A consumed request runs its suite in the consumer's own process with admission held.

    The 2026-11-18 06:13:55Z promotion (record 56262f54-1794982435362421925) was started
    by mishe-wake@genome.service, so no argv or moving-pin-delivery unit named it; only
    work/delivery-admission.json existed for its whole check stage.
    """
    monkeypatch.setattr(cadence, "_units_running", lambda: False)
    monkeypatch.setattr(cadence, "_running", lambda cmd: False)
    assert not cadence.gate_running(tmp_path), "no admission and no unit: the cadence may start"
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "delivery-admission.json").write_text("{}")
    assert cadence.gate_running(tmp_path), "the cadence would start a second promotion beside a live one"


def test_gate_sees_a_release_advance_started_outside_its_unit(monkeypatch, tmp_path):
    """`mishe-promote`'s promote-head.sh execs release advance, so a hand-run one is seen."""
    monkeypatch.setattr(cadence, "_units_running", lambda: False)
    proc = subprocess.Popen(
        ["bash", "-c", 'exec -a "python -m mishe_tauftauf release advance" sleep 30'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if cadence.gate_running(tmp_path):
                break
            time.sleep(0.05)
        assert cadence.gate_running(tmp_path), "the cadence would start a second promotion beside a live one"
    finally:
        proc.kill()
        proc.wait()
