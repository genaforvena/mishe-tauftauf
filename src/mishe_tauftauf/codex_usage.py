"""Codex account windows read from the codex CLI's own rollout files (read-only).

The codex CLI writes ``rate_limits`` into each ``token_count`` event of its session
rollouts under ``~/.codex/sessions``. Those readings are the only account evidence used:
no credential is read, copied or refreshed here. A window is FRESH only when the newest
reading is recent and its reset is still ahead (the producer's one freshness rule), so a
codex account that has not run lately is STALE and the router skips it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import budget_producer, codex_text
from .state_files import read_json, write_json

CODEX_ALIAS = "codex-a"
"""The registry alias of the one ChatGPT login the codex CLI holds."""
SESSIONS = Path.home() / ".codex" / "sessions"
RECENT_FILES = 40          # newest rollouts scanned for a rate_limits reading
TAIL_BYTES = 2 * 1024 * 1024  # bytes read from the end of each rollout (readings are near the end)
LIMIT_ID = "codex"


def _tail_lines(path: Path) -> list[str]:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        data = fh.read()
    return codex_text.tail_lines(data, cut=size > TAIL_BYTES)  # the first line may be cut mid-record


def latest_reading(sessions: Path = SESSIONS, *, limit: int = RECENT_FILES) -> dict | None:
    """The newest ``rate_limits`` reading of the codex limit, as ``{"at", "rate_limits"}``, or None."""
    if not sessions.is_dir():
        return None
    files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for path in files:
        best = None
        try:
            lines = _tail_lines(path)
        except OSError:
            continue
        for line in lines:
            if "rate_limits" not in line:
                continue
            event = codex_text.json_line(line)
            if event is None:
                continue
            payload = event.get("payload") if isinstance(event, dict) else None
            limits = payload.get("rate_limits") if isinstance(payload, dict) else None
            if not isinstance(limits, dict) or limits.get("limit_id") != LIMIT_ID:
                continue
            at = codex_text.parse_timestamp(event.get("timestamp"))
            if at is not None:
                best = {"at": at, "rate_limits": limits}
        if best is not None:
            return best
    return None


def _label(minutes) -> str | None:
    if not budget_producer._num(minutes) or minutes <= 0:
        return None
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "7d"
    return f"{int(minutes)}m"


def account_row(now: float, sessions: Path = SESSIONS) -> dict | None:
    """The engine-status account row for codex-a (budget producer shape), or None when no reading exists."""
    reading = latest_reading(sessions)
    if reading is None:
        return None
    windows = {}
    for key in ("primary", "secondary"):
        w = reading["rate_limits"].get(key)
        if not isinstance(w, dict):
            continue
        label = _label(w.get("window_minutes"))
        used, reset_at = w.get("used_percent"), w.get("resets_at")
        if label is None or not budget_producer._num(used) or not budget_producer._num(reset_at):
            continue
        observed = reading["at"]
        windows[label] = {
            "remaining": 1 - used / 100,
            "reset_at": reset_at,
            "observed_at": observed,
            "state": "FRESH" if budget_producer.window_fresh_at(observed, reset_at, now) else "STALE",
        }
    if not windows:
        return None
    state = "FRESH" if all(v["state"] == "FRESH" for v in windows.values()) else "STALE"
    return {"provider": "openai-codex", "state": state, "observed_at": reading["at"], "windows": windows}


PROBE_MODEL = "gpt-6-luna"
PROBE_AFTER_S = 480.0      # a reading older than this is refreshed (the producer's freshness rule is 900 s)
PROBE_TIMEOUT_S = 90
PROBE_COOLDOWN_S = 1800.0
"""How long a probe that produced no reading blocks the next probe.

The probe is a real model call on the codex-a account. Without a cooldown a probe
that cannot produce a reading is repeated on every producer tick, so the account pays
for calls that yield nothing (review finding on release 751ce3d2, 2026-11-15: a
probe that produces no reading must not be retried on the next tick). The cooldown
applies only to a probe that produced no reading: a probe that did produce one is
governed by the freshness rule alone, so a working account is never held back.
"""
PROBE_STATE = "engine/codex-probe.json"
"""Where the last probe attempt is remembered, so the cooldown survives the producer's exit."""


def _probe_state_path(home: Path) -> Path:
    return Path(home) / PROBE_STATE


def _last_failed_probe(home: Path) -> float | None:
    """When the last probe that produced no reading ran, or None when there was none."""
    try:
        value = read_json(_probe_state_path(home))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("reading") is not False:
        return None
    at = value.get("at")
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def _remember_probe(home: Path, now: float, *, reading: bool) -> None:
    """Record the probe attempt so the cooldown applies to the next producer tick."""
    path = _probe_state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"at": float(now), "reading": bool(reading)})


def refresh(now: float, sessions: Path = SESSIONS, *, run=subprocess.run, home: Path | None = None) -> bool:
    """Write a fresh rate_limits reading by one tiny ``codex exec`` when the newest one is old.

    The router skips an account whose window is not FRESH, so without a reader an idle codex-a would stay
    STALE for ever and no codex wake could start. The probe sends one word, uses a read-only sandbox in an
    empty directory and lets the CLI write its own rollout; no credential is read or copied. Returns True when
    a probe ran. A missing CLI or a failed probe changes nothing: the account then stays STALE, never FRESH.

    A probe that produced no reading starts a cooldown, so a probe that keeps failing cannot become a
    stream of model calls. The cooldown is remembered in the site home, because the producer exits after
    each tick and the next tick is a new process.
    """
    reading = latest_reading(sessions)
    if reading is not None and now - reading["at"] < PROBE_AFTER_S:
        return False
    if home is not None:
        last_failed = _last_failed_probe(home)
        if last_failed is not None and now - last_failed < PROBE_COOLDOWN_S:
            return False
    cli = shutil.which("codex")
    if cli is None:
        return False
    with tempfile.TemporaryDirectory(prefix="codex-probe-") as empty:
        try:
            run([cli, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", PROBE_MODEL, "--json", "-C", empty, "-"],
                input="Reply with the single word ok.", text=True, capture_output=True, timeout=PROBE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            if home is not None:
                _remember_probe(home, now, reading=False)
            return False
    produced = latest_reading(sessions)
    got_reading = produced is not None and (reading is None or produced["at"] > reading["at"])
    if home is not None:
        _remember_probe(home, now, reading=got_reading)
    return True
