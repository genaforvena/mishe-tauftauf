#!/usr/bin/env python3
"""Scheduled runner: the plant's observer checks are run, recorded and routed.

The gap this closes.  The plant owns a family of detector scripts under
``checks/`` that answer whether the evidence every pane shows is the evidence
the reviewed tree publishes — ``observer-view-agreement.py`` (projections
present?), ``observer-evidence-field-agreement.py`` (fields present within a
present line?), ``observer-verdict-agreement.py``, ``substrate-verdict-age.py``,
``outcome-channel-state.py``, ``observer-refresh-closure.py``.  Each is correct
and each is manual: nothing schedules them, so a difference is seen only when a
mind happens to run the check by hand.  Measured 2026-10-21: the observer
bundle's ``initiative.py`` predated ``d526771``, every published role row lacked
the ``checked-evidence-age`` field, and the plant learned it from a peer's wake
rather than from its own monitoring.

What this runner does, once per cycle:

1. Runs every check in ``INVENTORY`` from its **committed HEAD bytes**
   (``git show HEAD:checks/<name>.py``), never from the working tree, so an
   uncommitted edit cannot change what the plant's monitoring reads.
2. Runs the per-slug checks against every role the site declares
   (``minds/<role>``) that the plant renders (``top-pains/<role>``) and
   publishes a frame for (``dashboards/<role>.json``), skipping a target whose
   frame carries nothing for that check to read.  A skip is recorded with its
   reason; it is a statement about the check's applicability, not a fault.
3. Maps each exit code to a state: 0 OK, 2 FAIL, anything else UNKNOWN.  A
   check that cannot be run at all, or that cannot decide where it applies, is
   UNKNOWN — never silently OK.
4. Writes ``SITE/checks/observer-checks.json``: the cycle time, the role scope,
   every entry with its exit code and verdict line, the skip list, the checks
   that are deliberately not scheduled with their reasons, the confirmed problem
   set, the pending ids, and the last routed set.
5. Routes **on a confirmed change of the problem set only**.  These checks
   compare a live rendering with a fresh one, so one cycle can disagree and
   agree again by itself; an id therefore enters or leaves the problem set only
   after **two consecutive cycles** say the same thing, and the gate is the set
   of check ids, so a verdict *text* that moves between samples is not a change.
   An unchanged cycle is silent, and the runner cannot become a second firehose.

Liveness.  ``--loop`` runs the cycle and then ``--check``, which reads the state
file back and exits non-zero when it is missing or older than ``--max-age``: the
monitoring's own failure is RED, and the unit's watchdog lease stops with it.
The unit carries ``OnFailure`` to an alert router, so a dead runner is routed
rather than quiet.

Exit status (``--once``): 0 every applicable check OK; 2 a check reported a
difference; 3 a check was undecidable or could not be run; 4 the cycle itself
could not run.  Exit status (``--check``): 0 state present and fresh; 2 state
missing; 3 state stale or unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

GIT = "/usr/bin/git"
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# The marker the published frame uses for the evidence block.  A target whose
# frame carries it has something for the evidence checks to compare; one that
# does not is skipped rather than read as an undecidable problem.
EVIDENCE_MARKER = "shared follow-through evidence"

# The runtime verdict a published frame carries: "RUNTIME: <state> selection=...".
# The states are the ones the runtime-source verdict prints, so a frame with no
# such line has nothing for the pin-verdict check to read.  This name was used
# by the ``pin-verdict`` applicability branch below but never defined, so that
# branch raised NameError the moment a check asked for it (the gate's review
# found this on 2026-11-10; the branch was unreachable while every INVENTORY
# row carried no requirement, so nothing had exercised it).
PIN_VERDICT_RE = re.compile(
    r"^RUNTIME: (MATCH|DECLARED|DRIFT|UNKNOWN|NONE|UNPINNED)\b")

# (name, per-slug, accepts --repo, extra argv, what the target must carry)
INVENTORY = (
    ("outcome-channel-state.py", False, True, (), None),
    # The evidence-binding verdict's own consistency: a verdict of "verified"
    # must not sit over violating rows, and a verdict of "unknown" must rest on
    # a coverage gap that can hide a binding.  Plant level, so not per-slug.
    ("evidence-binding-verdict-consistency.py", False, True, (), None),
    # The field-review lane's landing contract: every review the branch
    # carries must name a real source and either a real target file or a
    # one-line discard.  The retired mesh's version of this lane lost 245 of
    # 376 reviews to "uncommitted in the tree (steward lands)"; an unlanded
    # review must not stay quiet here.  Plant level, so not per-slug.
    ("field-review-contract.py", False, True, (), None),
    # SITE/bin/mishe and mishe-tauftauf must run from any working directory: the
    # alert routers are systemd units with no WorkingDirectory, and a router that
    # cannot import cannot tell Health that a monitor failed (2026-10-31).
    ("cli-wrapper-cwd.py", False, True, (), None),
    # The containment guard probes /var/tmp, which moved to a second drive on 2026-10-31
    # after root-drive fsync stalls tripped the plant 11 times; this keeps the root visible.
    ("root-fs-stall.py", False, False, (), None),
    # runtime/current must equal the release of the last verified delivery; only the
    # delivery path writes it.  RED carries the restore command (incident 2026-11-10).
    ("pin-integrity.py", False, False, (), None),
    # The wakes window renders each model's newest retained output through the live view code.
    # A renderer failure is DMed to genome (resolver) once per distinct failure by the check itself.
    ("wakes-view-engines.py", False, True, ("--route",), None),
    # The shipped book states a rule about resolving a relative outcome-evidence
    # path.  The committed-tree gate (shipped-book-evidence.py) checks that
    # against the committed bytes, which is the reader's position; this one
    # checks it against the bytes the plant actually runs, the served release.
    # The two can disagree while every other gate passes, because the pin
    # moves on its own schedule.  Measured 2026-11-01 (Docs wake 157746): the
    # served release 91d5385 predates 81d04a3d, so a relative evidence path
    # is refused there while the committed tree accepts it, and that was the
    # plant's most frequent refusal class -- 172 of 206 outcome-writer
    # refusals in 24 hours, over every role.  Plant level.  Revert: remove
    # this row (2026-11-01).
    ("shipped-book-vs-served-release.py", False, True, (), None),
)

# Checks that exist and are not scheduled, with the measured reason.  Recorded
# in the state file so the exclusion is visible rather than silent.
NOT_SCHEDULED = (
    ("observer-source-identity.py",
     "its answer is why the running bytes have no commit identity, which is a "
     "property to read when asked, not a state that enters or leaves a problem set"),
)

OK, FAIL, UNKNOWN, SKIP = "OK", "FAIL", "UNKNOWN", "SKIP"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def child_env() -> dict:
    """The environment for every child, without the supervisor's notify socket.

    ``NOTIFY_SOCKET`` is inherited by everything the runner spawns, and a check
    that imports a module which notifies writes to the supervisor's socket from
    a child PID: systemd refuses it ("reception only permitted for main PID")
    and the journal fills with warnings that are not a runner fault.
    """
    return {key: value for key, value in os.environ.items() if key != "NOTIFY_SOCKET"}

def state_for(exit_code: int) -> str:
    return OK if exit_code == 0 else FAIL if exit_code == 2 else UNKNOWN


def verdict_line(text: str) -> str:
    """The last non-empty line of a check's output, clipped: its verdict."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:400]
    return "(no output)"

