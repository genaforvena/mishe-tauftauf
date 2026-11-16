"""Which engine a role wakes on. ``claude``, ``omp``, ``codex`` and ``qwen`` exist; codex is routed only when a route names it."""
from __future__ import annotations

from pathlib import Path

from .engine_claude import ALLOW, ClaudeEngine
from .engine_omp import OmpEngine
from .state_files import read_json

DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
DEFAULT_CODEX_MODEL = "gpt-6-luna"
"""The codex model a role gets when its config names the engine but no model.

A config of ``{"engine": "codex"}`` used to build a ``-m None`` argv, and the
``TypeError`` from ``subprocess.Popen`` escaped the wake as an uncaught crash
(review finding on release bcd536a3, 2026-11-15). The claude branch has had a
default all along; codex now has one too.
"""
DEFAULT_QWEN_MODEL = "qmodel_latest"


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
    if kind == "codex":
        from .engine_codex import CodexEngine
        return CodexEngine(config.get("model") or DEFAULT_CODEX_MODEL, env, config.get("thinking"))
    if kind == "qwen":
        from .engine_qwen import QwenEngine
        tools = tuple(config.get("tools") or ALLOW)
        return QwenEngine(config.get("model") or DEFAULT_QWEN_MODEL, env, tools)
    raise ValueError(f"unsupported engine {kind!r} for {role!r}; use claude, omp, codex or qwen")
