"""Claude Code as a one-shot engine: ``claude -p`` reading the dossier on stdin."""
from __future__ import annotations

import time
from pathlib import Path

from . import claude_error_text
from . import engine_process
from .model_policy import refuse_banned_model
from .wake import EngineExhausted, EngineRateLimited, EngineRejected

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
        refuse_banned_model(model)
        self.model, self.extra_env, self.tools = model, env or {}, tuple(tools)
        self.document: dict | None = None
        self.reset_at: float | None = None

    def spent_quota(self) -> str | None:
        """After a run: the quota error that ended it after acting, for the router to cool the account.

        A refusal that arrived before any inference had no effect, so the engine raises it
        instead and the router falls through. A refusal after inference may have acted, so
        the effect is unknown and the account is cooled without falling through.
        """
        document = self.document or {}
        text = claude_error_text.refusal(document)
        if text is None or not claude_error_text.inferred(document):
            return None
        if claude_error_text.is_quota_error(text):
            return text[:300]
        return None

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None, hard_timeout=None) -> int:
        prompt_file = Path(prompt_file)
        out = prompt_file.with_suffix(".out.json")
        code = engine_process.run_bounded(
            command(self.model, self.tools), env=dict(env, **self.extra_env), cwd=Path(cwd), timeout=timeout,
            stdin_from=prompt_file, stdout_to=out, on_start=on_start,
            hard_timeout=hard_timeout)
        document = claude_error_text.load(out)
        self.document = document
        if code == 0:
            return code
        text = claude_error_text.refusal(document)
        if text is None:
            # The CLI failed without a refusal document (a crash, a signal, unreadable
            # output): the run's effect is unknown, not absent.
            return code
        self.reset_at = claude_error_text.reset_at(document, now=time.time())
        if claude_error_text.inferred(document):
            # The model answered before the refusal, so the effect may have happened:
            # the router cools the account and does not fall through.
            return code
        if claude_error_text.is_quota_error(text):
            raise EngineExhausted(f"account out of quota before any tool call: {text}")
        if claude_error_text.is_rate_limit_error(text):
            raise EngineRateLimited(f"provider rate limit before any tool call: {text}")
        raise EngineRejected(f"model refused before any tool call: {text}")
