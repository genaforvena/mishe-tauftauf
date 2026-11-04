"""Claude Code as a one-shot engine: ``claude -p`` reading the dossier on stdin."""
from __future__ import annotations

from pathlib import Path

from . import engine_process

ALLOW = ("Bash", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Agent")
APPEND = "You are a Mishe mind, woken for one turn. Follow AGENTS.md in the working directory. End by running `mishe yield`."


def command(model: str, tools: tuple = ALLOW) -> list[str]:
    return ["claude", "-p", "--model", model, "--permission-mode", "dontAsk",
            "--allowedTools", *tools, "--setting-sources", "project,local",
            "--no-session-persistence", "--output-format", "json",
            "--append-system-prompt", APPEND]


class ClaudeEngine:
    name = "claude"

    def __init__(self, model: str, env: dict | None = None, tools: tuple = ALLOW):
        self.model, self.extra_env, self.tools = model, env or {}, tuple(tools)

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        prompt_file = Path(prompt_file)
        return engine_process.run_bounded(
            command(self.model, self.tools), env=dict(env, **self.extra_env), cwd=Path(cwd), timeout=timeout,
            stdin_from=prompt_file, stdout_to=prompt_file.with_suffix(".out.json"), on_start=on_start)
