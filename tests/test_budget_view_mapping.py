"""checks/budget-view-mapping.py judges the window's ENGINE columns against their own clocks.

``next`` is the file's own ``routing.selected`` (written every 600 s) and must be rendered
faithfully and be consistent with the file's own trail. ``running`` is live on both sides.
The old check compared ``next`` against a fresh ``assess()``, so a wake that started or
ended between the write and the check turned a correct window into a FAIL.
"""

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("budget_view_mapping", REPO / "checks" / "budget-view-mapping.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _trail(*steps):
    return [dict(key=key, verdict=verdict) for key, verdict in steps]


def _published(**roles):
    return {role: {"selected": selected, "decision": "select" if selected else "none", "trail": trail}
            for role, (selected, trail) in roles.items()}


CAP_TRAIL = _trail(("deepseek", "at-capacity"), ("hy4", "eligible"))
PLAIN_TRAIL = _trail(("deepseek", "eligible"))


def test_capacity_selection_written_earlier_still_holds_after_the_wakes_end():
    """The regression: publish chose hy4 because deepseek was full; the wakes are gone now."""
    published = _published(genome=("hy4", CAP_TRAIL))
    view = {"genome": {"running": None, "next": "hy4"}}
    assert mod.disagreements(view, published, {}) == []


def test_a_view_that_shows_the_first_candidate_is_a_failure():
    published = _published(genome=("hy4", CAP_TRAIL))
    view = {"genome": {"running": None, "next": "deepseek"}}
    bad = mod.disagreements(view, published, {})
    assert len(bad) == 1 and "routing.selected=hy4" in bad[0]


def test_a_selection_the_file_s_own_trail_does_not_choose_is_a_failure():
    published = _published(genome=("hy4", PLAIN_TRAIL))
    view = {"genome": {"running": None, "next": "hy4"}}
    bad = mod.disagreements(view, published, {})
    assert len(bad) == 1 and "first eligible candidate is deepseek" in bad[0]


def test_running_that_contradicts_the_ledger_is_a_failure():
    published = _published(genome=("deepseek", PLAIN_TRAIL))
    view = {"genome": {"running": None, "next": "deepseek"}}
    bad = mod.disagreements(view, published, {"genome": "deepseek"})
    assert len(bad) == 1 and "ledger running=deepseek" in bad[0]


def test_a_role_absent_from_the_file_is_a_failure():
    view = {"ghost": {"running": None, "next": "deepseek"}}
    bad = mod.disagreements(view, _published(genome=("deepseek", PLAIN_TRAIL)), {})
    assert len(bad) == 1 and "does not carry" in bad[0]


def test_an_idle_role_is_consistent_with_an_empty_trail():
    published = _published(docs=(None, _trail(("deepseek", "cooling"))))
    assert mod.disagreements({"docs": {"running": None, "next": None}}, published, {}) == []


STUB_VIEW = '''
import json
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]


def load_budget():
    return json.loads((HOME / "engine" / "budget.json").read_text()), None


def engine_view(d):
    rows = json.loads((HOME / "engine" / "rendered.json").read_text())
    return {role: dict(row) for role, row in rows.items()}
'''


def _site(tmp_path, view_rows):
    home = tmp_path / "site"
    (home / "engine").mkdir(parents=True)
    (home / "operator" / "budget").mkdir(parents=True)
    (home / "operator" / "budget" / "budget_view.py").write_text(STUB_VIEW)
    budget = {"schema": 1, "produced_at": 1794880000.0,
              "routing": {"roles": {"genome": {"selected": "hy4", "decision": "select", "trail": CAP_TRAIL}}}}
    (home / "engine" / "budget.json").write_text(json.dumps(budget))
    (home / "engine" / "rendered.json").write_text(json.dumps(view_rows))
    return home


def _ledger(home, events):
    (home / "engine" / "routing-ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_main_holds_when_the_window_renders_the_file(tmp_path):
    home = _site(tmp_path, {"genome": {"running": None, "next": "hy4"}})
    _ledger(home, [{"event": "select", "role": "genome", "key": "hy4"},
                   {"event": "outcome", "role": "genome", "key": "hy4"}])
    assert mod.main(["budget-view-mapping.py", str(home)]) == 0


def test_main_fails_when_the_window_shows_a_different_engine(tmp_path):
    home = _site(tmp_path, {"genome": {"running": None, "next": "deepseek"}})
    _ledger(home, [])
    assert mod.main(["budget-view-mapping.py", str(home)]) == 2


def test_main_fails_when_the_window_hides_a_running_wake(tmp_path):
    home = _site(tmp_path, {"genome": {"running": None, "next": "hy4"}})
    _ledger(home, [{"event": "select", "role": "genome", "key": "hy4"}])
    assert mod.main(["budget-view-mapping.py", str(home)]) == 2


def test_main_cannot_decide_without_the_window_module(tmp_path):
    home = _site(tmp_path, {})
    (home / "operator" / "budget" / "budget_view.py").unlink()
    assert mod.main(["budget-view-mapping.py", str(home)]) == 3
