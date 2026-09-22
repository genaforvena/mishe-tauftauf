#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    omp = shutil.which("omp")
    if omp is None:
        print("omp executable unavailable", file=sys.stderr)
        return 127
    version = subprocess.run([omp, "--version"], capture_output=True, text=True)
    if version.returncode or "18.2.8" not in version.stdout + version.stderr:
        print("omp adapter requires deployed omp 18.2.8", file=sys.stderr)
        return 2
    workspace = os.environ.get("MISHE_TAUFTAUF_WORKSPACE")
    if not workspace:
        print("MISHE_TAUFTAUF_WORKSPACE is required", file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    fd, name = tempfile.mkstemp(prefix="mishe-tauftauf-omp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(prompt)
        result = subprocess.run([omp, "--print", "--no-session", "--no-extensions", "--cwd", workspace, "@" + name])
        return result.returncode
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
