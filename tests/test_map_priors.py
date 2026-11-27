"""Map priors learner: unavailable camera hours are not evidence; adoption needs a held-out win."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("map_priors", ROOT / "tools/map_priors.py")
mp = importlib.util.module_from_spec(spec)
sys.modules["map_priors"] = mp
spec.loader.exec_module(mp)

UTC = datetime.timezone.utc


def _site(tmp_path, rows):
    site = tmp_path / "site"
    feat = site / "features"
    feat.mkdir(parents=True)
    (feat / "house-hourly.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return site


def _row(days_ago, hour, motion, unavailable=0):
    base = datetime.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    t = int((base - datetime.timedelta(days=days_ago)).replace(hour=hour).timestamp())
    return {"t": t, "v": 1,
            "camera": {"bedroom": {"motion": motion, "n": 14, "unavailable": unavailable}}}


def test_unavailable_hours_are_excluded_from_the_series(tmp_path):
    # The holdout cut is taken from the newest USABLE row, so the layout is explicit:
    # day 0 is fully unavailable but carries a motion number (must not count);
    # days 1-6 are usable holdout; day 7 is absent; days 8-15 are usable training.
    rows = [_row(0, h, 0.9, unavailable=14) for h in range(24)]
    rows += [_row(d, h, 0.9) for d in range(1, 7) for h in range(24)]
    rows += [_row(d, h, 0.9) for d in range(8, 16) for h in range(24)]
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    assert {k: out["bedroom_hours"][k] for k in ("total", "camera_available")} == {"total": 360, "camera_available": 336}
    assert out["rooms"]["bedroom"]["train_hours"] == 192


def test_all_unavailable_store_is_unknown_not_a_prior(tmp_path):
    rows = [_row(10, h, 0.9, unavailable=14) for h in range(24)]
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    assert out["night_bedroom"]["adopted"] is False
    assert "camera-available" in out["night_bedroom"]["reason"]


def test_learned_night_prior_adopted_only_on_heldout_win(tmp_path):
    # Days 8-15 train and days 1-6 are held out (day 7 absent so the cut is exact).
    # The same pattern holds in both windows: 21Z and 22Z active, 23Z and 0Z quiet.
    # A per-hour prior reproduces that pattern exactly, so it beats the 0.4 constant.
    rows = []
    for d in list(range(8, 16)) + list(range(1, 7)):
        rows.append(_row(d, 21, 0.9))
        rows.append(_row(d, 22, 0.9))
        rows.append(_row(d, 23, 0.001))
        rows.append(_row(d, 0, 0.001))
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    night = out["night_bedroom"]
    assert night["adopted"] is True
    assert night["brier_learned"] < night["brier_hand_set"]
    assert night["heldout_n"] == 24 and night["heldout_n"] >= mp.MIN_HELDOUT


def test_thin_heldout_keeps_hand_set(tmp_path):
    # Only one held-out night hour: below MIN_HELDOUT, hand-set kept even if it would win.
    rows = [_row(10, 22, 0.9), _row(10, 23, 0.001), _row(0, 22, 0.001)]
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    assert out["night_bedroom"]["adopted"] is False
    assert "hand-set" in out["night_bedroom"]["reason"]


def test_main_writes_priors_json_atomically(tmp_path):
    rows = [_row(10, 22, 0.9), _row(10, 23, 0.001)]
    site = _site(tmp_path, rows)
    rc = mp.main(["--site", str(site)])
    assert rc == 0
    doc = json.loads((site / "senses/map/priors.json").read_text())
    assert doc["v"] == 1
    assert doc["source"] == "features/house-hourly.jsonl"
    assert (site / "senses/map/priors.json.tmp").exists() is False


def test_learned_prior_not_adopted_when_heldout_contradicts_training(tmp_path):
    # Counterexample: training says 21Z/22Z active, 23Z/0Z quiet; held-out days flip that.
    # The learned prior is confidently wrong there, so the hand-set prior must be kept.
    rows = []
    for d in range(8, 16):
        rows += [_row(d, 21, 0.9), _row(d, 22, 0.9), _row(d, 23, 0.001), _row(d, 0, 0.001)]
    for d in range(1, 7):
        rows += [_row(d, 21, 0.001), _row(d, 22, 0.001), _row(d, 23, 0.9), _row(d, 0, 0.9)]
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    night = out["night_bedroom"]
    assert night["adopted"] is False
    assert night["brier_learned"] > night["brier_hand_set"]


def test_stale_feature_store_does_not_adopt_a_prior(tmp_path):
    # Same clean pattern as the adoption test, but the store stopped 60 days ago: the learner
    # must not present an old held-out win as current.
    rows = []
    for d in list(range(68, 76)) + list(range(61, 67)):
        rows.append(_row(d, 21, 0.9))
        rows.append(_row(d, 22, 0.9))
        rows.append(_row(d, 23, 0.001))
        rows.append(_row(d, 0, 0.001))
    site = _site(tmp_path, rows)
    out = mp.build(str(site), datetime.datetime.now(UTC))
    night = out["night_bedroom"]
    assert night["adopted"] is False
    assert "stale" in night["reason"] and "hand-set" in night["reason"]
    assert out["bedroom_hours"]["newest_age_hours"] > mp.STALE_HOURS
