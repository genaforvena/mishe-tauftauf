"""Drill for checks/inkling-upstream-timeout.py: an upstream timeout in the proxy journal goes RED.

Replays lines copied from the 2026-11-27 proxy journal, so no live unit is touched.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "checks" / "inkling-upstream-timeout.py"

TIMEOUT_1824 = "Nov 27 18:24:35 mesh-home python3[230259]: TimeoutError: timed out"
TIMEOUT_1824B = "Nov 27 18:24:50 mesh-home python3[230259]: TimeoutError: timed out"
ROUTINE = "Nov 27 19:19:27 mesh-home python3[230300]: POST /v1/chat/completions 200"


def _load():
    spec = importlib.util.spec_from_file_location("inkling_upstream_timeout", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timeout_in_window_is_red():
    check = _load()
    code, report = check.evaluate([ROUTINE, TIMEOUT_1824, TIMEOUT_1824B], "inkling-proxy.service",
                                  "TimeoutError", 900)
    assert code == 2
    assert "2 upstream TimeoutError line(s)" in report[0]
    assert "18:24:50" in report[1]


def test_quiet_window_is_green():
    check = _load()
    code, report = check.evaluate([ROUTINE], "inkling-proxy.service", "TimeoutError", 900)
    assert code == 0
    assert "GREEN" in report[0]


def test_unreadable_journal_is_unknown():
    check = _load()

    def broken(unit, since):
        raise RuntimeError("journalctl for inkling-proxy.service failed: no bus")

    assert check.main(["--window", "900"], read=broken, now=lambda: 0.0) == 3


def test_main_returns_red_from_injected_journal():
    check = _load()
    assert check.main([], read=lambda unit, since: [TIMEOUT_1824], now=lambda: 0.0) == 2
