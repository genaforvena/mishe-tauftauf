"""tools/wake_view.py: wake durations are measured or UNKNOWN, never invented.

A view that reports zero for a torn ledger would understate wake time exactly
where delivery_view once hid quiet fences behind an exact-zero test. These
tests pin pairing, the unpaired UNKNOWN, class alignment with wake-waste and
the nearest-rank summary.
"""
import calendar
import importlib.util
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wake_view", REPO / "tools" / "wake_view.py")
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)

NOW = calendar.timegm(time.strptime("2026-11-20T03:42:00Z", "%Y-%m-%dT%H:%M:%SZ"))


def ev(event, at, wake="w1", outcome="", reason="", role="docs", key="k1"):
    return {"event": event, "at": at, "wake": wake, "outcome": outcome,
            "reason": reason, "role": role, "key": key}


def test_duration_uses_the_first_select_on_a_fallthrough():
    events = [ev("select", NOW - 600), ev("select", NOW - 300), ev("outcome", NOW, outcome="yielded")]
    s = view.evaluate(events, NOW, 86400)
    assert s["outcomes"] == 1
    assert s["durations"]["n"] == 1
    assert s["durations"]["mean"] == 600.0
    assert s["unpaired"] == 0


def test_an_unpaired_outcome_is_unknown_not_a_zero_duration():
    events = [ev("outcome", NOW - 60, outcome="timeout", reason="exceeded 2100s (hard cap)")]
    s = view.evaluate(events, NOW, 86400)
    assert s["outcomes"] == 1
    assert s["durations"] == {}
    assert s["unpaired"] == 1
    text = view.render(Path("/nope"), 86400, NOW, events, s)
    assert "durations measured 0, UNKNOWN 1" in text
    assert "wake duration  UNKNOWN" in text


def test_classes_align_with_wake_waste():
    assert view.classify("yielded", "") == "yielded"
    assert view.classify("timeout", "exceeded 2100s (hard cap)") == "hard cap"
    assert view.classify("timeout", "exceeded 1800s (no output for 180s)") == "no output"
    assert view.classify("timeout", "silent 600s before the 1800s deadline") == "timeout"
    assert view.classify("no yield (exit 1)", "") == "no yield"


def test_hard_cap_rate_is_reported_by_class_and_role():
    events = [
        ev("select", NOW - 2100, "h"), ev("outcome", NOW, "h", "timeout", "exceeded 2100s (hard cap)", "pub"),
        ev("select", NOW - 120, "y"), ev("outcome", NOW, "y", "yielded", "", "genome"),
    ]
    s = view.evaluate(events, NOW, 86400)
    assert s["by_class"]["hard cap"]["n"] == 1
    assert s["by_class"]["yielded"]["n"] == 1
    assert s["by_role"]["pub"]["mean"] == 2100.0
    text = view.render(Path("/nope"), 86400, NOW, events, s)
    assert "hard cap" in text and "pub" in text


def test_old_outcomes_are_outside_the_window_but_their_later_select_is_not():
    events = [ev("select", NOW - 7200), ev("outcome", NOW - 7100, outcome="yielded")]
    assert view.evaluate(events, NOW, 3600)["outcomes"] == 0
    # A wake that started before the window but finished inside still measures.
    inside = [ev("select", NOW - 7200), ev("outcome", NOW - 10, outcome="yielded")]
    s = view.evaluate(inside, NOW, 3600)
    assert s["durations"]["mean"] == 7190.0


def test_a_select_with_no_outcome_is_in_flight_not_finished():
    events = [ev("select", NOW - 60), ev("outcome", NOW - 120, "old", "yielded")]
    s = view.evaluate(events, NOW, 86400)
    assert s["outcomes"] == 1            # only the old wake finished
    assert s["in_flight"] == 1
    assert "in flight 1" in view.render(Path("/nope"), 86400, NOW, events, s)


def test_summary_is_nearest_rank_and_empty_without_samples():
    assert view.summarize([]) == {}
    s = view.summarize([float(i * 10) for i in range(1, 11)])
    assert s["n"] == 10
    assert s["p50"] == 50.0              # ceil(10*0.5)=5th row
    assert s["p90"] == 90.0              # ceil(10*0.9)=9th row, not interpolated
    assert s["max"] == 100.0


def test_missing_or_empty_ledger_reports_unknown(tmp_path):
    assert "UNKNOWN" in view.render(tmp_path, 86400, NOW)
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "routing-ledger.jsonl").write_text("{torn\n")
    assert "UNKNOWN" in view.render(tmp_path, 86400, NOW)


def test_a_real_ledger_line_decodes_and_renders(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW))
    lines = [
        {"at": at.replace("03:42", "03:31"), "event": "select", "wake": "w1",
         "role": "genome", "key": "omp:opencode-go/hy4-flash-free:-"},
        {"at": at, "event": "outcome", "wake": "w1", "outcome": "yielded",
         "reason": "", "role": "genome", "key": "omp:opencode-go/hy4-flash-free:-"},
    ]
    (engine / "routing-ledger.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    text = view.render(tmp_path, 86400, NOW)
    assert "finished wakes: 1" in text
    assert "wake duration  mean 11m00s" in text


def test_longest_finished_wakes_are_sorted_by_duration_not_recency():
    events = [
        ev("select", NOW - 600, "long"), ev("outcome", NOW - 400, "long", "yielded"),
        ev("select", NOW - 200, "short"), ev("outcome", NOW - 100, "short", "yielded"),
    ]
    text = view.render(Path("/nope"), 86400, NOW, events, view.evaluate(events, NOW, 86400))
    line = next(ln for ln in text.splitlines() if "longest finished wakes" in ln)
    assert line.index("3m20s") < line.index("1m40s")
