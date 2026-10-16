#!/usr/bin/env python3
"""Detector: cross-plant DM channel accepts known linked site sources.

Verifies that:
1. A cross-plant DM with a known linked site source is accepted.
2. A cross-plant DM with an unknown site source is refused.
3. A wall write against a foreign home still exits 2 (binding guard).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
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

        # Initialize the site.
        result = run_cli(site, "seed", "init", "--slug", "genome")
        if result.returncode != 0:
            print(f"FAIL: seed init failed: {result.stderr}")
            return 1

        # 1. Cross-plant DM with unknown site source is refused.
        refused = run_cli(site, "wall", "dm", "--source", "unknown-site/health",
                          "--to", "genome", "--text", "test")
        if refused.returncode == 0:
            print("FAIL: cross-plant DM with unknown site was accepted")
            return 1
        if "not a known linked site" not in refused.stderr:
            print(f"FAIL: expected 'not a known linked site' in stderr, got: {refused.stderr}")
            return 1

        # 2. Add linked-sites.json and verify acceptance.
        health_dir = site / "health"
        health_dir.mkdir(exist_ok=True)
        (health_dir / "linked-sites.json").write_text(
            json.dumps({"version": 1, "sites": [
                {"session": "android-body", "home": "/home/mesh-home/android-body/.mishe-tauftauf"}
            ]}),
            encoding="utf-8",
        )
        accepted = run_cli(site, "wall", "dm", "--source", "android-body/health",
                           "--to", "genome", "--text", "cross-plant test")
        if accepted.returncode != 0:
            print(f"FAIL: cross-plant DM with linked site was refused: {accepted.stderr}")
            return 1

        # 3. Wall write against a foreign home still exits 2.
        foreign = repo / ".mishe-other"
        run_cli(foreign, "seed", "init", "--slug", "witness")
        env = os.environ.copy()
        env["MISHE_SEED_HOME"] = str(site)
        env["PYTHONPATH"] = str(Path(__file__).parent.parent / "src")
        write_result = subprocess.run(
            [sys.executable, "-m", "mishe_tauftauf", "--home", str(foreign),
             "wall", "write", "--owner", "genome", "--file", "/dev/null"],
            text=True, capture_output=True, timeout=30, env=env,
        )
        if write_result.returncode != 2:
            print(f"FAIL: wall write against foreign home should exit 2, got {write_result.returncode}")
            return 1

        print("PASS: cross-plant DM channel detector")
        return 0


if __name__ == "__main__":
    sys.exit(main())
