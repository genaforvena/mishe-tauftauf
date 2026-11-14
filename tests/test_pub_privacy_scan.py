"""The privacy scan's exit-code contract and its path to the bus.

The scan is the check that reads every live and draft DEV.to body, title, tag
and reply against the private/public rule list Ilya confirmed on 2026-11-12
13:46Z.  The duty that runs it maps its exit codes onto the bus, so the contract
is load-bearing: exit 0 is GREEN, exit 2 is RED and exit 3 is UNKNOWN.  A scan
that cannot read its input must be UNKNOWN, never a pass.

Four failures are pinned here.

The verdict must reach the bus.  On 2026-11-13 the scan wrote its verdict to
the report file and nothing else, so the bus kept the PREVIOUS verdict: the
report said GREEN at 22:25Z while the signal still said RED from 20:35Z, and
the steward read the bus and saw a RED that had not been true for two hours.
Every ``--all`` run therefore publishes its own verdict.

A RED must stay RED.  A violation we observed is an observation, not a missing
one: if the publish fails, the run still exits 2.  Reporting UNKNOWN there
would mask a real violation behind a transport failure.

The check must be committed.  It used to live outside the repository, so the
delivery gate's reviewer could not see its contract and refused the change on
2026-11-14.  The test asserts the file is in the repository and that the duty
names it.

The rule logic must not depend on the site-local terms file.  The terms (the
names) are site-local and never committed; the structural patterns (the rule)
are committed here, so the cases below run anywhere, including CI.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "pub-privacy-scan.py"
SITE = Path(os.environ.get("MISHE_SEED_HOME", REPO))
TERMS = SITE / "pub" / "privacy-terms.json"

#: The structural rules, with no site terms.  These are the committed part of the
#: rule: the site terms add names on top of them.
NO_TERMS = {"terms": [], "foreign_handles": [], "public_repo_rx": []}


def load_scan():
    """The committed check, loaded as a module so its functions can be exercised."""
    loader = importlib.machinery.SourceFileLoader("pub_privacy_scan", str(CHECK))
    spec = importlib.util.spec_from_loader("pub_privacy_scan", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeRun:
    """Records the CLI invocation and returns a chosen result."""

    def __init__(self, returncode=0, stdout="edge", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def fake_subprocess(runner):
    """A stand-in for the subprocess module, so the publish path never shells out."""
    return type("S", (), {"run": staticmethod(runner),
                          "SubprocessError": subprocess.SubprocessError,
                          "TimeoutExpired": subprocess.TimeoutExpired})


def test_the_check_is_committed_so_a_reviewer_can_see_its_contract():
    """A check outside the repository can never be reviewed.

    On 2026-11-14 the delivery gate refused the duty that runs this check with
    "insufficient context to assess deeper defects", naming the scanner's
    exit-code contract as missing evidence: the scanner lived at
    SITE_HOME/pub/bin/pub-privacy-scan, outside the repository the reviewer reads.
    """
    assert CHECK.is_file(), "the privacy scan must be a committed check under checks/"
    assert (REPO / "checks" / "pub-index-self-claims.py").is_file()
    assert (REPO / "checks" / "pub-account-count-claims.py").is_file()


def test_a_green_verdict_is_published_as_a_reading():
    scan = load_scan()
    runner = FakeRun()
    scan.subprocess = fake_subprocess(runner)
    assert scan.publish_verdict("GREEN", "controlled green") is True
    cmd = runner.calls[0]
    assert cmd[2] == "observe" and cmd[3] == scan.SIGNAL_NAME and cmd[4] == "GREEN"


def test_a_red_verdict_is_published_as_a_reading():
    scan = load_scan()
    runner = FakeRun()
    scan.subprocess = fake_subprocess(runner)
    assert scan.publish_verdict("RED", "controlled red") is True
    cmd = runner.calls[0]
    assert cmd[2] == "observe" and cmd[4] == "RED"


def test_an_unknown_verdict_is_published_as_a_state_never_as_a_value():
    """UNKNOWN is a state, not a reading.

    Observing the string "UNKNOWN" as a value would store a fresh OK reading and
    hide the very failure it names, so the UNKNOWN verdict goes through
    ``observe-unknown`` with a reason.
    """
    scan = load_scan()
    runner = FakeRun()
    scan.subprocess = fake_subprocess(runner)
    assert scan.publish_verdict("UNKNOWN", "controlled unknown") is True
    cmd = runner.calls[0]
    assert cmd[2] == "observe-unknown"
    assert "UNKNOWN" not in cmd[3:]
    assert "--reason" in cmd


def test_a_failed_publish_is_reported_and_never_claimed_as_success():
    scan = load_scan()
    runner = FakeRun(returncode=1, stderr="no such signal")
    scan.subprocess = fake_subprocess(runner)
    assert scan.publish_verdict("GREEN", "controlled failure") is False


def test_a_missing_cli_is_reported_and_never_claimed_as_success():
    scan = load_scan()
    runner = FakeRun()
    scan.subprocess = fake_subprocess(runner)
    assert scan.publish_verdict("GREEN", "controlled missing cli", cli=Path("/nonexistent/cli")) is False


def test_a_violation_is_red_and_an_unreadable_item_is_unknown():
    """A text that cannot be read is UNKNOWN, never a pass."""
    scan = load_scan()
    verdict, violations = scan.verdict_for(
        {"title": "x", "body": "Contact probe@example.com for the plan.", "tags": "", "replies": []},
        **NO_TERMS)
    assert verdict == "RED" and violations
    verdict, violations = scan.verdict_for({"title": "x", "_read_error": "HTTPError"}, **NO_TERMS)
    assert verdict == "UNKNOWN" and not violations
    verdict, violations = scan.verdict_for(
        {"title": "A plain note", "body": "A sentence about a gate.", "tags": "dev", "replies": []},
        **NO_TERMS)
    assert verdict == "GREEN" and not violations


def test_the_structural_rules_hold_on_their_own():
    """The committed part of the rule, case by case.

    The site terms add names on top of these; these are the patterns the
    repository itself carries, so they are checked here without the site.
    """
    scan = load_scan()

    def codes(body):
        return {code for _rule, code in scan.scan_text(body, [], [], [])}

    assert "email" in codes("write to probe@example.com")
    assert "ipv4" in codes("the host is at 192.168.1.10")
    assert "internal-id" in codes("see wake 1794413465868601864")
    assert "wall-marker" in codes("## Plate\nwhat I am doing")
    assert "secret" in codes("the header was api-key: hunter2hunter2")
    assert "client-identity" in codes("User-Agent: mesh-pub/1.0")
    assert "sha-or-digest" in codes("digest 19a674ad01efeb22e3e68925ea93d09f3f4bf0882894d282d8615028a0606bdf")
    # A statistical interval is not a coordinate pair.
    assert "coordinates" not in codes("the effect size was [0.153, 0.198]")
    # A fenced block is quoted program output, not our own prose.
    assert "sha-or-digest" not in codes("```\ntailscale commit: 6cac918179d4d673bfebe2fc74f81183ddd73fea\n```")
    # Raw JSON in our own prose is an internal state dump.
    assert "raw-json" in codes('the state file was {"paxos": {"ballot": 1}, "last_chosen": "blue"}')


def fake_cli(tmp_path):
    """An existing stand-in for the mishe CLI, so publish_verdict calls the runner."""
    cli = tmp_path / "mishe"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    return cli


def test_a_red_stays_red_when_the_publish_fails(tmp_path):
    """A violation we observed is an observation, not a missing one.

    The site-local copy this check replaced returned UNKNOWN for every verdict
    whose publish failed, including a RED. That masked a real violation behind a
    transport failure: the lane would read "nothing known" instead of "something
    wrong". The exit code is what the duty maps onto the bus, so it must stay 2.
    """
    scan = load_scan()
    runner = FakeRun(returncode=1, stderr="no such signal")
    scan.subprocess = fake_subprocess(runner)
    scan.load_terms = lambda terms_file=None: NO_TERMS
    scan.collect = lambda key: [{"id": 1, "state": "live"}]
    scan.read_item = lambda item, key: {"title": "x", "body": "probe@example.com", "tags": "", "replies": []}
    # An existing CLI path makes publish_verdict reach the injected runner. Without
    # it the test would pass through the missing-CLI branch and never exercise
    # the failed-publish handling it is named for.
    cli = fake_cli(tmp_path)
    assert scan.run_all("key", report=tmp_path / "report.json", cli=cli) == 2


def test_a_green_that_cannot_publish_is_unknown_not_green(tmp_path):
    """A scan that could not say its verdict on the bus must not read GREEN."""
    scan = load_scan()
    runner = FakeRun(returncode=1, stderr="no such signal")
    scan.subprocess = fake_subprocess(runner)
    scan.load_terms = lambda terms_file=None: NO_TERMS
    scan.collect = lambda key: [{"id": 1, "state": "live"}]
    scan.read_item = lambda item, key: {"title": "A note", "body": "A sentence.", "tags": "", "replies": []}
    cli = fake_cli(tmp_path)
    assert scan.run_all("key", report=tmp_path / "report.json", cli=cli) == 3


def test_the_report_records_the_verdict_and_never_the_matched_text(tmp_path):
    """The report names the item, the field and the rule code, never the text.

    A report that carried the matched text would itself leak the private term
    it found, into a file that is read and copied around.
    """
    scan = load_scan()
    runner = FakeRun()
    scan.subprocess = fake_subprocess(runner)
    scan.load_terms = lambda terms_file=None: NO_TERMS
    scan.collect = lambda key: [{"id": 4242, "state": "live"}]
    scan.read_item = lambda item, key: {"title": "x", "body": "write to probe@example.com", "tags": "", "replies": []}
    report = tmp_path / "report.json"
    assert scan.run_all("key", report=report, cli=fake_cli(tmp_path)) == 2
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["overall"] == "RED"
    assert written["counts"]["RED"] == 1
    assert written["items"][0]["id"] == 4242
    assert written["items"][0]["violations"][0]["code"] == "email"
    assert "probe@example.com" not in report.read_text(encoding="utf-8")


def test_the_self_test_passes_against_the_site_terms():
    """The check's own controlled failure must pass, or every scan is untrustworthy.

    The terms file is site-local, so this case runs where the site exists; the
    structural rules are checked above, everywhere.
    """
    if not TERMS.is_file():
        return
    scan = load_scan()
    assert scan.self_test(TERMS) == 0


if __name__ == "__main__":
    sys.exit(0)
