"""omp as a one-shot engine: ``omp --print --no-session`` reading the dossier file."""
from __future__ import annotations

from pathlib import Path

from . import engine_process


def command(model: str | None, cwd: Path, prompt_file: Path) -> list[str]:
    cmd = ["omp", "--print", "--no-session", "--no-extensions", "--approval-mode", "yolo",
           "--cwd", str(cwd)]
    if model:
        cmd += ["--model", model]
    return cmd + [f"@{prompt_file}"]


class OmpEngine:
    name = "omp"

    def __init__(self, model: str | None = None, env: dict | None = None):
        self.model, self.extra_env = model, env or {}

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        prompt_file = Path(prompt_file)
        return engine_process.run_bounded(
            command(self.model, Path(cwd), prompt_file), env=dict(env, **self.extra_env),
            cwd=Path(cwd), timeout=timeout, stdout_to=prompt_file.with_suffix(".out.txt"), on_start=on_start)
