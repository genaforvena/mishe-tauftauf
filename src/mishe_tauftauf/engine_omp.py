"""omp as a one-shot engine: ``omp --print --no-session`` reading the dossier file."""
from __future__ import annotations

from pathlib import Path

from . import engine_process
from .router_text import stream_events
from .wake import EngineExhausted, EngineRejected


# omp's own default thinking is high: a wake on longcat-2.5-free spent 19k thinking deltas against 165 of text
# and ran out its 1800 s (pub, 2026-11-09). A wake is agentic work in small steps: think a little, act more.
DEFAULT_THINKING = "low"
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")
FIRST_OUTPUT_S = 120.0
"""How long a wake waits for the model's first assistant event before the run is cut (operator DM 299288)."""


def command(model: str | None, cwd: Path, prompt_file: Path, thinking: str | None = None) -> list[str]:
    level = thinking if thinking in THINKING_LEVELS else DEFAULT_THINKING
    cmd = ["omp", "--print", "--no-session", "--no-extensions", "--approval-mode", "yolo",
           "--mode", "json", "--thinking", level, "--cwd", str(cwd)]
    if model:
        cmd += ["--model", model]
    return cmd + [f"@{prompt_file}"]


def refused_before_effect(stream: Path) -> str | None:
    """The model error that ended a stream with no tool call (so nothing ran), else None."""
    error = None
    for event in stream_events(stream):
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if any(isinstance(b, dict) and b.get("type") == "toolCall" for b in message.get("content") or []):
            return None
        if message.get("role") == "assistant" and message.get("stopReason") == "error":
            error = str(message.get("errorMessage") or "model error")[:300]
    return error


def saw_assistant_event(stream: Path) -> bool:
    """True once the stream holds an assistant message_start: the model's first response.

    The user turn's message_start comes first, so this marker means the model has actually
    begun answering. A stalled provider can write a few bytes (a partial line) and then go
    quiet; byte growth alone would not notice, so the first-output bound uses this event.
    """
    for event in stream_events(stream):
        if event.get("type") == "message_start":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                return True
    return False


# Provider text for an account that cannot pay for another call (MiniMax 402 "Token Plan usage limit ... insufficient_balance").
QUOTA_MARKERS = ("insufficient_balance", "usage limit has been reached", "usage limit", "status 402", "402 ")


def is_quota_error(error: str | None) -> bool:
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in QUOTA_MARKERS)


def spent_quota_after_effect(stream: Path) -> str | None:
    """The quota error the provider returned to the run's own retry or assistant turn, else None.

    Only structured provider errors count (an auto-retry that ended in failure, an assistant error turn), never
    tool output: a tool result that quotes quota text is not the account running out. The effect may already have
    happened (tool calls ran), so the caller cools the account and does not fall through to another candidate.
    """
    error = None
    for event in stream_events(stream):
        if event.get("type") == "auto_retry_end" and event.get("success") is False:
            text = event.get("finalError")
        else:
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("stopReason") != "error":
                continue
            text = message.get("errorMessage")
        if text and is_quota_error(str(text)):
            error = str(text)[:300]
    return error


class OmpEngine:
    name = "omp"

    def __init__(self, model: str | None = None, env: dict | None = None, thinking: str | None = None):
        self.model, self.extra_env, self.thinking = model, env or {}, thinking
        self.stream: Path | None = None

    def spent_quota(self) -> str | None:
        """After a run: the quota error that ended it after acting, for the router to cool the account."""
        return spent_quota_after_effect(self.stream) if self.stream else None

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None, hard_timeout=None) -> int:
        prompt_file = Path(prompt_file)
        stream = self.stream = prompt_file.with_suffix(".out.jsonl")
        code = engine_process.run_bounded(
            command(self.model, Path(cwd), prompt_file, self.thinking), env=dict(env, **self.extra_env),
            cwd=Path(cwd), timeout=timeout, stdout_to=stream, on_start=on_start, hard_timeout=hard_timeout,
            first_output_s=FIRST_OUTPUT_S, first_event=lambda: saw_assistant_event(stream))
        error = refused_before_effect(stream) if code != 0 else None
        if error is not None and is_quota_error(error):
            raise EngineExhausted(f"account out of quota before any tool call: {error}")
        if error is not None:
            raise EngineRejected(f"model refused before any tool call: {error}")
        return code
