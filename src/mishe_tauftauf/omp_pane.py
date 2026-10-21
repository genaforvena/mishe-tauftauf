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


_TUI_BOX_TOP_RE = re.compile(r"^\u256d[-\u2500]*\u256e")
"""The top border of an omp tool-call box."""

_TUI_BOX_BOTTOM_RE = re.compile(r"^\u2570[-\u2500]*\u256f")
"""The bottom border of an omp tool-call box."""

_TUI_OUTPUT_RULE_RE = re.compile(r"^\u251c.*\bOutput\b")
"""The rule that divides a tool-call box's command from its output."""

_TUI_BOX_EDGE_RE = re.compile(r"^[\u2502\u2503]\s?")
"""The box edge the TUI draws before every line of a tool call."""

_TUI_BOX_EDGE_END_RE = re.compile(r"[\u2502\u2503]$")
"""The same edge at the right of the box, which closes a padded command."""

_PROMPT_RE = re.compile(r"^[\$>] ")
"""A shell prompt: the TUI draws it before the command it is running."""

_SHELL_SPLIT_RE = re.compile(r"\|\||&&|;|\|")
"""Shell operators that separate one command from the next in a line."""

_WAIT_SHAPES = (
    ("sleep", re.compile(r"sleep\s+\d+s?\b")),
    ("tail -f", re.compile(r"tail\s+-f\b(?:\s+\S+)?")),
    ("gh run watch", re.compile(r"gh run watch\s+\d{6,}")),
    ("gh run view", re.compile(r"gh run view\s+\d{6,}")),
)
"""The wait spellings a command may carry, each with its own argument boundary.

``sleep`` needs a duration, ``tail -f`` its own flag, and a ``gh run`` poll
its own verb and a run id, because the run id is what names the run whose
state confirms the wait. The boundary is what keeps ``tail -foo`` from
reading as ``tail -f`` and ``sleepy`` from reading as ``sleep``.
"""

_WAIT_STATUS_RE = re.compile(r"^Waiting for CI poll result[ \t]*$", re.MULTILINE)
"""The omp status line that names its own wait, without a shell prompt."""

_CI_RUN_RE = re.compile("gh run (?:watch|view)\\s+(\\d{6,})")
"""A GitHub Actions run id named beside a poll command in pane text."""


def _command_lines(text: str) -> list[str]:
    """The command text a pane shows, one entry per command line.

    The TUI draws a tool call as a box: the command above the ``Output``
    rule, the tool's output below it. Only the command region is a command,
    so a tool that prints ``$ sleep 150`` in its output does not excuse a
    still pane. Outside every box the mind's own prose is not a command
    either; a line there counts only when it carries a shell prompt, which
    is how a plain shell renders a command.

    A prompt is stripped from the head of the line, so a segment that
    follows a shell operator is compared on its own: ``$ sleep 90 && cat x``
    names two commands and only the first carries the prompt.
    """
    lines: list[str] = []
    in_box = False
    in_output = False
    first_in_box = False
    for line in text.splitlines():
        if _TUI_BOX_TOP_RE.match(line):
            in_box, in_output, first_in_box = True, False, True
            continue
        if in_box:
            if _TUI_OUTPUT_RULE_RE.match(line):
                in_output = True
                continue
            if _TUI_BOX_BOTTOM_RE.match(line):
                in_box, in_output, first_in_box = False, False, False
                continue
            if in_output:
                continue
            body = _TUI_BOX_EDGE_RE.sub("", line, count=1)
            body = _TUI_BOX_EDGE_END_RE.sub("", body.rstrip()).rstrip()
        else:
            body = line
            if not _PROMPT_RE.match(body):
                continue
        if _PROMPT_RE.match(body):
            body = body[2:]
        elif first_in_box:
            # A truncated command region opens with the TUI's own marker.
            body = body.lstrip()
            first_in_box = False
        else:
            body = body.strip()
        lines.append(body)
    return lines


def _segment_shape(segment: str) -> str | None:
    """The wait spelling one command segment carries, or ``None``.

    A segment is a single command: the shape must open it, and the rest must
    be flags or their values. That keeps a sentence that begins with a
    command word (``sleep 150 is too long for CI``) from reading as a wait,
    while a real poll's own flags (``--json status,conclusion``) still do.
    """
    for name, shape in _WAIT_SHAPES:
        match = shape.match(segment)
        if match is None:
            continue
        if _flags_only(segment[match.end():]):
            return name
    return None


def _flags_only(tail: str) -> bool:
    """Whether the text after a wait spelling is only flags and their values.

    A flag may take one value, and a value is any token that is not itself
    a flag; ``--exit-status`` takes none and ``--json status,conclusion``
    takes one. A bare word that is not a flag's value ends the command.
    """
    tokens = tail.split()
    index = 0
    while index < len(tokens):
        if not tokens[index].startswith("--"):
            return False
        index += 1
        if index < len(tokens) and not tokens[index].startswith("--"):
            index += 1
    return True


def wait_pattern(text: str) -> str | None:
    """The first allowlisted wait spelling a pane's command carries, or ``None``.

    A miss reads ``None`` — the consumer holds UNKNOWN/SUSPECT, never clean —
    so a TUI rewording or a new long-poll shape cannot silently pass as idle.
    The match is anchored to command shape: a bare ``sleep`` or ``waiting on``
    in prose does not excuse a wait, and neither does prose that merely
    starts with a command word.
    """
    normalized = normalize_frame(text)
    for line in _command_lines(normalized):
        for segment in _SHELL_SPLIT_RE.split(line):
            shape = _segment_shape(segment.strip())
            if shape is not None:
                return shape
    if _WAIT_STATUS_RE.search(normalized):
        return "Waiting for CI poll result"
    return None


def ci_run_id(text: str) -> str | None:
    """The CI run id a poll command names in pane text, or ``None``.

    The id is read from the same command text the wait spelling is read
    from, so a run id quoted in prose or printed by a tool is never paired
    with a wait the pane is not actually running.
    """
    for line in _command_lines(normalize_frame(text)):
        match = _CI_RUN_RE.search(line)
        if match is not None:
            return match.group(1)
    return None


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