def detail_text(text: str) -> str:
    """The whole of a failing check's output, clipped: what the verdict line drops.

    ``verdict_line`` keeps the last non-empty line. For a check whose output is a
    list of field-level differences that is the last *item*, not the list: a
    13-role ``renderer_roots`` disagreement and a one-role one are recorded as
    the same string, so the state file cannot say which. Nothing in the routing
    gate or in the tape changes; the detail is additional, for the reader the
    routed message already names (``SITE/checks/observer-checks.json``).
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:2000]



def head_bytes(repo: Path, rel: str) -> tuple[bytes | None, str]:
    """The committed bytes of a check, or an error. Never the working tree."""
    result = subprocess.run([GIT, "-C", str(repo), "show", f"HEAD:{rel}"],
                            capture_output=True)
    if result.returncode:
        return None, (result.stderr.decode("utf-8", "replace").strip() or "git show failed")
    return result.stdout, ""


def frame_text(home: Path, slug: str) -> tuple[str | None, str]:
    """The text the plant published for one role, or a reason it is unreadable."""
    path = home / "dashboards" / (slug + ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no published frame"
    except (OSError, ValueError):
        return None, "unreadable published frame"
    text = record.get("frame") if isinstance(record, dict) else None
    return (text, "") if isinstance(text, str) else (None, "published frame carries no text")


def roles(home: Path, override: list[str]) -> list[str]:
    """The roles the per-slug checks run against.

    A role is a mind the site declares (``minds/<role>``) that the plant
    renders (``top-pains/<role>``) and publishes a frame for
    (``dashboards/<role>.json``).  The three sets together are the site's own
    answer, so a role added or retired changes the scope without a second
    declaration; the scope used is recorded in the state file, so a shrunken
    scope is visible rather than silent.  ``--slug`` overrides it for a manual
    run.
    """
    if override:
        return list(override)
    out = []
    for path in sorted((home / "minds").glob("*")):
        name = path.name
        if not SLUG_RE.match(name) or not path.is_file():
            continue
        if (home / "dashboards" / (name + ".json")).is_file() and (home / "top-pains" / name).is_file():
            out.append(name)
    return out


def applies(requirement: str | None, text: str | None, reason: str) -> str | None:
    """Why a check does not apply to this target, or None when it does."""
    if text is None:
        return reason
    if requirement == "evidence" and EVIDENCE_MARKER not in text:
        return "published frame carries no evidence block"
    if requirement == "pin-verdict" and not any(PIN_VERDICT_RE.match(line)
                                                for line in text.splitlines()):
        return "published frame carries no runtime verdict"
    return None


def plan(home: Path, override: list[str]) -> tuple[list, list[dict], list[str]]:
    """(runs, skips, scope) for this cycle."""
    scope = roles(home, override)
    runs, skips = [], []
    for name, per_slug, accepts_repo, extra, requirement in INVENTORY:
        for slug in (scope if per_slug else [None]):
            ident = f"{name[:-3]}:{slug}" if slug else name[:-3]
            if slug and requirement:
                text, reason = frame_text(home, slug)
                why = applies(requirement, text, reason)
                if why:
                    skips.append({"id": ident, "state": SKIP, "reason": why})
                    continue
            runs.append((ident, name, (slug, accepts_repo, extra)))
    return runs, skips, scope


def run_cycle(home: Path, repo: Path, python: str, timeout: float, workdir: Path,
              override: list[str]) -> dict:
    """Run every applicable check once and return the state record. Never raises."""
    started = time.monotonic()
    runs, skips, scope = plan(home, override)
    entries = []
    cache: dict[str, tuple[bytes | None, str]] = {}
    for ident, name, spec in runs:
        slug, accepts_repo, extra = spec
        if name not in cache:
            cache[name] = head_bytes(repo, f"checks/{name}")
        blob, error = cache[name]
        if blob is None:
            entries.append({"id": ident, "exit": None, "state": UNKNOWN,
                            "verdict": "cannot read HEAD:checks/" + name + ": " + error})
            continue
        script = workdir / name
        script.write_bytes(blob)
        argv = [python, str(script), "--home", str(home)]
        if accepts_repo:
            argv += ["--repo", str(repo)]
        if slug:
            argv += ["--slug", slug]
        argv += list(extra)
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                    env=child_env())
        except subprocess.TimeoutExpired:
            entries.append({"id": ident, "exit": None, "state": UNKNOWN,
                            "verdict": f"timed out after {timeout:.0f}s"})
            continue
        except OSError as exc:
            entries.append({"id": ident, "exit": None, "state": UNKNOWN,
                            "verdict": "cannot run: " + str(exc)})
            continue
        output = result.stdout + result.stderr
        entry = {"id": ident, "exit": result.returncode,
                 "state": state_for(result.returncode),
                 "verdict": verdict_line(output)}
        if entry["state"] != OK and output.strip():
            entry["detail"] = detail_text(output)
        entries.append(entry)
    return {
        "checked_at": now(),
        "cycle_seconds": round(time.monotonic() - started, 2),
        "roles": scope,
        "entries": entries,
        "skipped": skips,
        "not_scheduled": [{"id": name[:-3], "reason": why} for name, why in NOT_SCHEDULED],
        "problems": sorted(e["id"] for e in entries if e["state"] != OK),
    }


def read_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def route(home: Path, added: list[dict], cleared: list[str]) -> None:
    """One addressed message per state change; nothing when nothing changed."""
    cli = home / "bin" / "mishe-tauftauf"
    if added:
        lines = ["[dm] to=health",
                 "Observer checks entered the problem set (scheduled runner).",
                 "The set is compared by check id, so a verdict text that moves "
                 "between samples is not a change; an id here is a check whose "
                 "exit code moved off 0."]
        for entry in added:
            lines.append(f"- {entry['id']} exit={entry['exit']} state={entry['state']}: {entry['verdict']}")
        lines.append("Resolver=health. Re-run `checks-runner/observer-checks.py --home SITE --once` "
                     "and read SITE/checks/observer-checks.json; a difference here means the "
                     "published evidence is not the reviewed tree's, not that the runner failed. "
                     "A check that is wrong about its own reading belongs to its author, not to "
                     "this runner.")
    else:
        lines = ["[dm] to=health",
                 "Observer checks left the problem set (scheduled runner): "
                 + ", ".join(cleared)
                 + ". State: SITE/checks/observer-checks.json."]
    text = "\n".join(lines)
    result = subprocess.run([str(cli), "--home", str(home), "append",
                             "--source", "sensor/observer-checks"],
                            input=text, text=True, capture_output=True, timeout=60,
                            env=child_env())
    if result.returncode:
        raise RuntimeError("routing failed: " + (result.stderr.strip() or "no stderr"))


def cycle(home: Path, repo: Path, python: str, timeout: float, state_path: Path,
          override: list[str]) -> int:
    """One cycle: run, confirm, record, route. Confirmation is two cycles.

    These checks compare a *live* rendering with a fresh one, so a single cycle
    can disagree and agree again on its own — a service restart moves an import
    root, a role's own state line appears and clears.  A detector that routes
    every such sample is a firehose, so an id enters or leaves the problem set
    only after two consecutive cycles say the same thing.  Nothing is hidden:
    the current cycle's failing ids and the pending ones are both in the state
    file, and the interval is the unit's own.
    """
    previous = read_state(state_path) or {}
    with tempfile.TemporaryDirectory(prefix="observer-checks-") as tmp:
        record = run_cycle(home, repo, python, timeout, Path(tmp), override)
    was = set(previous.get("problems") or [])
    last_failing = set(previous.get("candidates") or [])
    failing = set(record["problems"])
    staying = {ident for ident in was if ident in failing or ident in last_failing}
    entering = {ident for ident in failing if ident not in was and ident in last_failing}
    confirmed = sorted(staying | entering)
    added = [e for e in record["entries"] if e["id"] in set(confirmed) - was]
    cleared = sorted(was - set(confirmed))
    record["problems"] = confirmed
    record["candidates"] = sorted(failing)
    record["pending"] = sorted(failing - set(confirmed))
    record["routed"] = {"problems": confirmed, "at": record["checked_at"]} \
        if added or cleared else (previous.get("routed") or {})
    record["repo_head"] = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    if added or cleared:
        route(home, added, cleared)
    # Stdout becomes the unit's signal detail and journal line on a non-zero exit,
    # so name every check that was not OK; an exit 3 with no lines here is unreadable.
    for entry in record["entries"]:
        if entry["state"] != OK:
            print(f"observer-checks: {entry['id']} exit={entry['exit']} "
                  f"state={entry['state']}: {entry['verdict']}")
    return 2 if any(e["state"] == FAIL for e in record["entries"]) else (
        3 if any(e["state"] == UNKNOWN for e in record["entries"]) else 0)


def freshness(state_path: Path, max_age: float) -> int:
    """The monitoring's own failure must be RED: missing is 2, stale is 3."""
    state = read_state(state_path)
    if state is None:
        print("state missing or unreadable: " + str(state_path))
        return 2
    try:
        stamp = datetime.fromisoformat(state["checked_at"])
    except (KeyError, TypeError, ValueError):
        print("state carries no readable checked_at: " + str(state_path))
        return 3
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    if age > max_age:
        print(f"state stale: age={age:.0f}s bound={max_age:.0f}s path={state_path}")
        return 3
    return 0


