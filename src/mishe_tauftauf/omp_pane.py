"""Decode a pane's own omp model from its process command line.

Three external spellings meet here, so consumers act on decoded values:

* ``/proc/<pid>/cmdline`` is NUL-separated bytes, an external kernel dialect.
  The fields are decoded once, here, and an unreadable or empty file reads
  ``None`` rather than an empty success.
* omp names one model differently in different places. A launcher writes the
  provider-qualified ``opencode-go/longcat-2.5-preview-free`` while the session
  log records the bare ``longcat-2.5-preview-free``, and a name may carry more
  than one ``/``. The final path segment is the model's identity, so that is
  what ``same_model`` compares; no provider prefix is guessed.
* A malformed launcher can leave ``--model`` without a usable value: the flag
  last, an empty ``--model=``, or another option (``--model --thinking``) as its
  value. None of those is a model name, so ``model`` reads ``None`` rather than
  a value that would make the wedge reader drop every record it should count.
* A captured pane is a fourth dialect: the omp TUI frame with volatile spinner
  glyphs, elapsed timers and throughput counters over stable tool text. The
  ``normalize_frame``/``wait_pattern``/``ci_run_id`` decoders below read that
  dialect once, here, so the watch-wait consumer binds a verdict to decoded
  values rather than parsing renderer text itself. An unparseable frame reads
  as no pattern, never a clean bill.

``sense.mind.wedge-suspect`` needs the model decoders: an omp session log
carries provider errors from every agent the pane hosts, including the title
generator and spawned subagents, whose model differs from the mind's, so the
reader must know which model the pane's own process runs before it can
attribute an error.
"""
from __future__ import annotations

import re

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
    """The ``--model`` a pane process was launched with, or ``None``.

    A malformed launcher can leave the flag without a usable value: ``--model``
    last, an empty ``--model=``, or another option as the value (``--model
    --thinking``). None of those is a model name, so this reads ``None``, which
    the caller treats as "cannot attribute" and counts rather than hides.
    """
    value = option(argv, "--model")
    if not value or value.startswith("-"):
        return None
    return value


def same_model(left: str, right: str) -> bool:
    """Whether two model names denote the same model."""
    return left.rsplit("/", 1)[-1] == right.rsplit("/", 1)[-1]

_SPINNER_GLYPHS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
"""Braille spinner frames the omp/opencode TUI advances without new content."""

_FRAME_CLOCK_RE = re.compile("·\\s*\\d+[smh]\\d*s?")
"""An elapsed timer (``· 2m8s``) the frame advances while a tool waits."""

_THROUGHPUT_RE = re.compile("\\d+(?:\\.\\d+)?\\s*(?:tok/s|/s)")
"""A throughput counter that churns while tool output is unchanged."""


def normalize_frame(text: str) -> str:
    """The stable tool text of one captured pane frame.

    Strips volatile TUI chrome — spinner glyphs, elapsed timers, throughput
    counters — so two captures of the same waiting tool hash equal while a
    frame that gained tool output hashes different. Junk in yields the junk
    out, never a guessed clean frame: the caller binds UNKNOWN on unreadable
    input, not on this function's output.
    """
    normalized = "".join(char for char in text if char not in _SPINNER_GLYPHS)
    normalized = _FRAME_CLOCK_RE.sub("", normalized)
    normalized = _THROUGHPUT_RE.sub("", normalized)
    return "\n".join(line.rstrip() for line in normalized.splitlines())


_WAIT_COMMAND_RE = re.compile(
    r"^[\$>] (?:sleep\s+\d+|tail\s+-f(?:\s+\S+)?|"
    r"gh run (?:watch|view)\s+\d{6,}(?:\s+--[\w-]+)*)[ \t]*$",
    re.MULTILINE,
)
"""A shell command shape the sense may excuse, with its own prompt.

Bare spellings like ``sleep`` or ``waiting on`` match prose (e.g. a commit
subject ``reduce sleep in the CI follower``), so a still frame reads
``watch-wait`` on prose alone. The shell commands therefore need the shape a
typed command has: a prompt, the command, its own argument boundary and the
end of the line — ``$ sleep 150``, ``$ tail -f /var/log/syslog``,
``$ gh run watch 38968299950``. Line-start prose cannot supply the prompt,
and a command's argument boundary keeps ``tail -foo`` from reading as
``tail -f``.
"""

_WAIT_STATUS_RE = re.compile(r"^Waiting for CI poll result[ \t]*$", re.MULTILINE)
"""The omp status line that names its own wait, without a shell prompt."""

_CI_RUN_RE = re.compile("gh run (?:watch|view)\\s+(\\d{6,})")
"""A GitHub Actions run id named beside a poll command in pane text."""


def wait_pattern(text: str) -> str | None:
    """The first allowlisted wait spelling present in pane text, or ``None``.

    A miss reads ``None`` — the consumer holds UNKNOWN/SUSPECT, never clean —
    so a TUI rewording or a new long-poll shape cannot silently pass as idle.
    The match is anchored to command shape: a bare ``sleep`` or ``waiting on``
    in prose does not excuse a wait, and neither does prose that merely
    starts with a command word.
    """
    normalized = normalize_frame(text)
    match = _WAIT_COMMAND_RE.search(normalized)
    if match is not None:
        matched = match.group(0)
        if matched.lstrip("$> ").startswith("sleep"):
            return "sleep"
        if "tail -f" in matched:
            return "tail -f"
        if "gh run watch" in matched:
            return "gh run watch"
        return "gh run view"
    if _WAIT_STATUS_RE.search(normalized):
        return "Waiting for CI poll result"
    return None


def ci_run_id(text: str) -> str | None:
    """The CI run id a poll command names in pane text, or ``None``."""
    match = _CI_RUN_RE.search(normalize_frame(text))
    return match.group(1) if match else None


def frame_empty(text: str) -> bool:
    """Whether a captured pane frame carries no visible text."""
    return not text.strip()


def ci_run_state(raw: str) -> dict | None:
    """The CI state from one ``gh run view --json`` payload, or ``None``.

    The payload is an external dialect (GitHub CLI JSON), decoded once here:
    a ``status`` string names the run state, ``conclusion`` and ``updatedAt``
    ride along as opaque values. Anything else — malformed JSON, a non-dict,
    a missing or empty status — reads ``None``, so the caller holds UNKNOWN
    rather than excusing a wait on a state it could not read.
    """
    import json

    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        return None
    return {"status": status, "conclusion": payload.get("conclusion"),
            "updated_at": payload.get("updatedAt")}
