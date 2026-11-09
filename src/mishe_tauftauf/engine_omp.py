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


# Provider text for an account that cannot pay for another call (MiniMax 402 "Token Plan usage limit ... insufficient_balance").
QUOTA_MARKERS = ("insufficient_balance", "usage limit has been reached", "usage limit", "status 402", "402 ")


def is_quota_error(error: str | None) -> bool:
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in QUOTA_MARKERS)


class OmpEngine:
    name = "omp"

    def __init__(self, model: str | None = None, env: dict | None = None, thinking: str | None = None):
        self.model, self.extra_env, self.thinking = model, env or {}, thinking

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        prompt_file = Path(prompt_file)
        stream = prompt_file.with_suffix(".out.jsonl")
        code = engine_process.run_bounded(
            command(self.model, Path(cwd), prompt_file, self.thinking), env=dict(env, **self.extra_env),
            cwd=Path(cwd), timeout=timeout, stdout_to=stream, on_start=on_start)
        error = refused_before_effect(stream) if code != 0 else None
        if error is not None and is_quota_error(error):
            raise EngineExhausted(f"account out of quota before any tool call: {error}")
        if error is not None:
            raise EngineRejected(f"model refused before any tool call: {error}")
        return code
