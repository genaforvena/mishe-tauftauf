"""Decode a pane's own omp model from its process command line.

Four external spellings meet here, so consumers act on decoded values:

* ``/proc/<pid>/cmdline`` is NUL-separated bytes, an external kernel dialect.
  The fields are decoded once, here, and an unreadable or empty file reads
  ``None`` rather than an empty success.
* A launcher's own bytes are another dialect: the shell that runs it accepts
  ``--model value`` and ``--model=value`` as the same option, so a reader that
  knows only one of the two spellings reports a model that is plainly named as
  absent. ``launcher_model`` reads the script the way the shell words it.
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
import shlex

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


_MODEL_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:@+"
)
"""The characters a model name is written with; shell and source punctuation is not one."""


def _logical_lines(text: str) -> list[str] | None:
    """The script's logical lines, or ``None`` when a line is not one.

    A backslash at the very end of a physical line continues it, and the shell
    deletes the pair: nothing is put in its place, so a word split across the
    pair is one word. A backslash followed by a space escapes the space
    instead, and the line ends there, so only an immediate backslash-newline
    pair continues.
    """
    lines = []
    buffer = ""
    for line in text.splitlines():
        if line.endswith("\\"):
            buffer += line[:-1]
            continue
        lines.append(buffer + line)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


_SHELL_PUNCTUATION = ";&|<>(){}`"
"""The shell's own operators and grouping characters this reader does not model.

A pipeline, an AND-list, a redirection, a here-document, a subshell or a
group all change *what the shell runs*, so a reader that does not model them
cannot say which command the pane is running. It refuses them: the reading is
unknown, never a guess. A token that carries one of these characters is
refused whatever else it carries, so a quoted operator is refused too.
"""

_SHELL_EXPANSIONS = "$*?"
"""The characters the shell expands before it runs anything.

A parameter expansion can name another program, or inject a flag the reader
would not have seen, so a word that carries one of these is refused before
any fact is taken from the command.
"""

_UNSUPPORTED_WORDS = frozenset({
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "select", "function", "time", "coproc", "command",
    "nohup", "setsid",
})
"""The shell keywords and wrappers this reader does not model.

``command``, ``nohup`` and ``setsid`` each have their own option grammar, and
an option this reader mis-reads would make it name a program the shell never
runs. They are refused rather than half-modelled.
"""

_CONTROL_BUILTINS = frozenset({
    "exit", "return", "break", "continue", "set", "trap", "eval", "source",
    ".", "wait",
})
"""The shell builtins that can stop the launcher before it reaches its program.

``exit`` ends the script, ``set -e`` ends it on the first failure, and
``source`` runs a file whose bytes this reader has not read. None of them is a
program launch, so a script that contains one settles nothing: it is refused.
"""


def _words(line: str) -> list[str]:
    """The words of a line, the way the shell words it.

    ``#`` is left in the stream because the shell reads a ``#`` that begins a
    word as a comment and one inside a word as an ordinary character.
    """
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _launcher_commands(text: str) -> list[list[str]] | None:
    """The commands of a launcher script, or ``None`` when the script is more.

    A whole-line comment is skipped. Any operator, grouping character,
    keyword, expansion or ``#`` this reader cannot place refuses the whole
    script, because the reader would not know which command the pane is
    running. A line the shell cannot word refuses it too: the shell rejects
    such a script, so a later line of it never runs.
    """
    commands = []
    for logical in _logical_lines(text) or []:
        if not logical.strip() or logical.lstrip().startswith("#"):
            continue
        try:
            tokens = _words(logical)
        except ValueError:
            return None
        if not tokens:
            continue
        for token in tokens:
            if any(character in _SHELL_PUNCTUATION for character in token):
                return None
            if any(character in _SHELL_EXPANSIONS for character in token):
                return None
            if token in _UNSUPPORTED_WORDS or token in _CONTROL_BUILTINS:
                return None
            if token.startswith("#"):
                return None
        commands.append(tokens)
    return commands


_ENV_FLAGS = frozenset({"-i", "-0", "--ignore-environment", "--debug"})
"""The ``env`` options that are flags and take no word."""

_ENV_OPTIONS_WITH_OPERAND = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
"""The ``env`` options that take a word after them."""

_ASSIGNMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""The shell's own rule for the name of an assignment: ASCII letters, digits and underscore."""

_PYTHON_FLAGS = frozenset({
    "-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-q", "-I", "-P", "-v", "-x",
})
"""The python interpreter options that take no argument."""


def _is_assignment(token: str) -> bool:
    """Whether a word is an environment assignment, which the shell runs nothing.

    The name follows the shell's own rule: a unicode letter is not an
    assignment name to the shell, so ``λ=x`` is a command name, not an
    assignment.
    """
    name, separator, _ = token.partition("=")
    if not separator or token.startswith("-"):
        return False
    return _ASSIGNMENT_NAME.fullmatch(name) is not None


