"""Qwen Code as a one-shot engine: ``qwen -p`` reading the dossier on stdin.

Mishe drives the ``qwen`` CLI that already holds the account login; it never copies
credentials into another store. The headless result envelope has the same shape as
``claude -p --output-format json`` (measured 2026-11-16), so the retained ``.out.json``
is read by the same envelope reader and no new parsing boundary is needed.
"""
from __future__ import annotations

from pathlib import Path

from . import engine_process
from .engine_claude import ALLOW, APPEND
from .model_policy import refuse_banned_model


def command(model: str, tools: tuple = ALLOW) -> list[str]:
    cmd = ["qwen", "-p", "--model", model, "--permission-mode", "dont_ask",
           "--setting-sources", "project,local", "--no-session-persistence",
           "--output-format", "json", "--append-system-prompt", APPEND]
    for tool in tools:
        cmd += ["--allowed-tools", tool]
    return cmd


class QwenEngine:
    name = "qwen"

    def __init__(self, model: str, env: dict | None = None, tools: tuple = ALLOW):
        refuse_banned_model(model)
        self.model, self.extra_env, self.tools = model, env or {}, tuple(tools)

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None, hard_timeout=None) -> int:
        prompt_file = Path(prompt_file)
        # qwen writes advisory warnings (e.g. a blocked AGENTS.md @import) to stderr on every
        # run in this repo; merged into stdout they would make the one JSON envelope unreadable.
        return engine_process.run_bounded(
            command(self.model, self.tools), env=dict(env, **self.extra_env), cwd=Path(cwd), timeout=timeout,
            stdin_from=prompt_file, stdout_to=prompt_file.with_suffix(".out.json"),
            stderr_to=prompt_file.with_suffix(".err"), on_start=on_start, hard_timeout=hard_timeout)