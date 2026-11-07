"""Household validation: labels are scored only when explicit rules decide them; blind spans and gaps are UNKNOWN."""
import calendar
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("household_validate", ROOT / "tools/household_validate.py")
hv = importlib.util.module_from_spec(spec)
sys.modules["household_validate"] = hv
spec.loader.exec_module(hv)


def _ep(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def test_truth_rules_decide_only_clear_labels():
    assert hv.truth("ilya=living_room_couch\trozalia=bedroom_bed") is True
    assert hv.truth("rozalia=living_room_beanbag ilya=bedroom_at_mac") is True
    assert hv.truth("living_room=empty_of_humans bedroom=ilya+rozalia") is False
    assert hv.truth("ilya=outdoors_walk_with_redmi rozalia=bedroom_in_bed") is False
    assert hv.truth("ilya=moving_to_living_room_from_bedroom rozalia=bedroom_bed") is None
    assert hv.truth("ilya=home_back_from_walk redmi10=with_ilya") is None


def test_parse_label_splits_time_and_fields():
    stamp, text = hv.parse_label("2026-11-07T10:05:15Z\tilya=outdoors\trozalia=bedroom_in_bed\n")
    assert stamp == "2026-11-07T10:05:15Z"
    assert "rozalia=bedroom_in_bed" in text
    assert hv.parse_label("no time here") is None


def test_stated_blind_span_excludes_earlier_labels_only():
    blind = [{"room": "living room", "from_utc": None, "to_utc": "09:34Z"}]
    assert hv.blind_end(blind, _ep("2026-11-07T09:05:04Z")) == "09:34Z"
    assert hv.blind_end(blind, _ep("2026-11-07T10:05:15Z")) is None
    assert hv.blind_end([{"room": "bedroom", "to_utc": "09:34Z"}], _ep("2026-11-07T09:05:04Z")) is None


def test_score_counts_hits_and_misses_and_keeps_unscored_visible():
    t0 = _ep("2026-11-06T22:00:00Z")
    # Activity is high (occupied) before 22:30 and low (empty) after; luma flat so no lamp step.
    recs = [{"t": t0 + i * 60.0, "luma": 100.0,
             "activity": 5.0 if i < 30 else 0.5} for i in range(60)]
    labels = [
        ("2026-11-06T22:10:00Z", "ilya=living_room_couch rozalia=bedroom_bed"),   # occupied, high: hit
        ("2026-11-06T22:50:00Z", "living_room=empty_of_humans"),                  # empty, low: hit
        ("2026-11-06T22:50:30Z", "ilya=moving_to_living_room_from_bedroom"),      # undecided: unscored
        ("2026-11-06T23:50:00Z", "living_room=empty_of_humans"),                  # no data nearby: unscored
    ]
    result = hv.score(labels, recs, [], hv.ha.QUIET)
    assert result["scored"] == 2
    assert result["hits"] == 2
    assert result["misses"] == []
    reasons = sorted(row["reason"] for row in result["unscored"])
    assert len(reasons) == 2
    assert any("rules do not decide" in r for r in reasons)
    assert any("no usable camera reading" in r for r in reasons)
