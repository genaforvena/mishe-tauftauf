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


# --- WAKES: one line per declared mind, never cut by the window fit -------------------------
import json
import sys
import time

sys.path.insert(0, str(REPO / "tools"))
import tend_wakes  # noqa: E402

HEAD = "# Mishe tending pass 2026-11-11 10:00:00Z  (host up 1h)\n\n"


def _site(tmp_path, names, dispatched=()):
    (tmp_path / "charters").mkdir()
    for name in names:
        (tmp_path / "charters" / f"{name}.md").write_text("charter\n")
    (tmp_path / "dispatch").mkdir()
    (tmp_path / "wakes").mkdir()
    for name in dispatched:
        (tmp_path / "dispatch" / f"{name}.json").write_text(json.dumps({"state": "idle"}))
    return tmp_path


def _rendered(monkeypatch, home, now):
    text = HEAD + "## wakes\n" + "\n".join(line for _, line, _ in tend_wakes.wakes(home, now)) + "\n\n## verdict\nOK\n"
    monkeypatch.setattr(view, "run_tend", lambda: (text, None))
    return view.render(now)


def _wakes_block(lines):
    start = lines.index("WAKES") + 1
    end = next((i for i in range(start, len(lines)) if lines[i] == ""), len(lines))
    return lines[start:end]


def test_every_declared_role_is_in_rendered_wakes(monkeypatch, tmp_path):
    names = ["docs", "genome", "health", "network", "operator", "pub", "rust-plan", "senses", "steward", "field-review"]
    home = _site(tmp_path, names, dispatched=["genome", "health"])
    block = _wakes_block(_rendered(monkeypatch, home, time.time()))
    for name in names:
        assert any(line.strip().startswith(name + " ") for line in block), f"{name} missing from WAKES"
    undispatched = [l for l in block if l.strip().startswith(("docs ", "pub "))]
    assert all("UNKNOWN" in l and "no dispatch record" in l for l in undispatched)


def test_wakes_are_not_pushed_out_by_other_sections(monkeypatch, tmp_path):
    names = [f"mind-{i:02d}" for i in range(30)]
    home = _site(tmp_path, names)
    text = HEAD + "## verdict\nNEEDS ATTENTION:\n- a\n- b\n- c\n- d\n- e\n"
    text += "## units\n" + "\n".join(f"unit {i}" for i in range(9)) + "\n"
    text += "## signals\nGREEN: 85 of 85\n"
    text += "## wakes\n" + "\n".join(line for _, line, _ in tend_wakes.wakes(home, time.time())) + "\n"
    monkeypatch.setattr(view, "run_tend", lambda: (text, None))
    lines = view.render()
    block = _wakes_block(lines)
    assert len(block) == 30
    assert all(name in " ".join(block) for name in names)
    assert len(lines) <= view.WINDOW_ROWS     # the other sections give way to fit, WAKES does not
    assert "UNITS" not in lines or len([l for l in lines if l.startswith("  unit ")]) < 9
