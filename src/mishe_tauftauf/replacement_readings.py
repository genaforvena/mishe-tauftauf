"""Deterministic replacement readings for legacy runtime-only senses.

Two of the legacy readings the migrated scanner no longer emits describe the
plant's own tmux and renderer: ``sense.tmux.windows`` and
``sense.runtime.renderer-coverage``. Each reader here is a pure function over
injected observations, plus one thin probe, so the decision rules are testable
without a live tmux server.

Validity rules: a probe that cannot run, a missing socket, or an unreadable
pinned root reads UNKNOWN, never calm. A reachable server with no windows is a
fact (verified, count 0) only when the server itself answered.

Nothing here is wired into the running scanner; the wiring is a reviewed patch.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

TMUX_SENSE_ID = "sense.tmux.windows"
RENDERER_SENSE_ID = "sense.runtime.renderer-coverage"

# Modules the plant view entry point needs at the pinned runtime. The view host
# execs ``mishe_tauftauf.plant_view``; the boundary module is imported by it.
RENDERER_MODULES = ("plant_view", "plant_view_boundary")

from .plant_tmux import WINDOW_FORMAT as _WINDOW_FORMAT, parse_list_windows


def _run_tmux(socket: str, args: list[str], timeout: float = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["tmux", "-S", socket, *args], text=True,
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def read_tmux_windows(socket: str | None, *, runner=_run_tmux) -> dict[str, object]:
    """Sense of the inner tmux server's windows, or UNKNOWN with the cause.

    `runner(socket, args)` returns a CompletedProcess or None (probe could not
    run). Exit 0 with parseable output is verified. Any other outcome is UNKNOWN,
    because "no server" and "probe broken" look alike from the outside.
    """
    base = {"id": TMUX_SENSE_ID, "kind": "read", "source": socket or "unset"}
    if not socket:
        return {**base, "state": "unknown", "sample": "no tmux socket configured"}
    result = runner(socket, ["list-windows", "-a", "-F", _WINDOW_FORMAT])
    if result is None:
        return {**base, "state": "unknown", "sample": "tmux probe could not run"}
    if result.returncode != 0:
        return {**base, "state": "unknown",
                "sample": f"tmux list-windows exit={result.returncode} (no server or refused)"}
    windows = parse_list_windows(result.stdout or "")
    if windows is None:
        return {**base, "state": "unknown", "sample": "tmux list-windows output malformed"}
    if not windows:
        return {**base, "state": "verified", "sample": "windows=0 (server answered)",
                "windows": []}
    names = sorted({f"{w['session']}:{w['window']}" for w in windows})
    panes = sum(int(w["panes"]) for w in windows)
    return {**base, "state": "verified",
            "sample": f"windows={len(windows)} panes={panes} names={','.join(names)}",
            "windows": windows}


def _module_problem(path: Path) -> str | None:
    """Return None when `path` is a parseable module, else a failure reason."""
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    except (OSError, UnicodeDecodeError) as exc:
        return f"unreadable: {exc.__class__.__name__}"
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return f"unparseable at line {exc.lineno}"
    return None


def read_renderer_coverage(pin_root: Path | None, *,
                           modules: tuple[str, ...] = RENDERER_MODULES) -> dict[str, object]:
    """Whether the pinned runtime carries every module the renderer needs.

    A missing or unparseable module is a verified drift fact (state "drift"), since
    the root was read. An unset or unreadable root is UNKNOWN: the absence of a
    root is not the same as a complete renderer.
    """
    base = {"id": RENDERER_SENSE_ID, "kind": "read", "source": str(pin_root or "unset")}
    if pin_root is None or not pin_root.is_dir():
        return {**base, "state": "unknown", "sample": "pinned runtime root not readable"}
    package = pin_root / "src" / "mishe_tauftauf"
    problems = []
    for name in modules:
        reason = _module_problem(package / f"{name}.py")
        if reason is not None:
            problems.append(f"{name}.py={reason}")
    if problems:
        return {**base, "state": "drift",
                "sample": "renderer modules incomplete: " + "; ".join(problems),
                "missing": problems}
    return {**base, "state": "verified",
            "sample": f"renderer modules present: {','.join(f'{m}.py' for m in modules)}"}
