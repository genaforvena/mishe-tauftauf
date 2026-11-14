from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from mishe_tauftauf.units import _env


def test_unit_path_puts_the_site_bin_first() -> None:
    # The `mishe` wrapper lives in the site bin; a wake whose PATH omits it
    # cannot run bare `mishe yield` and ends without a yield.
    cfg = SimpleNamespace(home=Path("/srv/site/.mishe-x"), repo=Path("/srv/site"),
                          pythonpath="/srv/site/.mishe-x/runtime/current/src")
    path_line = next(line for line in _env(cfg).splitlines() if line.startswith("Environment=PATH="))
    entries = path_line[len("Environment=PATH="):].split(":")
    assert entries[0] == "/srv/site/.mishe-x/bin"
    assert "/usr/bin" in entries and "/bin" in entries
