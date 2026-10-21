#!/usr/bin/env python3
"""Fail a tree that widens the frozen legacy parsing budget.

`tools/check_parsing.py` allows a file's parsing sites when the policy's
`legacy` section carries a matching expression fingerprint.  That budget is
meant to be *frozen* debt: AGENTS.md says the frozen legacy budget "must not
be widened to pass a change", and `docs/protocol-boundaries.md` says "Do not
regenerate the legacy budget to make a failing change pass".  Nothing
enforced either sentence.  A budget entry added beside a failing change makes
the guard pass again, silently, and the only trace is a diff a reader has to
notice.

This check compares the policy's legacy budget against a frozen baseline
(`parsing-legacy-freeze.json`, committed beside the policy) and fails when the
budget has grown:

  * a path the freeze does not know (a new debt file),
  * more budgeted sites in a known path (more debt),
  * the same number of sites hiding a different set of expressions (the
    budget was rewritten rather than grown -- the "total count stays constant"
    case the plant's own rule calls out).

A budget smaller than the freeze is allowed: that is a migration, which the
plant wants.  The standing allowance is reported, so it can be closed.

Exit status: 0 = the budget is not wider than the freeze, 2 = it is (FAIL),
3 = the comparison could not be made (UNKNOWN, never OK).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

POLICY_NAME = "parsing-policy.json"
FREEZE_NAME = "parsing-legacy-freeze.json"
GIT = "/usr/bin/git"


def canonical_digest(counts: dict) -> str:
    """A digest over one path's budgeted expressions and their counts."""
    body = "\n".join(f"{fingerprint}:{counts[fingerprint]}" for fingerprint in sorted(counts))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def path_stats(legacy: dict) -> dict:
    stats = {}
    for path, counts in legacy.items():
        stats[path] = {
            "sites": sum(counts.values()),
            "entries": len(counts),
            "digest": canonical_digest(counts),
        }
    return stats


def compare(current: dict, freeze: dict) -> tuple:
    """(findings, allowances) for the policy's budget against the freeze."""
    findings, allowances = [], []
    for path in sorted(current):
        if path not in freeze:
            findings.append(f"{path}: the frozen budget does not know this path; "
                            f"it now carries {current[path]['sites']} budgeted site(s)")
            continue
        got, allowed = current[path], freeze[path]
        if got["sites"] > allowed["sites"]:
            findings.append(f"{path}: budgeted sites grew from {allowed['sites']} to {got['sites']}")
        elif got["sites"] == allowed["sites"] and got["digest"] != allowed["digest"]:
            findings.append(f"{path}: {got['sites']} budgeted site(s) hide a different set of "
                            f"expressions than the freeze records")
        elif got["sites"] < allowed["sites"]:
            allowances.append(f"{path}: the freeze still allows {allowed['sites'] - got['sites']} "
                              f"site(s) this policy no longer records")
    for path in sorted(freeze):
        if path not in current:
            allowances.append(f"{path}: the freeze allows {freeze[path]['sites']} site(s) this "
                              f"policy no longer records")
    return findings, allowances


def git_bytes(repo: Path, name: str) -> bytes:
    proc = subprocess.run([GIT, "-C", str(repo), "show", f"HEAD:{name}"], capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"{name} is not in HEAD"
        raise FileNotFoundError(detail)
    return proc.stdout


def read_bytes(repo: Path, name: str, working_tree: bool) -> bytes:
    if working_tree:
        path = repo / name
        if not path.is_file():
            raise FileNotFoundError(f"{name} is missing from {repo}")
        return path.read_bytes()
    return git_bytes(repo, name)


def load_policy(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("legacy"), dict):
        raise ValueError("the policy carries no legacy budget")
    for path, counts in data["legacy"].items():
        if not isinstance(path, str) or not isinstance(counts, dict):
            raise ValueError("the policy's legacy budget is malformed")
        for fingerprint, count in counts.items():
            if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                    or not isinstance(count, int) or isinstance(count, bool) or count <= 0):
                raise ValueError("the policy's legacy budget has an invalid entry")
    return data


