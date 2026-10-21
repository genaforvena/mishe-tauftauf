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

The budget alone is not enough, because the policy allows a *count* per
expression and the tree does not always hold that many sites.  Every free slot
is a hole: a new parsing site that reuses an expression the budget already
allows is accepted by the guard, changes no policy, and grows the debt the
freeze claims to hold.  So the freeze also records what the tree actually
held, per path and per expression, and this check compares the tree against
that record: a site the freeze does not know is new debt, and it fails even
when no policy byte changed.  A tree that holds fewer sites than the freeze
records is a migration and passes.

Exit status: 0 = the debt is not wider than the freeze, 2 = it is (FAIL),
3 = the comparison could not be made (UNKNOWN, never OK).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

POLICY_NAME = "parsing-policy.json"
FREEZE_NAME = "parsing-legacy-freeze.json"
FREEZE_VERSION = 2
SCAN_DIRS = ("src", "coordination", "tools")
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
            raise FileNotFoundError(f"{name} is not in the working tree")
        return path.read_bytes()
    return git_bytes(repo, name)


def load_policy(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "boundaries", "legacy"} or data["version"] != 1:
        raise ValueError("invalid parsing policy")
    return data


def load_freeze(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or "paths" not in data:
        raise ValueError("invalid frozen baseline")
    return data


def head_commit(repo: Path) -> str:
    proc = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _inspector(repo: Path):
    """The guard's own site finder, so both read one fingerprint definition."""
    path = repo / "tools" / "check_parsing.py"
    spec = importlib.util.spec_from_file_location("check_parsing_boundary", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError("tools/check_parsing.py is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def head_sources(repo: Path) -> dict:
    """The committed bytes of every Python file the guard scans."""
    proc = subprocess.run([GIT, "-C", str(repo), "archive", "--format=tar", "HEAD"],
                          capture_output=True)
    if proc.returncode != 0:
        raise FileNotFoundError(proc.stderr.decode("utf-8", "replace").strip() or "the tree has no HEAD")
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            if member.name.split("/", 1)[0] not in SCAN_DIRS:
                continue
            handle = tar.extractfile(member)
            sources[member.name] = handle.read().decode("utf-8") if handle else ""
    return sources


def disk_sources(repo: Path) -> dict:
    """The bytes on disk of every Python file the guard scans."""
    sources = {}
    for directory in SCAN_DIRS:
        base = repo / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            sources[path.relative_to(repo).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def observed_counts(repo: Path, legacy: dict, working_tree: bool) -> dict:
    """How many sites of each expression the tree really holds, per legacy path."""
    inspector = _inspector(repo)
    sources = disk_sources(repo) if working_tree else head_sources(repo)
    counts = {path: {} for path in legacy}
    for relative, source in sources.items():
        if relative not in counts:
            continue
        for site in inspector.inspect_source(relative, source):
            counts[relative][site.fingerprint] = counts[relative].get(site.fingerprint, 0) + 1
    return counts


def compare_observed(current: dict, frozen: dict) -> tuple:
    """(findings, allowances) for the tree's real sites against the freeze."""
    findings, allowances = [], []
    for path in sorted(current):
        record = frozen.get(path)
        if record is None:
            continue  # the budget comparison already names an unknown path
        allowed = record.get("observed")
        if allowed is None:
            raise ValueError(f"the freeze records no observed sites for {path}")
        for fingerprint, count in sorted(current[path].items()):
            was = allowed.get(fingerprint, 0)
            if count > was:
                findings.append(f"{path}: the tree holds {count} site(s) of expression "
                                f"{fingerprint[:12]} where the freeze recorded {was}; the legacy "
                                f"debt grew without a policy edit")
        for fingerprint, count in sorted(allowed.items()):
            now = current[path].get(fingerprint, 0)
            if count > now:
                allowances.append(f"{path}: the tree dropped {count - now} site(s) of expression "
                                  f"{fingerprint[:12]} the freeze records")
    return findings, allowances


def free_slots(budget: dict, observed: dict) -> list:
    """Slots the policy allows that the tree does not hold."""
    slots = []
    for path, counts in budget.items():
        held = observed.get(path, {})
        free = sum(max(0, allowed - held.get(fingerprint, 0))
                   for fingerprint, allowed in counts.items())
        if free > 0:
            slots.append(f"{path}: the policy allows {free} more site(s) than the tree holds; "
                         f"the freeze closes those slots")
    return slots


def baseline_payload(policy: dict, freeze: dict, observed: dict) -> dict:
    current = path_stats(policy["legacy"])
    paths = {}
    for path, stats in current.items():
        entry = dict(stats)
        entry["observed"] = dict(sorted(observed.get(path, {}).items()))
        entry["observed_sites"] = sum(entry["observed"].values())
        paths[path] = entry
    payload = {
        "version": FREEZE_VERSION,
        "frozen_at": freeze.get("frozen_at") or datetime.now(timezone.utc).isoformat(),
        "frozen_from_commit": freeze.get("frozen_from_commit") or "",
        "paths": paths,
    }
    if freeze.get("updated_at"):
        payload["updated_at"] = freeze["updated_at"]
    if freeze.get("updated_from_commit"):
        payload["updated_from_commit"] = freeze["updated_from_commit"]
    payload["note"] = ("The frozen legacy parsing debt: the sites tools/check_parsing.py still "
                       "allows. It may shrink when a consumer migrates behind an adapter; it may "
                       "not grow. `observed` is what the tree really held when the freeze was "
                       "written; a site the freeze does not record is new debt.")
    return payload


def write_baseline(payload: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def create_baseline(policy: dict, freeze: dict, observed: dict, out: Path, repo: Path) -> int:
    """Record the freeze the plant starts from.  This is a deliberate act."""
    payload = baseline_payload(policy, freeze, observed)
    payload["frozen_at"] = datetime.now(timezone.utc).isoformat()
    payload["frozen_from_commit"] = head_commit(repo)
    write_baseline(payload, out)
    print(f"wrote {out}: {len(payload['paths'])} path(s), "
          f"{sum(entry['observed_sites'] for entry in payload['paths'].values())} observed site(s)")
    return 0


def update_baseline(policy: dict, freeze: dict, observed: dict, out: Path, repo: Path) -> int:
    """Rewrite the freeze to the policy's current budget.  It may only shrink."""
    current = path_stats(policy["legacy"])
    previous = freeze.get("paths", {})
    findings = []
    for path, stats in current.items():
        if path not in previous:
            findings.append(f"{path}: the frozen budget does not know this path; "
                            f"it now carries {stats['sites']} budgeted site(s)")
            continue
        allowed = previous[path]
        if stats["sites"] > allowed["sites"]:
            findings.append(f"{path}: budgeted sites grew from {allowed['sites']} to {stats['sites']}")
        elif stats["sites"] == allowed["sites"] and stats["digest"] != allowed["digest"]:
            findings.append(f"{path}: {stats['sites']} budgeted site(s) hide a different set of "
                            f"expressions than the freeze records")
    for path, record in previous.items():
        was = record.get("observed")
        if was is None:
            continue
        now = observed.get(path, {})
        for fingerprint, count in sorted(now.items()):
            if count > was.get(fingerprint, 0):
                findings.append(f"{path}: the tree holds {count} site(s) of expression "
                                f"{fingerprint[:12]} where the freeze recorded "
                                f"{was.get(fingerprint, 0)}; the legacy debt grew without a "
                                f"policy edit")
    if findings:
        for line in findings:
            print(line)
        print("REFUSED — the frozen legacy parsing debt cannot grow on demand; migrate the site "
              "behind a registered adapter, or remove it")
        return 2
    payload = baseline_payload(policy, freeze, observed)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["updated_from_commit"] = head_commit(repo)
    removed = [path for path in previous if path not in current]
    write_baseline(payload, out)
    print(f"updated {out}: {len(current)} path(s), "
          f"{sum(entry['observed_sites'] for entry in payload['paths'].values())} observed site(s), "
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
                        help="rewrite the freeze to the policy's current budget and the tree's "
                             "current sites; may only shrink it")
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
    try:
        observed = observed_counts(repo, policy["legacy"], args.working_tree)
    except (OSError, ValueError, subprocess.SubprocessError, SyntaxError) as exc:
        print(f"UNKNOWN — the tree's parsing sites cannot be counted: {exc}", file=sys.stderr)
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
        try:
            if created:
                return create_baseline(policy, freeze, observed, out, repo)
            return update_baseline(policy, freeze, observed, out, repo)
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
    if freeze.get("version") != FREEZE_VERSION:
        print(f"UNKNOWN — the frozen baseline records version {freeze.get('version')!r}, not "
              f"{FREEZE_VERSION}; regenerate it with --update-baseline", file=sys.stderr)
        return 3
    current = path_stats(policy["legacy"])
    findings, allowances = compare(current, freeze["paths"])
    try:
        observed_findings, observed_allowances = compare_observed(observed, freeze["paths"])
    except ValueError as exc:
        print(f"UNKNOWN — the frozen legacy budget cannot be compared: {exc}", file=sys.stderr)
        return 3
    findings.extend(observed_findings)
    allowances.extend(observed_allowances)
    allowances.extend(free_slots(policy["legacy"], observed))
    for line in findings:
        print(line)
    for line in allowances:
        print(f"standing allowance — {line}")
    if findings:
        print(f"FAIL the frozen legacy parsing budget was widened: {len(findings)} path(s) carry "
              f"debt the freeze does not allow; AGENTS.md forbids widening it to pass a change")
        return 2
    total = sum(entry["sites"] for entry in current.values())
    held = sum(sum(counts.values()) for counts in observed.values())
    print(f"PASS — the legacy parsing budget is not wider than the freeze ({len(current)} paths, "
          f"{total} budgeted sites, {held} observed site(s), compared against {where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
