"""Decode the text the patch CLI and the process reader receive.

The patch gate takes its check, activate, observe and revert commands as JSON
on the command line. Decoding that text is an adapter job, kept here so the
gate itself reads argument lists, not JSON text.

The process reader's ``ps -o etimes=,args=`` rows and the argv shapes that mean
"this wake is waiting on CI or an external state" are read here too, so
discovery reads parsed fields rather than matching text itself.
"""

import json
import re

WAIT_ARGV_PATTERNS = (
    re.compile(r"(?:^|\s|/)gh\s+run\s+watch\b"),
    re.compile(r"(?:^|\s|/)gh\s+pr\s+checks\b.*--watch\b"),
    re.compile(r"(?:^|[\s/])sleep\s+\d+(?:\.\d+)?(?:\s|$)"),
)
"""argv shapes that mean a wake is waiting on CI or an external state."""


def decode_argv(text: str | None):
    """The argument list in ``text``, or None when no argument was given."""
    return json.loads(text) if text else None


def is_wait_argv(args: str) -> bool:
    """True when process argv text is a CI or external-state wait shape."""
    return any(pattern.search(args) for pattern in WAIT_ARGV_PATTERNS)


def parse_ps_row(text: str) -> tuple[int, str] | None:
    """(elapsed seconds, argv text) from one ``ps -o etimes=,args=`` row."""
    line = text.strip()
    if not line:
        return None
    etimes_text, _, args = line.partition(" ")
    if not etimes_text.isdigit():
        return None
    return int(etimes_text), args.strip()
