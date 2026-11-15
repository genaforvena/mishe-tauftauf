"""Codex CLI as a one-shot engine: ``codex exec`` reading the dossier on stdin.

Mishe drives the ``codex`` CLI that already holds the ChatGPT login. It never copies
the OAuth tokens into an omp store: two clients sharing one rotating refresh token can
invalidate both (operator DM 15:20Z, 2026-11-15).

Built flagged off: no route in ``engine/routes.json`` names ``codex`` yet. The sandbox
mode is a security decision still open with the operator (minds commit, run systemctl and
tmux and push), so :data:`SANDBOX` is a named default, not a settled policy.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import engine_process
from .engine_omp import is_quota_error, is_rate_limit_error
from .model_policy import refuse_banned_model
from .wake import EngineExhausted, EngineRateLimited, EngineRejected

SANDBOX = "workspace-write"
"""Sandbox passed as ``-s``. Pending the operator's decision; workspace-write is too narrow for minds."""

THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh")
EFFECT_ITEM_TYPES = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})
"""Item kinds that mean the model acted (a tool or file effect happened), not just answered."""
ERROR_EVENT_TYPES = frozenset({"error", "turn.failed"})

# The ChatGPT-account refusal for a model name the account cannot use (measured 2026-11-15).
UNSUPPORTED_MARKERS = ("is not supported when using codex with a chatgpt account", "model is not supported")


def command(model: str, cwd: Path, sandbox: str = SANDBOX, thinking: str | None = None) -> list[str]:
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", sandbox,
           "-m", model, "--json", "-C", str(cwd)]
    if thinking in THINKING_LEVELS:
        cmd += ["-c", f'model_reasoning_effort="{thinking}"']
    # The trailing "-" makes codex read the prompt from stdin. Without it the CLI waits on
    # "Reading additional input from stdin" forever.
    return cmd + ["-"]


def events(stream: Path):
    """Each parseable JSONL event of a codex stream; malformed lines are skipped, never trusted."""
    if not stream.is_file():
        return
    with stream.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def _error_text(event: dict) -> str | None:
    """The provider text of an error event, or None when the event is not an error."""
    if event.get("type") not in ERROR_EVENT_TYPES:
        return None
    error = event.get("error")
    if isinstance(error, dict):
        text = error.get("message")
    else:
        text = error if isinstance(error, str) else event.get("message")
    return str(text or event.get("type"))[:300]


def acted(stream: Path) -> bool:
    """True once codex ran a tool or changed a file: the effect may have happened."""
    for event in events(stream):
        item = event.get("item")
        if event.get("type", "").startswith("item.") and isinstance(item, dict) and item.get("type") in EFFECT_ITEM_TYPES:
            return True
    return False


def refused_before_effect(stream: Path) -> str | None:
    """The provider error that ended a stream with no tool or file effect, else None."""
    if acted(stream):
        return None
    error = None
    for event in events(stream):
        text = _error_text(event)
        if text is not None:
            error = text
    return error


def spent_quota_after_effect(stream: Path) -> str | None:
    """A quota error codex returned after it acted: the account is spent, the effect is unknown."""
    error = None
    for event in events(stream):
        text = _error_text(event)
        if text and is_quota_error(text):
            error = text
    return error


def is_unsupported_model(error: str | None) -> bool:
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in UNSUPPORTED_MARKERS)


class CodexEngine:
    name = "codex"

    def __init__(self, model: str, env: dict | None = None, thinking: str | None = None, sandbox: str = SANDBOX):
        refuse_banned_model(model)
        self.model, self.extra_env, self.thinking, self.sandbox = model, env or {}, thinking, sandbox
        self.stream: Path | None = None

    def spent_quota(self) -> str | None:
        return spent_quota_after_effect(self.stream) if self.stream else None

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None, hard_timeout=None) -> int:
        prompt_file = Path(prompt_file)
        stream = self.stream = prompt_file.with_suffix(".out.jsonl")
        code = engine_process.run_bounded(
            command(self.model, Path(cwd), self.sandbox, self.thinking), env=dict(env, **self.extra_env),
            cwd=Path(cwd), timeout=timeout, stdin_from=prompt_file, stdout_to=stream,
            on_start=on_start, hard_timeout=hard_timeout)
        error = refused_before_effect(stream) if code != 0 else None
        if error is not None and is_quota_error(error):
            raise EngineExhausted(f"account out of quota before any tool call: {error}")
        if error is not None and is_rate_limit_error(error):
            raise EngineRateLimited(f"provider rate limit before any tool call: {error}")
        if error is not None:
            kind = "model not usable on this account" if is_unsupported_model(error) else "model refused"
            raise EngineRejected(f"{kind} before any tool call: {error}")
        return code
