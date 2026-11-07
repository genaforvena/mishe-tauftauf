"""Daylight curve: lamp steps are separated from the sun, blind rows never count."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daylight_curve", ROOT / "tools/daylight_curve.py")
dc = importlib.util.module_from_spec(spec)
sys.modules["daylight_curve"] = dc
spec.loader.exec_module(dc)

T0 = 1794000000  # a fixed UTC instant; hour math only uses gmtime


def _rows(room, luma_at, start, stop, every=45, status="ok"):
    return [{"room": room, "status": status, "captured_at_epoch_s": float(t),
             "mean_luma": luma_at(t), "grid": [0] * 48, "grid_h": 6, "grid_w": 8, "source": "webcam"}
            for t in range(start, stop, every)]


def test_lamp_step_is_separated_from_sun_curve():
    # Sun ramps slowly from 40 to 60 over 2000 s, then a lamp adds +50 at T0+1500.
    def luma(t):
        base = 40 + 20 * min(1.0, (t - T0) / 2000.0)
        return base + (50 if t >= T0 + 1500 else 0)

    rows = _rows("living room", luma, T0, T0 + 3000)
    report = dc.build(rows)
    room = report["rooms"]["living room"]
    assert room["lamp_steps"] == 1
    lamp_hour = dc.time.gmtime(T0 + 1500).tm_hour
    # Samples within the guard of the step are dropped, so the hour's median stays
    # on the sun curve rather than jumping by the lamp's +50.
    cell = room["hours"][f"{lamp_hour:02d}"]
    assert cell["lamp_steps"] == 1
    assert cell["median_luma"] is not None and cell["median_luma"] < 70


def test_blind_rows_are_never_counted():
    rows = _rows("bedroom", lambda t: 3.3, T0, T0 + 600, every=60, status="blind")
    report = dc.build(rows)
    assert report["rooms"] == {}
    assert "no ok rows" in dc.panel(report)


def test_empty_hour_is_unknown_not_guessed():
    rows = _rows("living room", lambda t: 50.0, T0, T0 + 600)
    report = dc.build(rows)
    empty_hour = f"{(dc.time.gmtime(T0).tm_hour + 5) % 24:02d}"
    cell = report["rooms"]["living room"]["hours"][empty_hour]
    assert cell == {"samples": 0, "median_luma": None, "lamp_steps": 0}


def test_main_writes_daylight_json(tmp_path):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    lines = [json.dumps(r) for r in _rows("living room", lambda t: 45.0, T0, T0 + 900)]
    lines.append("not json")
    (site / "spatial/work/household/signatures.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert dc.main(["--site", str(site)]) == 0
    data = json.loads((site / "spatial/work/household/daylight.json").read_text(encoding="utf-8"))
    assert data["rooms"]["living room"]["total_samples"] == len(lines) - 1


def test_no_usable_rows_exits_unknown_not_an_empty_green(tmp_path):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    (site / "spatial/work/household/signatures.jsonl").write_text('{"room": "living room", "status": "blind"}\n')
    assert dc.main(["--site", str(site)]) == 1
    assert not (site / "spatial/work/household/daylight.json").exists()


def test_usable_rows_write_the_curve_and_exit_zero(tmp_path):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    rows = _rows("living room", lambda t: 60.0, T0, T0 + 3000)
    (site / "spatial/work/household/signatures.jsonl").write_text(
        "".join(__import__("json").dumps(r) + "\n" for r in rows))
    assert dc.main(["--site", str(site)]) == 0
    assert (site / "spatial/work/household/daylight.json").exists()


def test_household_daylight_duty_is_declared_with_bounded_timing():
    import json
    manifest = json.loads((ROOT / "duties.json").read_text())
    item = next(d for d in manifest["duties"] if d["name"] == "household-daylight")
    assert item["resolver"] == "senses"
    assert item["command"][-2:] == ["--site", "{home}"]
    assert item["command"][1] == "{repo}/tools/daylight_curve.py"
    assert item["every"] == 600 and item["max_age"] >= item["every"] + item["timeout"]
    assert item["max_age"] <= 6 * item["every"]
