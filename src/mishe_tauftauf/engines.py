"""Which engine a role wakes on. Only ``claude`` and ``omp`` exist."""
from __future__ import annotations

from pathlib import Path

from .engine_claude import ALLOW, ClaudeEngine
from .engine_omp import OmpEngine
from .state_files import read_json

DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"


def for_role(home, role: str):
    from . import engine_router
    routed = engine_router.for_role(home, role)
    if routed is not None:
        return routed
    config = read_json(Path(home) / "engines" / f"{role}.json") or {}
    kind = config.get("engine", "claude")
    env = config.get("env", {})
    if kind == "claude":
        tools = tuple(config.get("tools") or ALLOW)
        return ClaudeEngine(config.get("model", DEFAULT_CLAUDE_MODEL), env, tools)
    if kind == "omp":
        return OmpEngine(config.get("model"), env, config.get("thinking"))
    raise ValueError(f"unsupported engine {kind!r} for {role!r}; use claude or omp")
