"""Acquire subscription quota through a supported native local command only."""
import fcntl
import os
from pathlib import Path
import subprocess
import time
import uuid

from .claude_quota_text import append_history, read_usage
from .state_files import read_json, write_json


def collect(home, *, now=None, runner=subprocess.run):
    root = Path(home) / "operator/budget"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".native-quota.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        at = time.time() if now is None else now
        evidence_path = root / "native-evidence" / (str(time.time_ns()) + "-" + uuid.uuid4().hex + ".json")
        path = root / "claude-native-quota.json"
        evidence = dict(at=at, command="claude native /usage", source="claude-native-usage")
        try:
            evidence["previous"] = read_json(path)
        except (OSError, ValueError) as exc:
            evidence["previous_error"] = str(exc)
            # Preserve malformed bytes as well as their diagnosis.
            evidence["previous_raw"] = path.read_text(errors="replace")
        try:
            command = ["claude", "-p", "/usage", "--model", "claude-haiku-5-5", "--tools", "",
                       "--max-turns", "1", "--output-format", "json", "--no-session-persistence",
                       "--setting-sources", "project,local", "--settings", '{"disableAllHooks":true}']
            env = dict(os.environ, TZ="UTC", LC_ALL="C.UTF-8", DISABLE_AUTOUPDATER="1")
            completed = runner(command, env=env, capture_output=True, text=True, timeout=45,
                               cwd=Path(home).parent)
            evidence.update(code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
            if completed.returncode:
                raise ValueError("native usage command failed")
            result = read_usage(completed.stdout, now=at)
            if result["state"] == "FRESH":
                # An UNKNOWN sample (window just reset) has no five_hour value to log.
                append_history(root / "claude-rate-history.jsonl", result)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                evidence["stdout"] = str(exc.output or "")
                evidence["stderr"] = str(exc.stderr or "")
            result = dict(state="UNKNOWN", source="claude-native-usage", observed_at=at,
                          error=str(exc))
        result["evidence"] = str(evidence_path.relative_to(Path(home)))
        evidence["result"] = result
        # Preserve original evidence before replacing current observations. A
        # failure publishes no windows: the existing budget view fails closed.
        write_json(evidence_path, evidence)
        write_json(path, result)
        return result
