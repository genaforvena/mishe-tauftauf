"""The outcome-channel detector must name a closed channel and a silent denial.

The plant's ``wall outcome`` writer fails closed when it cannot derive the
complete set of bound evidence paths. On 2026-10-19 one tape row (sequence
66107) claimed to be an outcome publication without carrying the dialect's
shape, and the pinned release — which fails closed on the presence of any such
row — refused every role's publication from then on.

The detector measures two things with the candidate bytes themselves: whether
the channel is open, and whether a refusal would leave a record. These tests
pin the decision logic and the measurement, including the two row shapes that
decide it:

* a damaged row that carries no record reference binds nothing, so a
  binding-scoped rule publishes through it;
* a damaged row that carries a record reference may hide a bound path, so it
  blocks every publication under every rule.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "outcome-channel-state.py"
SRC = REPO / "src"

# A row whose record reference names a record the store does not hold: the
# binding is unknown, so it blocks.
BLOCKING_BODY = "\n".join([
    "Wall outcome hypothesis-changed by probe",
    "probe prose",
    "[record] records/" + "0" * 64 + ".json sha256=" + "0" * 64,
]) + "\n"

# The shape of tape row 66107: the reserved prefix, the prose on the header's
# line, and no record reference at all.
UNBOUND_BODY = ("Wall outcome hypothesis-changed by witness # a wake's wall text "
                "on the header's own line, with no reference\n")


def load_check():
    """The check's decision logic, imported as a module."""
    spec = importlib.util.spec_from_file_location("outcome_channel_state", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_home(home: Path, body: str) -> Path:
    """A site home whose tape carries one damaged outcome row."""
    (home / "records").mkdir(parents=True)
    frame = "v1 00000000000000000001 2026-10-20T00:00:00.000000Z probe ::\n"
    frame += "".join("    | %s\n" % line for line in body.splitlines())
    frame += "    .-\n"
    (home / "chat.log").write_text(frame, encoding="utf-8")
    return home


def run_check(home: Path, release: Path = SRC, reference: Path = SRC):
    """Run the check the way a runner would, and return its result."""
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home),
         "--release-src", str(release), "--reference-src", str(reference)],
        capture_output=True, text=True)


def test_a_binding_relevant_damaged_row_closes_the_channel(tmp_path):
    """A damaged row that may hide a binding blocks every publication."""
    home = write_home(tmp_path / "site", BLOCKING_BODY)
    result = run_check(home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "CLOSED" in result.stdout


def test_a_damaged_row_without_a_reference_does_not_close_the_channel(tmp_path):
    """The shape of row 66107 binds nothing, so a binding-scoped rule publishes."""
    home = write_home(tmp_path / "site", UNBOUND_BODY)
    result = run_check(home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OPEN" in result.stdout


def test_the_row_that_decides_the_channel_is_named(tmp_path):
    """The finding names the tape row, so the cause is not a mystery."""
    home = write_home(tmp_path / "site", BLOCKING_BODY)
    result = run_check(home)
    assert "1" in result.stdout.split("running gaps :")[1].splitlines()[0]


def test_a_closed_channel_says_the_tape_blocks_both_byte_sets(tmp_path):
    """The cause separates a stale pin from a fault in the tape."""
    home = write_home(tmp_path / "site", BLOCKING_BODY)
    result = run_check(home, reference=SRC)
    assert "the reviewed tree's bytes refuse as well" in result.stdout


def test_assess_names_a_stale_pin_as_the_cause():
    """Only the running bytes refuse: the reviewed tree publishes, so the pin is stale."""
    module = load_check()
    code, _verdict, cause = module.assess(
        {"channel": "closed", "denial": "silent"}, {"channel": "open", "denial": "observed"})
    assert code == 2
    assert "the reviewed tree's bytes publish where the running bytes refuse" in cause


def test_assess_names_a_closed_channel():
    """A closed channel is the plant-wide refusal, whatever the reference does."""
    module = load_check()
    code, verdict, _cause = module.assess(
        {"channel": "closed", "denial": "observed"}, {"channel": "open", "denial": "observed"})
    assert code == 2
    assert "CLOSED" in verdict


def test_assess_reports_a_silent_denial_separately():
    """An open channel whose refusals leave no record is its own fault."""
    module = load_check()
    code, verdict, _cause = module.assess(
        {"channel": "open", "denial": "silent"}, {"channel": "open", "denial": "observed"})
    assert code == 3
    assert "SILENT" in verdict


def test_assess_is_quiet_when_the_channel_is_open_and_observable():
    """Nothing to report: the instrument works and its denials are recorded."""
    module = load_check()
    code, verdict, _cause = module.assess(
        {"channel": "open", "denial": "observed"}, {"channel": "open", "denial": "observed"})
    assert code == 0
    assert "OPEN" in verdict


def test_assess_reads_a_broken_probe_as_undecidable():
    """A probe that did not reproduce a denial is not evidence of an open channel."""
    module = load_check()
    for denial in ("no-refusal", "error"):
        code, verdict, _cause = module.assess(
            {"channel": "open", "denial": denial}, {"channel": "open", "denial": "observed"})
        assert code == 4
        assert "UNKNOWN" in verdict


def test_a_missing_home_reads_unknown(tmp_path):
    """Without a tape there is nothing to decide."""
    result = run_check(tmp_path / "absent")
    assert result.returncode == 4
    assert result.stdout.startswith("UNKNOWN")


def test_the_check_writes_nothing_to_the_plant(tmp_path):
    """The probe is disposable: the site's own tape and logs stay untouched."""
    home = write_home(tmp_path / "site", BLOCKING_BODY)
    before = {path: path.read_bytes() for path in sorted(home.rglob("*")) if path.is_file()}
    result = run_check(home)
    assert result.returncode == 2
    after = {path: path.read_bytes() for path in sorted(home.rglob("*")) if path.is_file()}
    assert before == after
