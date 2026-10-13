"""systemd unit property text: the approved adapter for a unit's declaration.

Interpreting the property text ``systemctl show`` prints — which paths a unit's
own ``ExecStart``, ``WorkingDirectory`` and ``Environment`` declare — is an
interpretation of external text, and ``discovery`` carries frozen legacy parsing
debt whose budget must not widen to pass a change. Decoding therefore lives here
as a boundary; the caller acts on the decoded paths and never on the text.
"""
from __future__ import annotations

import re

# A path begins at a separator: whitespace, systemd's list punctuation, an
# ``=`` in a KEY=value pair, or the ``:`` separating several paths one value
# names. It ends at the same punctuation, and a slash that follows a filename
# character, a dot or another slash is a relative path's own separator, not the
# start of an absolute one.
ABSOLUTE_PATH = re.compile(r"(?<![\w./-])/[^\s;:,\"'()]+")


def declared_paths(text: str) -> list[str]:
    """Every absolute path the property text names, in order, without repeats.

    systemd renders ExecStart as ``{ path=/usr/bin/x ; argv[]=/usr/bin/x a b }``,
    WorkingDirectory as a single path, and Environment as space-separated
    ``KEY=value`` pairs whose values can themselves name several
    colon-separated paths. All three are the unit's own declaration of where it
    runs, so the paths are collected from the whole text rather than from one
    property, and a caller asking "does this unit declare a site?" reads one
    list instead of re-parsing three renderings.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for match in ABSOLUTE_PATH.finditer(text):
        # A trailing quote or period is punctuation from the rendering, not part
        # of the path.
        path = match.group(0).rstrip(".,")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths

SITE_UNIT_PREFIX = "mishe-"
"""Shared prefix of every unit this plant installs, across every site session.

A site's manifest names its own session's units, so the prefix is what makes a
second site's services visible to a scan of this one.
"""


def is_site_unit(unit_name: str) -> bool:
    """Whether a unit name belongs to this plant (starts with the site prefix)."""
    return unit_name.startswith(SITE_UNIT_PREFIX)
