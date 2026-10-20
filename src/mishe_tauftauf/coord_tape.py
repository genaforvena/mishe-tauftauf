"""Decode the chat tape's own sample lines for the coordination senses.

The coordination fog sense publishes the paths it finds in its sample, so the
tape carries its own publications. Reading the tape as a record of what has
been claimed would count those publications as claims of their own paths: the
next evaluation would find every fog path already named in the tape while
the bytes are still unclaimed. The tape's own line format is a dialect, so
the filter that excludes those lines lives here.
"""

import re

OWN_SAMPLE_RE = re.compile(r"dirty=\d+ fog=\d+")
"""The fog sense's own sample shape: the tier counts it publishes."""


def claim_source(tape: str) -> str:
    """The tape without the fog sense's own published sample lines.

    Lines that carry the sense's own shape are dropped; every other line is
    kept verbatim, so a claim made by anyone else still reads as a claim.
    """
    return "\n".join(line for line in tape.splitlines()
                     if not OWN_SAMPLE_RE.search(line))
