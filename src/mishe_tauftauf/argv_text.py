"""Decode the JSON argument lists the patch CLI receives.

The patch gate takes its check, activate, observe and revert commands as JSON
on the command line. Decoding that text is an adapter job, kept here so the
gate itself reads argument lists, not JSON text.
"""

import json


def decode_argv(text: str | None):
    """The argument list in ``text``, or None when no argument was given."""
    return json.loads(text) if text else None
