from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .feed import Feed
from .predictions import expectations_text

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
FOOTER_RUNTIME_RE = re.compile(r"^-- runtime: .* --$")
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


def compose_frame(home: Path, slug: str, timeout: float = 10.0) -> RenderedPain:
    rendered = run_renderer(home, slug, timeout)
    entries = Feed(home).entries()
    body = rendered.body
    if not body.endswith("\n"):
        body += "\n"
    body += "\n" + check_report(home, slug) + "\n" + expectations_text(home, entries, slug)
    return RenderedPain(slug, body, rendered.ok, rendered.reason)


def strip_owned_chrome(text: str, *, strip_expectations: bool = False) -> str:
    lines = text.splitlines()
    output: list[str] = []
    in_expectations = False
    for line in lines:
        if FOOTER_RUNTIME_RE.fullmatch(line) or FOOTER_LEASE_RE.fullmatch(line):
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
    diagnostic = result.stderr.decode("utf-8", "replace").strip()
    if result.returncode == 0:
        return FilterResult(True, "pass", diagnostic)
    if result.returncode == 1:
        return FilterResult(False, "hold", diagnostic)
    return FilterResult(True, "error", f"UNKNOWN event filter {slug}: exit {result.returncode}" + (f": {diagnostic}" if diagnostic else ""))
