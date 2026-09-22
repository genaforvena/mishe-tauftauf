from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .observations import FOOTER_LEASE_RE, discover, strip_owned_chrome

OWNED_OPTION = "@mishe-tauftauf-home"
_LAST_REPAIR: dict[tuple[str, str], float] = {}


class TmuxError(RuntimeError):
    pass


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    if shutil.which("tmux") is None:
        raise TmuxError("tmux executable is unavailable")
    result = subprocess.run(["tmux", *args], capture_output=True)
    if check and result.returncode:
        raise TmuxError(result.stderr.decode("utf-8", "replace").strip() or f"tmux {' '.join(args)} failed")
    return result


def owns_session(home: Path, session: str) -> bool:
    result = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False)
    return result.returncode == 0 and result.stdout.decode().strip() == str(home.resolve())


def start(home: Path, session: str = "mishe-tauftauf", interval: float = 5.0) -> None:
    if _tmux("has-session", "-t", session, check=False).returncode != 0:
        _tmux("new-session", "-d", "-s", session, "-n", "bootstrap")
        _tmux("set-option", "-t", session, OWNED_OPTION, str(home.resolve()))
    elif not owns_session(home, session):
        raise TmuxError(f"session {session!r} exists but is not owned by {home}")
    command = [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "pain", "watch"]
    slugs = discover(home)
    if "observability" not in slugs:
        _install_observability(home, session)
        slugs = discover(home)
    for slug in slugs:
        target = f"{session}:{slug}"
        if _tmux("list-windows", "-t", session, "-F", "#{window_name}", check=False).stdout.decode().splitlines().count(slug) == 0:
            _tmux("new-window", "-d", "-t", session, "-n", slug, "sh")
            _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
            _tmux("split-window", "-v", "-t", target, "sh", "-c", "printf 'Mind surface ready\\n'; exec sh")
            _tmux("respawn-pane", "-k", "-t", f"{target}.0", *command, slug, "--interval", str(interval))
        panes = _tmux("list-panes", "-t", target, "-F", "#{pane_index}").stdout.decode().splitlines()
        if "1" not in panes:
            _tmux("split-window", "-v", "-t", target, "sh", "-c", "printf 'Mind surface ready\\n'; exec sh")
        _tmux("set-option", "-p", "-t", f"{target}.1", "remain-on-exit", "on")
        _tmux("select-layout", "-t", target, "even-vertical")
    if "bootstrap" in _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines() and slugs:
        _tmux("kill-window", "-t", f"{session}:bootstrap")


