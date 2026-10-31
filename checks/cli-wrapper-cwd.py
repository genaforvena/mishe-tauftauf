#!/usr/bin/env python3
"""Detector: the site CLI wrappers work from any working directory.

Every systemd alert router, timer and mind shell calls SITE/bin/mishe or
SITE/bin/mishe-tauftauf without a WorkingDirectory. A wrapper whose
PYTHONPATH is relative to the repo root imports fine from the repo and fails
`ModuleNotFoundError` everywhere else, so the routers that tell Health a monitor
failed fail too (2026-10-31: model-changes-alert, and by the same construction
the other *-alert units). A failing router has nobody to tell, so only a check
outside the router can see it.

This check runs each wrapper from a scratch directory, the way a unit does.

Exit status:
  0  every wrapper present runs `--help` from a neutral directory
  2  a wrapper fails there (named in the verdict line, with the last stderr line)
  3  undecidable: no wrapper exists, or one cannot be executed
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

NAMES = ("mishe", "mishe-tauftauf")


def evaluate(home: Path) -> tuple[int, list[str]]:
    wrappers = [home / "bin" / name for name in NAMES]
    missing = [w.name for w in wrappers if not w.is_file()]
    if missing:
        return 3, ["wrapper missing: " + ", ".join(missing)]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    problems = []
    with tempfile.TemporaryDirectory() as neutral:
        for wrapper in wrappers:
            try:
                run = subprocess.run([str(wrapper), "--home", str(home), "append", "--help"], cwd=neutral,
                                     capture_output=True, text=True, timeout=30, env=env)
            except (OSError, subprocess.SubprocessError) as exc:
                return 3, [f"{wrapper.name} cannot be executed: {exc}"]
            if run.returncode:
                tail = (run.stderr.strip().splitlines() or ["no stderr"])[-1]
                problems.append(f"{wrapper.name} fails outside the repo root: {tail}")
    return (2, problems) if problems else (0, ["both wrappers run from a neutral directory"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo")
    args = parser.parse_args()
    code, lines = evaluate(Path(args.home))
    print(("OK" if code == 0 else "FAIL" if code == 2 else "UNKNOWN") + ": " + "; ".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
