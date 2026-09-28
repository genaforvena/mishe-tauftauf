#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_MAX_SECONDS = 300.0


def configured_timeout() -> float | None:
    value = os.environ.get("MISHE_TAUFTAUF_MAX_SECONDS")
    if value is None:
        return DEFAULT_MAX_SECONDS
    try:
        timeout = float(value)
    except ValueError:
        timeout = 0
    if not math.isfinite(timeout) or timeout <= 0:
        print("MISHE_TAUFTAUF_MAX_SECONDS must be a finite positive number", file=sys.stderr)
        return None
    return timeout


def stop_process_group(process: subprocess.Popen) -> bool:
    """Stop OMP and children that inherited its process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        print(f"could not terminate OMP process group: {exc}", file=sys.stderr)
        return False
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    # The direct child can exit on TERM while its descendants keep running.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        print(f"could not kill OMP process group: {exc}", file=sys.stderr)
        return False
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        print("OMP process did not exit after process-group kill", file=sys.stderr)
        return False
    return True


def main() -> int:
    omp = shutil.which("omp")
    if omp is None:
        print("omp executable unavailable", file=sys.stderr)
        return 127
    workspace = os.environ.get("MISHE_TAUFTAUF_WORKSPACE")
    if not workspace:
        print("MISHE_TAUFTAUF_WORKSPACE is required", file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    fd, name = tempfile.mkstemp(prefix="mishe-tauftauf-omp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(prompt)
        model = os.environ.get("MISHE_TAUFTAUF_MODEL")
        command = [omp, "--print", "--no-session", "--no-extensions", "--cwd", workspace]
        if model:
            command.extend(["--model", model])
        timeout = configured_timeout()
        if timeout is None:
            return 2
        process = subprocess.Popen([*command, "@" + name], start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"OMP child exceeded {timeout:g} seconds", file=sys.stderr)
            return 124 if stop_process_group(process) else 125
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
