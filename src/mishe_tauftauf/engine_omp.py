"""omp as a one-shot engine: ``omp --print --no-session`` reading the dossier file."""
from __future__ import annotations

from pathlib import Path

from . import engine_process


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


class OmpEngine:
    name = "omp"

    def __init__(self, model: str | None = None, env: dict | None = None, thinking: str | None = None):
        self.model, self.extra_env, self.thinking = model, env or {}, thinking

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        prompt_file = Path(prompt_file)
        return engine_process.run_bounded(
            command(self.model, Path(cwd), prompt_file, self.thinking), env=dict(env, **self.extra_env),
            cwd=Path(cwd), timeout=timeout, stdout_to=prompt_file.with_suffix(".out.jsonl"), on_start=on_start)