def _skip_env_options(tokens: list[str], index: int) -> int | None:
    """The index of the program ``env`` runs, or ``None`` for an option it does not know.

    An option ``env`` rejects means no program runs at all, so an option this
    reader does not model refuses the reading rather than being stepped over.
    A flag that takes no word refuses a word attached to it, because ``env``
    rejects that too.
    """
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if token in _ENV_FLAGS:
            index += 1
            continue
        if token in _ENV_OPTIONS_WITH_OPERAND:
            index += 2
            continue
        if token.startswith("--"):
            name, separator, _ = token.partition("=")
            if name in _ENV_FLAGS:
                if separator:
                    return None
                index += 1
                continue
            if name in _ENV_OPTIONS_WITH_OPERAND:
                index += 1 if separator else 2
                continue
            return None
        if token.startswith("-") and token != "-":
            if any(token.startswith(flag) and len(token) > len(flag)
                   for flag in _ENV_OPTIONS_WITH_OPERAND):
                index += 1
                continue
            return None
        break
    return index


def _program_and_exec(tokens: list[str]) -> tuple[str | None, bool, int]:
    """The program a command runs, whether it replaces the shell, and where.

    The program is the word the shell runs, in the position it runs it: the
    shell's own prefix assignments, then at most one ``exec``, then the word
    itself. ``exec`` is a builtin that takes the command as its operand, so a
    second ``exec`` after it is a program name and not another builtin. ``env``
    is an external program, so the word after it is the program it runs and not
    another wrapper. A word that names a path is an argument, not a program --
    ``printf %s /home/user/bin/omp --model=...`` prints an omp path and runs no
    omp -- and a reader that takes the model from it records a model no
    process ever ran on.
    """
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    uses_exec = False
    if index < len(tokens) and tokens[index] == "exec":
        uses_exec = True
        index += 1
    if index < len(tokens) and tokens[index] == "env":
        index = _skip_env_options(tokens, index + 1)
        if index is None:
            return None, uses_exec, -1
        while index < len(tokens) and _is_assignment(tokens[index]):
            index += 1
        if index >= len(tokens):
            return None, uses_exec, -1
        return tokens[index], uses_exec, index
    if index < len(tokens):
        return tokens[index], uses_exec, index
    return None, uses_exec, -1


def _runs_the_pane(program: str | None, command: list[str], index: int) -> bool:
    """Whether a command runs the program the pane is.

    omp is the pane's own program. A launcher that reaches it through a
    wrapper -- the plant's own ``exec /usr/bin/python3 .../longcat-launch.py
    --model=...`` -- names a python interpreter running a launcher script, and
    the model it selects is the model's the pane runs on. Any other program is
    not the pane: ``/bin/echo --model=...`` prints a model and runs no pane,
    and an arbitrary python script is not the plant's launcher either.
    """
    if program is None:
        return False
    if program == "omp" or program.endswith("/omp"):
        return True
    if not program.startswith("/"):
        return False
    if not program.rsplit("/", 1)[-1].startswith("python"):
        return False
    following = index + 1
    while following < len(command) and command[following] in _PYTHON_FLAGS:
        following += 1
    script = command[following] if following < len(command) else ""
    return script.endswith("-launch.py")


def launcher_model(text: str) -> str | None:
    """The ``--model`` a launcher script selects, or ``None``.

    A launcher is a shell script, so the flag arrives in the shell's own two
    spellings -- ``--model value`` and ``--model=value`` -- and a reader that
    knows only one of them reports a model the bytes plainly name as absent.

    This reader recognizes the launcher's shape and refuses the rest. It reads
    a script of plain commands -- comments, environment assignments, the
    shell's own ``exec``, ``env`` with the options it knows, one program and
    its flags -- and it reads the model from the command that runs the pane's
    program. A command that ``exec``s replaces the shell, so it decides and
    the commands after it never run. A script that launches the pane's program
    twice without an ``exec`` settles nothing, so it reads no model.

    Any construct this reader does not model -- a pipeline, an AND-list, a
    redirection, a here-document, a subshell, a group, a conditional, a
    keyword, an expansion, a quoted ``#``, an option the reader does not know
    -- makes the whole reading unknown. The contract is deliberately narrow: a
    wrong model name is worse than an unknown one, because the plant records
    it as the fact about what the mind runs on.

    The value rules of :func:`model` apply unchanged -- an empty value, or
    another option in the value's place, is no model -- and a value that is
    not written like a model name reads ``None`` as well: a launcher written
    in a language the shell only quotes, ``"--model=vendor/model",``, yields a
    value carrying the source's own punctuation, and a record that names
    ``vendor/model,`` as the model is a wrong fact wearing a model's name.
    """
    commands = _launcher_commands(text)
    if commands is None:
        return None
    candidates = []
    for command in commands:
        program, uses_exec, index = _program_and_exec(command)
        if not _runs_the_pane(program, command, index):
            continue
        value = model(command[index:])
        usable = value if value is not None and set(value) <= _MODEL_NAME_CHARS else None
        if uses_exec:
            return usable
        candidates.append(usable)
    if len(candidates) == 1:
        return candidates[0]
    return None

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
