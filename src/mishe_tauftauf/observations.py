from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed
from .predictions import expectations_text

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
FOOTER_WAKE_LABEL = "wake"
FOOTER_WAKE_RE = re.compile(r"^-- wake: .* --$")
FOOTER_LEDGER_LABEL = "ledger"  # Historical frames remain readable.
FOOTER_LEDGER_RE = re.compile(rf"^-- {re.escape(FOOTER_LEDGER_LABEL)}: .* --$")
FOOTER_LEASE_RE = re.compile(r"^-- pane live \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z · refresh [0-9.]+s · ticks every frame --$")


@dataclass(frozen=True)
class RenderedPain:
    slug: str
    body: str
    ok: bool
    reason: str | None = None


def validate_slug(slug: str) -> str:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,63}")
    return slug


def validate_home(home: Path) -> Path:
    """Reject missing homes, repository roots, and uninitialized plant directories.

    Generic standalone feed directories outside Git remain supported. Inside a
    worktree, or for a reserved .mishe-* home, require the initialized runtime
    layout before any writer can create a second conversation tape.
    """
    home = Path(home)
    if not home.is_dir():
        raise ValueError(f"site home does not exist: {home}")
    resolved = home.resolve()
    if (resolved / ".git").exists():
        raise ValueError(f"not an initialized site: {home} (repository root)")
    in_worktree = any((parent / ".git").exists() for parent in resolved.parents)
    if (in_worktree or home.name.startswith(".mishe-") or resolved.name.startswith(".mishe-")) and not (
        (home / "top-pains").is_dir() and (home / "minds").is_dir()
        and ((home / "chat.log").is_file() or (home / "feed").is_file())
    ):
        raise ValueError(f"not an initialized site: {home}")
    return home


def executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def discover(home: Path) -> list[str]:
    directory = home / "top-pains"
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir() if SLUG_RE.fullmatch(path.name) and executable(path))


def _unknown(slug: str, reason: str) -> RenderedPain:
    return RenderedPain(slug, f"UNKNOWN — top-pain {slug} renderer {reason}\n", False, reason)


