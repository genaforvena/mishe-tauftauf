"""The health window's reading of the tending pass: UNKNOWN and RED must never look calm."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("health_view_under_test", REPO / "tools" / "health_view.py")
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)

PASS = """# Mishe tending pass 2026-11-11 10:00:00Z  (host up 1h)

## signals
RED (2): budget (5min), budget-claude (5min)
UNKNOWN (8): a, b
GREEN: 74 of 85

## verdict
NEEDS ATTENTION:
- 1 start-limit refusals in the last hour
"""

CALM = """# Mishe tending pass x

## signals
GREEN: 85 of 85

## verdict
OK
"""


def test_red_signal_is_red_even_when_pass_says_attention():
    head, secs = view.sections(PASS)
    v, why = view.verdict_of(secs)
    assert v == "RED"
    assert "UNKNOWN" in why and "not GREEN" in why


def test_unknown_signals_never_make_an_ok_verdict_silent():
    text = CALM.replace("GREEN: 85 of 85", "UNKNOWN (3): a, b, c\nGREEN: 82 of 85")
    _, secs = view.sections(text)
    v, why = view.verdict_of(secs)
    assert v == "OK" and "3 signal(s) UNKNOWN" in why


def test_missing_pass_renders_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(view, "TEND", tmp_path / "absent.py")
    monkeypatch.setitem(view._cache, "text", None)
    monkeypatch.setitem(view._cache, "at", 0.0)
    lines = view.render()
    assert lines[2] == "VERDICT: UNKNOWN"
    assert all(len(l) <= view.WIDTH for l in lines)


def test_pass_exit_one_with_report_is_a_result(monkeypatch, tmp_path):
    script = tmp_path / "tend.py"
    script.write_text("import sys\nprint(%r)\nsys.exit(1)\n" % PASS)
    monkeypatch.setattr(view, "TEND", script)
    monkeypatch.setitem(view._cache, "text", None)
    monkeypatch.setitem(view._cache, "at", 0.0)
    text, error = view.run_tend()
    assert error is None and text.startswith("# Mishe tending pass")