def load_freeze(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
        raise ValueError("the frozen baseline records no paths")
    for path, entry in data["paths"].items():
        if not isinstance(path, str) or not isinstance(entry, dict):
            raise ValueError("the frozen baseline is malformed")
        for key in ("sites", "entries", "digest"):
            if key not in entry:
                raise ValueError(f"the frozen baseline is missing {key}")
        if not isinstance(entry["sites"], int) or isinstance(entry["sites"], bool) or entry["sites"] < 0:
            raise ValueError("the frozen baseline has an invalid site count")
        if not isinstance(entry["digest"], str) or len(entry["digest"]) != 64:
            raise ValueError("the frozen baseline has an invalid digest")
    return data


def head_commit(repo: Path) -> str:
    proc = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def baseline_payload(policy: dict, freeze: dict) -> dict:
    current = path_stats(policy["legacy"])
    return {
        "version": 1,
        "frozen_at": freeze.get("frozen_at"),
        "frozen_from_commit": freeze.get("frozen_from_commit"),
        "updated_at": freeze.get("updated_at"),
        "updated_from_commit": freeze.get("updated_from_commit"),
        "note": ("The frozen legacy parsing budget: the debt tools/check_parsing.py still allows. "
                 "It may shrink when a consumer migrates behind an adapter; it may not grow."),
        "paths": {path: current[path] for path in sorted(current)},
    }


def write_baseline(payload: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def create_baseline(policy: dict, freeze: dict, out: Path) -> int:
    """Record the freeze the plant starts from.  This is a deliberate act."""
    payload = baseline_payload(policy, freeze)
    write_baseline(payload, out)
    total = sum(entry["sites"] for entry in payload["paths"].values())
    print(f"created {out}: {len(payload['paths'])} path(s), {total} budgeted site(s)")
    return 0


def update_baseline(policy: dict, freeze: dict, out: Path) -> int:
    """Rewrite the freeze to the policy's current budget.  It may only shrink.

    A widening is refused: the freeze is the record of what the plant owes, and
    a record that can grow on demand records nothing.  Removing debt shrinks
    the freeze, so the allowance to re-add it closes with the migration.
    """
    current = path_stats(policy["legacy"])
    allowed = freeze["paths"]
    for path in sorted(current):
        if path not in allowed:
            print(f"REFUSED — {path} is not in the frozen baseline; a baseline update may not "
                  f"widen the budget")
            return 2
        if current[path]["sites"] > allowed[path]["sites"]:
            print(f"REFUSED — {path} carries {current[path]['sites']} budgeted site(s) and the "
                  f"freeze allows {allowed[path]['sites']}; a baseline update may not widen the budget")
            return 2
    payload = baseline_payload(policy, freeze)
    write_baseline(payload, out)
    removed = sorted(set(allowed) - set(current))
    print(f"updated {out}: {len(current)} path(s), "
          f"{sum(entry['sites'] for entry in current.values())} budgeted site(s); "
          f"{len(removed)} path(s) dropped")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=None,
                        help="site home; accepted for the observer-check runner contract")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root that holds the policy and the freeze")
    parser.add_argument("--working-tree", action="store_true",
                        help="read the files on disk instead of the committed HEAD bytes")
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the freeze to the policy's current budget; may only shrink it")
    parser.add_argument("--baseline-out", type=Path, default=None,
                        help="where --update-baseline writes the freeze (default: REPO/parsing-legacy-freeze.json)")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    where = "the working tree" if args.working_tree else "the committed HEAD bytes"
    try:
        policy_raw = read_bytes(repo, POLICY_NAME, args.working_tree)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"UNKNOWN — the frozen legacy budget cannot be compared: {exc}", file=sys.stderr)
        return 3
    try:
        policy = load_policy(policy_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"UNKNOWN — the frozen legacy budget cannot be compared: {exc}", file=sys.stderr)
        return 3
    if args.update_baseline:
        out = args.baseline_out or (repo / FREEZE_NAME)
        try:
            freeze = load_freeze(read_bytes(repo, FREEZE_NAME, args.working_tree))
            created = False
        except FileNotFoundError:
            freeze = {"frozen_at": datetime.now(timezone.utc).isoformat(),
                      "frozen_from_commit": head_commit(repo), "paths": {}}
            created = True
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeDecodeError) as exc:
            print(f"UNKNOWN — the frozen baseline cannot be read: {exc}", file=sys.stderr)
            return 3
        freeze = dict(freeze)
        freeze["updated_at"] = datetime.now(timezone.utc).isoformat()
        freeze["updated_from_commit"] = head_commit(repo)
        try:
            if created:
                return create_baseline(policy, freeze, out)
            return update_baseline(policy, freeze, out)
        except OSError as exc:
            print(f"UNKNOWN — the frozen baseline cannot be written: {exc}", file=sys.stderr)
            return 3
    try:
        freeze = load_freeze(read_bytes(repo, FREEZE_NAME, args.working_tree))
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"UNKNOWN — the frozen legacy budget cannot be compared: {exc}", file=sys.stderr)
        return 3
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"UNKNOWN — the frozen legacy budget cannot be compared: {exc}", file=sys.stderr)
        return 3
    current = path_stats(policy["legacy"])
    findings, allowances = compare(current, freeze["paths"])
    for line in findings:
        print(line)
    for line in allowances:
        print(f"standing allowance — {line}")
    if findings:
        print(f"FAIL the frozen legacy parsing budget was widened: {len(findings)} path(s) carry "
              f"debt the freeze does not allow; AGENTS.md forbids widening it to pass a change")
        return 2
    total = sum(entry["sites"] for entry in current.values())
    print(f"PASS — the legacy parsing budget is not wider than the freeze ({len(current)} paths, "
          f"{total} budgeted sites, compared against {where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