def run_renderer(home: Path, slug: str, timeout: float = 10.0) -> RenderedPain:
    validate_slug(slug)
    path = home / "top-pains" / slug
    if not executable(path):
        return _unknown(slug, "missing-or-not-executable")
    try:
        result = subprocess.run([str(path)], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _unknown(slug, f"timeout-after-{timeout:g}s")
    except OSError as exc:
        return _unknown(slug, f"launch-failed: {exc}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        return _unknown(slug, f"exit-{result.returncode}" + (f": {detail}" if detail else ""))
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _unknown(slug, f"invalid-utf8: {exc}")
    if not text:
        return _unknown(slug, "empty-output")
    return RenderedPain(slug, text, True)


def check_report(home: Path, slug: str) -> str:
    path = home / "observations" / slug
    if not path.exists():
        return "SYSTEM ZERO\nUNKNOWN — no check report\n"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"SYSTEM ZERO\nUNKNOWN — check report unreadable: {exc}\n"
    return "SYSTEM ZERO\n" + text + ("" if text.endswith("\n") else "\n")


def _watcher(home: Path) -> str:
    path = home / "checks/silence-heartbeat.json"
    if not path.exists():
        return "unknown"
    try:
        at = datetime.fromisoformat(json.loads(path.read_text())["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unreadable"
    return "stale" if (datetime.now(timezone.utc) - at).total_seconds() > 30 else "live"


def headline(home: Path) -> str:
    """A one-line focus: mind state, delivery staleness and watcher liveness."""
    path = home / "checks/silence-watch.json"
    reading = {}
    if path.exists():
        try:
            reading = json.loads(path.read_text())
        except (OSError, ValueError):
            reading = {}
    state = reading.get("state", "UNKNOWN")
    idle = reading.get("idle_seconds")
    watcher = _watcher(home)
    facts = [f"mind {state.lower()}" + (f" idle {idle:.0f}s" if isinstance(idle, (int, float)) else ""),
             f"watcher {watcher}"]
    delivery = reading.get("delivery") or {}
    if isinstance(delivery.get("last_commit_seconds"), (int, float)):
        facts.append(f"commit {delivery['last_commit_seconds'] / 3600:.1f}h ago")
    if isinstance(delivery.get("last_activation_seconds"), (int, float)):
        facts.append(f"activation {delivery['last_activation_seconds'] / 3600:.1f}h ago")
    level = "RED" if state in {"SILENT", "ENDED"} or watcher != "live" else "GREEN" if state == "OK" else "UNKNOWN"
    return f"HEADLINE: {level} — " + "; ".join(facts) + "\n"


def compose_frame(home: Path, slug: str, timeout: float = 10.0) -> RenderedPain:
    rendered = run_renderer(home, slug, timeout)
    from . import wall
    wall.settings(home)
    failure = f"\nRENDERER: RED {rendered.reason or 'command failed'}\n" if not rendered.ok else ""
    checked = "\n" + check_report(home, slug) if (home / "observations" / slug).is_file() else ""
    return RenderedPain(slug, headline(home) + rendered.body + failure + checked + wall.pane(home, slug), rendered.ok, rendered.reason)


def strip_owned_chrome(text: str, *, strip_expectations: bool = False) -> str:
    lines = text.splitlines()
    output: list[str] = []
    in_expectations = False
    for line in lines:
        if FOOTER_WAKE_RE.fullmatch(line) or FOOTER_LEDGER_RE.fullmatch(line) or FOOTER_LEASE_RE.fullmatch(line):
            continue
        if strip_expectations and line == "EXPECTATIONS":
            in_expectations = True
            continue
        if in_expectations:
            continue
        output.append(line)
    return "\n".join(output).rstrip("\n") + "\n"


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    status: str
    diagnostic: str = ""


MAX_PROJECTION_BYTES = 4096


def run_projector(home: Path, slug: str, previous: str, current: str, timeout: float = 2.0) -> str:
    """Return only bounded public text; never use a failed projector's output."""
    path = home / "projectors" / slug
    if not executable(path):
        return f"UNKNOWN — event projector {slug} unavailable"
    with tempfile.TemporaryDirectory(prefix="mishe-tauftauf-project-") as directory:
        previous_path = Path(directory) / "previous"
        current_path = Path(directory) / "current"
        previous_path.write_text(previous, encoding="utf-8")
        current_path.write_text(current, encoding="utf-8")
        try:
            result = subprocess.run([str(path), str(previous_path), str(current_path)], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"UNKNOWN — event projector {slug} timeout"
        except OSError:
            return f"UNKNOWN — event projector {slug} launch-failed"
    if result.returncode != 0:
        return f"UNKNOWN — event projector {slug} exit-{result.returncode}"
    if not 0 < len(result.stdout) <= MAX_PROJECTION_BYTES:
        return f"UNKNOWN — event projector {slug} invalid-size"
    try:
        projection = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return f"UNKNOWN — event projector {slug} invalid-utf8"
    if not projection.strip():
        return f"UNKNOWN — event projector {slug} empty"
    return projection


def run_filter(home: Path, slug: str, previous: str, current: str, timeout: float = 2.0) -> FilterResult:
    path = home / "filters" / slug
    if not path.exists():
        return FilterResult(previous != current, "default-change" if previous != current else "default-hold")
    if not executable(path):
        return FilterResult(True, "error", f"UNKNOWN event filter {slug}: not executable")
    with tempfile.TemporaryDirectory(prefix="mishe-tauftauf-filter-") as directory:
        prev_path = Path(directory) / "previous"
        cur_path = Path(directory) / "current"
        prev_path.write_text(previous, encoding="utf-8")
        cur_path.write_text(current, encoding="utf-8")
        try:
            result = subprocess.run([str(path), str(prev_path), str(cur_path)], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return FilterResult(True, "error", f"UNKNOWN event filter {slug}: timeout after {timeout:g}s")
        except OSError as exc:
            return FilterResult(True, "error", f"UNKNOWN event filter {slug}: launch failed: {exc}")
    # Filter stderr can contain the raw pane. It is transient and never durable.
    diagnostic = ""
    if result.returncode == 0:
        return FilterResult(True, "pass", diagnostic)
    if result.returncode == 1:
        return FilterResult(False, "hold", diagnostic)
    return FilterResult(True, "error", f"UNKNOWN event filter {slug}: exit {result.returncode}")
