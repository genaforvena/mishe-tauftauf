"""Decode a pane's own omp model from its process command line.

Two external spellings meet here, so consumers act on decoded values:

* ``/proc/<pid>/cmdline`` is NUL-separated bytes, an external kernel dialect.
  The fields are decoded once, here, and an unreadable or empty file reads
  ``None`` rather than an empty success.
* omp names one model differently in different places. A launcher writes the
  provider-qualified ``opencode-go/longcat-2.5-preview-free`` while the session
  log records the bare ``longcat-2.5-preview-free``, and a name may carry more
  than one ``/``. The final path segment is the model's identity, so that is
  what ``same_model`` compares; no provider prefix is guessed.

``sense.mind.wedge-suspect`` needs both: an omp session log carries provider
errors from every agent the pane hosts, including the title generator and
spawned subagents, whose model differs from the mind's, so the reader must know
which model the pane's own process runs before it can attribute an error.
"""
from __future__ import annotations

from pathlib import Path


def command_line(pid: int) -> list[str] | None:
    """The process's argv, or ``None`` when its command line is unreadable."""
    try:
        raw = Path("/proc", str(pid), "cmdline").read_bytes()
    except OSError:
        return None
    return [field.decode("utf-8", "replace") for field in raw.split(b"\0") if field]


def option(argv: list[str], name: str) -> str | None:
    """The value of ``--name value`` or ``--name=value`` in an argv, or ``None``."""
    prefix = name + "="
    for index, field in enumerate(argv):
        if field == name and index + 1 < len(argv):
            return argv[index + 1]
        if field.startswith(prefix):
            return field[len(prefix):]
    return None


def model(argv: list[str]) -> str | None:
    """The ``--model`` a pane process was launched with, or ``None``."""
    return option(argv, "--model")


def same_model(left: str, right: str) -> bool:
    """Whether two model names denote the same model."""
    return left.rsplit("/", 1)[-1] == right.rsplit("/", 1)[-1]
