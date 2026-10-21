import hashlib
import json
import subprocess
import sys
import time

import pytest

from mishe_tauftauf import observer_verdict_watch
from mishe_tauftauf.feed import Feed

DISAGREE_OUTPUT = """observer  : /tmp/observer
reference : /tmp/reference
published : 2026-10-20T23:53:25Z (age 20s)
observer  : DISAGREE — the running observer's reading differs from the reviewed tree's reading
     state: observed='UNKNOWN' reference='KNOWN'
     problems: observed-only 'live renderer import roots unavailable: core-suite'
published : AGREE — the published reading equals the reviewed tree's reading
"""


def stub_check(tmp_path, output, exit_code):
    path = tmp_path / "check.py"
    path.write_text("#!/usr/bin/env python3\n"
                    "import sys\n"
                    f"sys.stdout.write({output!r})\n"
                    f"raise SystemExit({exit_code})\n")
    return path


def test_parse_reads_the_differences_the_check_names():
    assert observer_verdict_watch.parse(DISAGREE_OUTPUT) == [
        "state: observed='UNKNOWN' reference='KNOWN'",
        "problems: observed-only 'live renderer import roots unavailable: core-suite'",
    ]
    assert observer_verdict_watch.parse("observer  : AGREE — equal\n") == []


def test_summarize_names_the_fault_or_the_reason_it_could_not_decide():
    assert observer_verdict_watch.summarize(0, "observer  : AGREE — equal\n").startswith(
        "the running observer's reading and the published reading both equal")
    assert observer_verdict_watch.summarize(2, DISAGREE_OUTPUT) == \
        "state: observed='UNKNOWN' reference='KNOWN'"
    assert observer_verdict_watch.summarize(3, "UNKNOWN — the published substrate snapshot is missing\n") == \
        "the published substrate snapshot is missing"
    assert observer_verdict_watch.summarize(3, "") == "the check could not decide"


def test_observe_reports_the_exit_contract_and_the_check_digest(tmp_path):
    check = stub_check(tmp_path, DISAGREE_OUTPUT, 2)
    row = observer_verdict_watch.observe(tmp_path, check)
    assert (row["exit"], row["verdict"]) == (2, "DISAGREE")
    assert row["check_sha256"] == hashlib.sha256(check.read_bytes()).hexdigest()
    assert row["problems"]
    row = observer_verdict_watch.observe(tmp_path, stub_check(tmp_path, "UNKNOWN — no import root\n", 3))
    assert (row["exit"], row["verdict"]) == (3, "UNKNOWN")
    assert observer_verdict_watch.observe(tmp_path, tmp_path / "absent.py")["verdict"] == "UNKNOWN"


def test_should_route_bounds_an_undecidable_reading_by_age():
    """UNKNOWN is legal: it routes only once it has persisted across the cutoff."""
    assert not observer_verdict_watch.should_route(0, 0, 3, None, "agreed")
    assert observer_verdict_watch.should_route(2, 1, 3, None, "state differs")
    assert not observer_verdict_watch.should_route(3, 1, 3, None, "no import root")
    assert not observer_verdict_watch.should_route(3, 2, 3, None, "no import root")
    assert observer_verdict_watch.should_route(3, 3, 3, None, "no import root")
    # Already routed for this shape: no repeat until the shape itself changes.
    assert not observer_verdict_watch.should_route(3, 4, 3, "no import root", "no import root")
    assert observer_verdict_watch.should_route(3, 4, 3, "no import root", "unreadable module")
    assert not observer_verdict_watch.should_route(2, 1, 3, "state differs", "state differs")


@pytest.mark.parametrize("exit_code,output", [(2, DISAGREE_OUTPUT), (3, "UNKNOWN — no import root\n")])
def test_run_routes_a_persistent_condition_through_the_detector(tmp_path, exit_code, output):
    """The controlled failure: a check that never agrees must reach health."""
    home = tmp_path / "site"
    home.mkdir()
    check = stub_check(tmp_path, output, exit_code)
    process = subprocess.Popen(
        [sys.executable, "-m", "mishe_tauftauf.observer_verdict_watch",
         "--home", str(home), "--check", str(check), "--interval", "0.2", "--cutoff", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    alert = home / "health/observer-verdict-alert.md"
    verdict = home / "health/observer-verdict.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not alert.exists():
        if process.poll() is not None:
            pytest.fail(process.stdout.read() + process.stderr.read())
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    assert alert.exists(), "a disagreement or a persistent UNKNOWN must reach the resolver"
    row = json.loads(verdict.read_text())
    assert row["verdict"] == ("DISAGREE" if exit_code == 2 else "UNKNOWN")
    entries = [entry for entry in Feed(home).entries() if entry.source == "observer-verdict"]
    assert entries and entries[-1].body.startswith("[dm] to=health")
    assert "Resolver health" in alert.read_text()