def notify(text: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        raise RuntimeError("supervisor notify socket missing")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(text.encode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--state", type=Path, default=None,
                        help="state file (default: SITE/checks/observer-checks.json)")
    parser.add_argument("--python", default="/usr/bin/python3",
                        help="interpreter the checks run under")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-check timeout")
    parser.add_argument("--interval", type=float, default=900.0, help="loop interval seconds")
    parser.add_argument("--max-age", type=float, default=1800.0,
                        help="--check bound: state older than this is stale")
    parser.add_argument("--slug", action="append", default=[],
                        help="role the per-slug checks run against (default: every site role)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--check", action="store_true", help="assert the state file is fresh")
    mode.add_argument("--loop", action="store_true", help="run cycles under the watchdog")
    args = parser.parse_args()

    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    state_path = (args.state or (home / "checks" / "observer-checks.json")).resolve()

    if args.check:
        return freshness(state_path, args.max_age)
    if args.loop:
        notify("READY=1")
        while True:
            try:
                cycle(home, repo, args.python, args.timeout, state_path, args.slug)
            except Exception as exc:  # a broken cycle stops the lease: restart + OnFailure
                print("cycle failed: " + str(exc), file=sys.stderr)
                return 1
            if freshness(state_path, args.max_age):
                return 1
            notify("WATCHDOG=1")
            time.sleep(args.interval)
    try:
        return cycle(home, repo, args.python, args.timeout, state_path, args.slug)
    except Exception as exc:
        print("cycle failed: " + str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