def _install_observability(home: Path, session: str) -> None:
    path = home / "top-pains" / "observability"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    quoted_session = shlex.quote(session)
    quoted_feed = shlex.quote(str(home / "feed"))
    script = f'''#!/bin/sh
printf '%s\\n' 'DESIRED STATE: every Top Pain lease advances and failures remain visible' 'UNRESOLVED: pane liveness is checked independently'
if command -v tmux >/dev/null 2>&1 && tmux has-session -t {quoted_session} 2>/dev/null; then
  for window in $(tmux list-windows -t {quoted_session} -F '#{{window_name}}' 2>/dev/null); do
    lease=$(tmux capture-pane -p -t {quoted_session}:\"$window\".0 -S -50 2>/dev/null | sed -n '/^-- pane live /p' | tail -n 1)
    if [ -n \"$lease\" ]; then printf 'window %s · %s\\n' \"$window\" \"$lease\"; else printf 'UNKNOWN — window %s has no visible lease\\n' \"$window\"; fi
  done
else
  printf '%s\\n' 'UNKNOWN — tmux session unavailable'
fi
printf '%s\\n' 'OUTSTANDING OBSERVATION FAILURES'
if [ -r {quoted_feed} ]; then
  sed -n '/^    | .*\\(UNKNOWN\\|HOLD pane-\\)/p' {quoted_feed} | tail -n 20
else
  printf '%s\\n' 'UNKNOWN — feed unavailable'
fi
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def stop(home: Path, session: str = "mishe-tauftauf") -> None:
    if _tmux("has-session", "-t", session, check=False).returncode != 0:
        return
    if not owns_session(home, session):
        raise TmuxError(f"refusing to remove unowned session {session!r}")
    _tmux("kill-session", "-t", session)


def capture_raw(session: str, slug: str) -> str:
    result = _tmux("capture-pane", "-p", "-t", f"{session}:{slug}.0", "-S", "-", check=False)
    if result.returncode:
        return f"UNKNOWN — top-pain {slug} pane missing\n"
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"UNKNOWN — top-pain {slug} pane invalid UTF-8: {exc}\n"
    if not text.strip():
        return f"UNKNOWN — top-pain {slug} pane empty\n"
    lines = text.splitlines()
    leases = [index for index, line in enumerate(lines) if FOOTER_LEASE_RE.fullmatch(line)]
    if leases:
        end = leases[-1] + 1
        start = leases[-2] + 1 if len(leases) > 1 else 0
        lines = lines[start:end]
        text = "\n".join(lines) + "\n"
    return text


def capture_top(home: Path, session: str, slug: str) -> str:
    return strip_owned_chrome(capture_raw(session, slug))


def lease_value(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if FOOTER_LEASE_RE.fullmatch(line):
            return line
    return None


def _pane_stopped_or_dead(session: str, slug: str) -> bool:
    result = _tmux(
        "display-message", "-p", "-t", f"{session}:{slug}.0",
        "#{pane_dead} #{pane_pid}", check=False,
    )
    if result.returncode:
        return True
    fields = result.stdout.decode("ascii", "replace").split()
    if not fields or fields[0] == "1":
        return True
    if len(fields) > 1:
        try:
            status = Path(f"/proc/{int(fields[1])}/status").read_text(encoding="ascii")
        except (OSError, ValueError):
            return False
        for line in status.splitlines():
            if line.startswith("State:") and "\tT " in line:
                return True
    return False


def check_pane(home: Path, session: str, slug: str, wait: float = 11.0) -> tuple[bool, str]:
    if _pane_stopped_or_dead(session, slug):
        return False, f"HOLD pane-frozen: {slug} renderer process is stopped or dead"
    first = capture_raw(session, slug)
    if "pane missing" in first:
        return False, f"HOLD pane-missing: {slug}"
    if "pane empty" in first:
        return False, f"HOLD pane-empty: {slug}"
    if first.startswith("UNKNOWN — top-pain"):
        return False, f"HOLD pane-fallback: {slug}"
    lease1 = lease_value(first)
    if lease1 is None:
        return False, f"HOLD pane-fallback: {slug} has no owned lease"
    time.sleep(wait)
    second = capture_raw(session, slug)
    lease2 = lease_value(second)
    if lease2 is None or lease2 == lease1:
        return False, f"HOLD pane-frozen: {slug} lease did not advance"
    return True, f"PASS pane-live: {slug}"
def repair_top(home: Path, session: str, slug: str) -> tuple[bool, str]:
    if not owns_session(home, session):
        return False, f"UNKNOWN pane command ownership for {session}:{slug}.0"
    target = f"{session}:{slug}.0"
    command_result = _tmux(
        "display-message", "-p", "-t", target, "#{pane_start_command}", check=False,
    )
    start_command = command_result.stdout.decode("utf-8", "replace").strip()
    if command_result.returncode or "mishe_tauftauf" not in start_command.replace("-", "_") or "pain watch" not in start_command:
        return False, f"UNKNOWN pane command for {target}: {start_command or 'unreadable'}"
    key = (session, slug)
    current = time.monotonic()
    previous = _LAST_REPAIR.get(key)
    if previous is not None and current - previous < 60.0:
        return False, f"HOLD pane-frozen recurrence within {current - previous:.1f}s for {slug}"
    _LAST_REPAIR[key] = current
    command = [
        sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
        "pain", "watch", slug, "--interval", "5",
    ]
    _tmux("respawn-pane", "-k", "-t", target, *command)
    return True, f"pane {slug} respawned once"




def launch_mind(home: Path, session: str, slug: str, sequence: int, attempt: int, invocation: str, context: str) -> None:
    context_path = home / "minds" / f".{invocation}.context"
    context_path.write_text(context, encoding="utf-8")
    target = f"{session}:{slug}.1"
    command = [
        sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "tmux-mind-run", slug,
        str(sequence), str(attempt), invocation, str(context_path), "--session", session,
    ]
    _tmux("respawn-pane", "-k", "-t", target, *command)
