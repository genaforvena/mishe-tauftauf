#!/usr/bin/env python3
"""Detector: the plant's outcome channel must be open under the bytes it runs.

``wall outcome`` is the plant's instrument for recording an evidenced
contribution. Its writer refuses closed when it cannot derive the complete
set of already-bound evidence paths, because a publication that reused a bound
path would break the write-once rule. On 2026-10-19T16:38:42Z one tape row
(sequence 66107, witness) claimed to be an outcome publication without carrying
the dialect's shape. The pinned release fails closed on the *presence* of any
such row, so from that moment every role's publication was refused, plant-wide,
and the only way to learn about it was to attempt a publication and be refused.

Two observations are missing from the plant:

* **the channel state** — whether the running bytes can publish at all, and
  which tape rows decide it. ``sense.ledger.evidence-binding`` reports
  ``coverage_incomplete`` (a count of gaps) under the reviewed tree's bytes; it
  does not report the consequence for the bytes the plant runs.
* **denial observability** — whether a refusal leaves a record. The pinned
  release's writer has no refusal-audit call, so its refusals leave nothing in
  ``logs/outcome-refusal-audit.jsonl``: the plant's refusal count is a floor,
  not a total.

This check measures both with the candidate bytes themselves, and compares the
bytes the plant runs with a clean export of the reviewed HEAD tree.

The channel state is measured on the live plant's own tape. The denial probe is
measured on a *disposable* home that carries one damaged, binding-relevant row,
so the plant-wide denial is reproduced and observed without writing to the
plant: the probe's refusal must leave a record in the disposable home's
``logs/outcome-refusal-audit.jsonl``.

Exit status:
  0  the running bytes can publish an outcome, and a refusal would leave a record
  2  the outcome channel is closed under the running bytes
  3  the channel is open, but a refusal would leave no record
  4  the question cannot be decided: no home, no pin, no reviewed tree, an
     unreadable tape, or bytes that answer for another import root

The verdict is the last line of the output; the lines before it say what was
compared. A runner may read the exit status alone.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "mishe_tauftauf"

# The child rebuilds its own import path: the given root plus the standard
# library. An ambient installation, an editable install or a PYTHONPATH entry
# would otherwise answer for the root under test, and the check would read
# agreement where there is none.
CHILD = r"""
import json
import os
import shutil
import sys
import sysconfig
import tempfile
from pathlib import Path

root = os.path.abspath(sys.argv[1])
stdlib = {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
keep = []
for entry in sys.path:
    if not entry:
        continue
    resolved = os.path.abspath(entry)
    if any(resolved == base or resolved.startswith(base + os.sep) for base in stdlib):
        keep.append(entry)
sys.path = [root] + keep

home = Path(sys.argv[2])

from mishe_tauftauf import outcome_events
from mishe_tauftauf import wall
from mishe_tauftauf.discovery import EVIDENCE_BINDING_CLAUSE_TIME

module = os.path.abspath(wall.__file__)
if not module.startswith(root + os.sep):
    raise SystemExit("the package came from " + module + ", not from " + root)

coverage = outcome_events.classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME)
rows = [{"sequence": row.get("sequence"), "reason": row.get("reason"),
         "binding": row.get("binding", "unscoped")}
        for row in coverage["incomplete"]]

channel = "open"
reason = ""
bound = None
try:
    bound = wall._bound_evidence_paths(home)
except Exception as exc:
    channel = "closed"
    reason = str(exc)

# The denial probe runs on a disposable home: one damaged row that carries a
# record reference, so it is binding-relevant under every rule and blocks the
# publication. The plant's tape is never written to.
probe = Path(tempfile.mkdtemp(prefix="outcome-channel-probe-"))
denial = "not-measured"
denial_reason = ""
try:
    # The probe writes only into its own disposable home, so the writer's home
    # binding is pointed at that home for the probe alone.
    os.environ["MISHE_SEED_HOME"] = str(probe)
    (probe / "records").mkdir()
    (probe / "artifacts").mkdir()
    digest = "0" * 64
    body = "\n".join([
        "Wall outcome hypothesis-changed by probe",
        "probe prose",
        "[record] records/%s.json sha256=%s" % (digest, digest),
    ]) + "\n"
    frame = "v1 00000000000000000001 2026-10-20T00:00:00.000000Z probe ::\n"
    frame += "".join("    | %s\n" % line for line in body.splitlines())
    frame += "    .-\n"
    (probe / "chat.log").write_text(frame, encoding="utf-8")
    evidence = probe / "artifacts" / "probe.md"
    evidence.write_text("probe evidence\n", encoding="utf-8")
    try:
        wall.outcome(probe, "research-methods", "hypothesis-changed", "probe publication", evidence)
        denial = "no-refusal"
    except ValueError as exc:
        denial_reason = str(exc)
        audit = probe / "logs" / "outcome-refusal-audit.jsonl"
        denial = "observed" if audit.exists() and audit.read_text(encoding="utf-8").strip() else "silent"
    except Exception as exc:
        denial = "error"
        denial_reason = type(exc).__name__ + ": " + str(exc)
