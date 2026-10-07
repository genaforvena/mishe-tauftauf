import hashlib
import re

import pytest
from wcwidth import wcswidth

from mishe_tauftauf.viewport import project
from mishe_tauftauf.tmux import lease_value


FOOTER = (
    "-- wake: pending=42 yield=40 clear=40 --\n"
    "-- pane live 2026-10-07T10:30:00Z · refresh 5s · ticks every frame --\n"
)
BODY = (
    "STATE: RED — source=Note3; original=2026-10-07T10:29:05Z; receipt=2026-10-07T10:29:25Z\n"
    "LIGHT: 16.4952 lux; freshness=conditional; uncertainty=clocks unsynchronized\n"
    "SERVICE: UNKNOWN — controller rejected opcode=0x2008 parameters=" + "0123456789" * 25 + " failed\n"
    "UNICODE: 界面 e\u0301 👩\u200d💻; value=unchanged  trailing  spaces  \n"
)


@pytest.mark.parametrize("columns,lines", [(137, 20), (43, 20), (80, 9)])
def test_continuations_preserve_values_order_and_separate_chrome(columns, lines):
    first = project(BODY, FOOTER, "health", columns, lines, 0)
    count = int(re.search(r"VIEW 1/(\d+)", first)[1])
    recovered = []
    for tick in range(count):
        page = project(BODY, FOOTER, "health", columns, lines, tick)
        # A continuation can wrap its instructions; facts start after that command.
        prefix, content = page.split("dashboard\r\n", 1)
        assert f"frame={hashlib.sha256(BODY.encode()).hexdigest()[:12]}" in prefix.replace("\r\n", "")
        facts, footer = content.split("-- wake:", 1)
        recovered.extend(facts.split("\r\n"))
        assert "-- wake:" + footer == FOOTER.rstrip("\n").replace("\n", "\r\n")
        physical = prefix.split("\r\n") + facts.split("\r\n")[:-1]
        physical_count = len(physical) + sum((wcswidth(row) + columns - 1) // columns for row in FOOTER.splitlines())
        assert physical_count == lines
        assert all(wcswidth(row) <= columns for row in physical)
    assert "".join(recovered) == BODY.replace("\n", "")
    assert project(BODY, FOOTER, "health", columns, lines, count) == first


def test_small_or_missing_geometry_is_unknown_without_a_live_lease():
    for columns, lines in [(43, 3), (1, 1), (0, 20), (80, 0)]:
        page = project(BODY, FOOTER, "health", columns, lines, 0)
        assert page.replace("\r\n", "").startswith("UNKNOWN" if columns != 1 else "U")
        assert lease_value(page) is None
        assert "STATE: RED" not in page


def test_controls_cannot_erase_failure_and_changed_suffix_changes_frame_identity():
    body = "STATE: UNKNOWN\x1b[2J\tbad\rvalue\n"
    page = project(body, FOOTER, "health", 137, 20, 0)
    assert "STATE: UNKNOWN\\x1b[2J\\tbad\\rvalue" in page
    assert "\x1b" not in page
    changed = project(body + " not GREEN\n", FOOTER, "health", 137, 20, 0)
    assert page.split("\r\n", 1)[0] != changed.split("\r\n", 1)[0]
    assert lease_value(FOOTER) is not None
    assert lease_value(FOOTER.rstrip("\n") + " corrupt\n") is None
