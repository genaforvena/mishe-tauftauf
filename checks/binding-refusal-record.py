#!/usr/bin/env python3
"""Detector: binding refusal writes a record to the caller's bound site.

Verifies that:
1. A command with a mismatched --home exits 2 (binding guard).
2. The refusal produces a record in the caller's bound site logs/binding-refusal-audit.jsonl.
3. No record is written to the attempted foreign home.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_cli(home: Path, *args: str, seed_home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if seed_home is not None:
        env["MISHE_SEED_HOME"] = str(seed_home)
    else:
        env.pop("MISHE_SEED_HOME", None)
    env["PYTHONPATH"] = str(Path(__file__).parent.parent / "src")
    return subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), *args],
        text=True, capture_output=True, timeout=30, env=env,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        site = repo / ".mishe-tauftauf"

        # Initialize the bound site.
        result = run_cli(site, "seed", "init", "--slug", "genome")
        if result.returncode != 0:
            print(f"FAIL: seed init failed: {result.stderr}")
            return 1

        # 1. Command with mismatched --home exits 2.
        foreign = repo / ".mishe-other"
        foreign.mkdir()
        refused = run_cli(foreign, "wall", "write", "--owner", "genome", "--file", "/dev/null",
                          seed_home=site)
        if refused.returncode != 2:
            print(f"FAIL: binding mismatch should exit 2, got {refused.returncode}")
            return 1

        # 2. Refusal produced a record in the caller's bound site.
        audit_path = site / "logs" / "binding-refusal-audit.jsonl"
        if not audit_path.exists():
            print(f"FAIL: no binding-refusal record at {audit_path}")
            return 1
        entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        if not entries:
            print("FAIL: binding-refusal record is empty")
            return 1
        entry = entries[-1]
        if entry.get("attempted_home") != str(foreign.resolve()):
            print(f"FAIL: attempted_home mismatch: {entry.get('attempted_home')}")
            return 1
        if entry.get("bound_home") != str(site.resolve()):
            print(f"FAIL: bound_home mismatch: {entry.get('bound_home')}")
            return 1
        if "wall" not in entry.get("command", ""):
            print(f"FAIL: command mismatch: {entry.get('command')}")
            return 1

        # 3. No record in the attempted foreign home.
        foreign_audit = foreign / "logs" / "binding-refusal-audit.jsonl"
        if foreign_audit.exists():
            print("FAIL: binding-refusal record written to attempted foreign home")
            return 1

        print("PASS: binding-refusal record detector")
        return 0


if __name__ == "__main__":
    sys.exit(main())