finally:
    shutil.rmtree(probe, ignore_errors=True)

print(json.dumps({
    "root": root,
    "channel": channel,
    "reason": reason,
    "rows": rows,
    "events": len(coverage["events"]),
    "unavailable": coverage["unavailable"],
    "bound": None if bound is None else len(bound),
    "denial": denial,
    "denial_reason": denial_reason,
}))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def pin_root(home: Path) -> Path:
    """The import root of the bytes the plant is pinned to."""
    path = home / "health" / "runtime-release.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Undecidable("no runtime pin record at " + str(path))
    except (OSError, ValueError) as exc:
        raise Undecidable("the runtime pin record is unreadable: " + type(exc).__name__)
    if not isinstance(record, dict) or not isinstance(record.get("source"), str):
        raise Undecidable("the runtime pin record names no source")
    root = Path(record["source"]) / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the pinned source carries no plant package")
    return root


def export_head(repo: Path, workdir: Path) -> Path:
    """A clean export of the reviewed HEAD tree.

    The reference is the reviewed tree and not the working tree's uncommitted
    bytes, so an unreviewed change cannot make the check agree by itself.
    """
    archive = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", "src"],
                             capture_output=True)
    if archive.returncode:
        raise Undecidable("cannot export HEAD from " + str(repo))
    extract = subprocess.run(["tar", "-x", "-C", str(workdir)], input=archive.stdout,
                             capture_output=True)
    if extract.returncode:
        raise Undecidable("cannot unpack the HEAD export")
    root = workdir / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the HEAD export carries no plant package")
    return root


def run_child(root: Path, home: Path, cwd: Path) -> dict:
    """Ask the code under one import root about the channel it offers."""
    result = subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home)],
                            capture_output=True, text=True, cwd=str(cwd))
    if result.returncode:
        raise Undecidable("the measurement under " + str(root) + " failed: "
                          + result.stderr.strip()[-300:])
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the measurement under " + str(root) + " printed no readable answer")
    if not isinstance(payload, dict) or payload.get("channel") not in {"open", "closed"}:
        raise Undecidable("the measurement under " + str(root) + " printed no readable answer")
    return payload


def assess(running: dict, reference: dict) -> tuple:
    """The verdict from two byte sets' measurements of the same plant."""
    if running.get("denial") in {"no-refusal", "error"}:
        return 4, "UNKNOWN — the denial probe did not reproduce a refusal", (
            "the probe under the running bytes reported " + str(running.get("denial"))
            + (": " + running.get("denial_reason") if running.get("denial_reason") else ""))
    if running.get("channel") == "closed":
        if reference.get("channel") == "open":
            cause = ("the reviewed tree's bytes publish where the running bytes refuse; "
                     "the tape's rows decide it")
        else:
            cause = ("the reviewed tree's bytes refuse as well; the tape's rows block both")
        return 2, "CLOSED — the running bytes refuse every outcome publication", cause
    if running.get("denial") != "observed":
        return 3, ("SILENT — the channel is open, but a refused publication would leave "
                   "no record"), "the running bytes' writer has no refusal-audit call"
    return 0, ("OPEN — the running bytes can publish an outcome, and a refusal would "
               "leave a record"), ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--release-src", type=Path, default=None,
                        help="import root of the running bytes (default: the plant's pin)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reference tree (default: a clean HEAD export)")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    if not (home / "chat.log").is_file():
        print("UNKNOWN — no tape at " + str(home / "chat.log")
              + "; the channel cannot be measured against a site that has no tape")
        return 4
    workdir = Path(tempfile.mkdtemp(prefix="outcome-channel-state-"))
    try:
        running_root = (args.release_src.resolve() if args.release_src
                        else pin_root(home))
        reference_root = (args.reference_src.resolve() if args.reference_src
                          else export_head(repo, workdir))
        running = run_child(running_root, home, workdir)
        reference = run_child(reference_root, home, workdir)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 4
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("home       :", home)
    print("running    :", running_root)
    print("reference  :", reference_root)
    print("events     :", running.get("events"), "outcome publications read from the tape")
    print("running gaps :", ", ".join(
        "%s(%s)" % (row.get("sequence"), row.get("binding")) for row in running.get("rows", []))
        or "none")
    print("reference gaps:", ", ".join(
        "%s(%s)" % (row.get("sequence"), row.get("binding")) for row in reference.get("rows", []))
        or "none")
    print("running channel  :", running.get("channel"),
          ("- " + running.get("reason")) if running.get("reason") else "")
    print("reference channel:", reference.get("channel"),
          ("- " + reference.get("reason")) if reference.get("reason") else "")
    print("running denial   :", running.get("denial"),
          ("- " + running.get("denial_reason")) if running.get("denial_reason") else "")
    print("reference denial :", reference.get("denial"),
          ("- " + reference.get("denial_reason")) if reference.get("denial_reason") else "")
    code, verdict, cause = assess(running, reference)
    if cause:
        print("cause      :", cause)
    print("verdict    :", verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
