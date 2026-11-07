"""Household activity: lights, quiet blocks and rhythm are derived; no usable rows is UNKNOWN."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("household_activity", ROOT / "tools/household_activity.py")
ha = importlib.util.module_from_spec(spec)
sys.modules["household_activity"] = ha
spec.loader.exec_module(ha)

T0 = 1794000000  # fixed UTC instant


def _frames(room, start, stop, luma_at, moving_at, every=45):
    """Ok signature rows; a moving frame alternates the grid so consecutive frames differ."""
    rows = []
    for i, t in enumerate(range(start, stop, every)):
        grid = [(200 if (i % 2 and moving_at(t)) else 0)] * 48
        rows.append({"room": room, "status": "ok", "captured_at_epoch_s": float(t),
                     "mean_luma": luma_at(t), "grid": grid, "grid_h": 6, "grid_w": 8})
    return rows


def _site(tmp_path, sig_rows, audio_rows=()):
    base = tmp_path / "site/spatial/work/household"
    base.mkdir(parents=True)
    (base / "signatures.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sig_rows))
    (base / "audio.jsonl").write_text("".join(json.dumps(r) + "\n" for r in audio_rows))
    return tmp_path / "site", base


def test_lamp_off_is_lights_out_and_moving_frames_are_not_quiet():
    lamp_off = T0 + 3000
    lr = _frames("living room", T0, T0 + 9000, lambda t: 80 if t < lamp_off else 40,
                 moving_at=lambda t: t < T0 + 2000)
    recs, times = ha.frame_records(lr)
    events = ha.luma_events(recs, times)
    offs = [e for e in events if e["kind"] == "off"]
    assert len(offs) == 1 and abs(offs[0]["t"] - lamp_off) <= ha.EVENT_GAP
    summary = ha.summarize((recs, times), ([], []))
    assert summary["lights_out_utc"] == ha.hhmm(offs[0]["t"])
    # Moving frames before T0+2000 are activity, not quiet; the quiet tail is.
    quiet = summary["longest_quiet_block_living_room"]
    assert quiet is not None and quiet["from_utc"] >= ha.hhmm(T0 + 2000 + ha.ACT_WINDOW)


def test_first_activity_after_lights_out_ignores_the_lamp_transient():
    lamp_off = T0 + 3000
    # The grid churns for 1000 s right after the lamp goes off (the transient). The first
    # activity reported must come after the 600 s window has passed, not at the first churn.
    lr = _frames("living room", T0, T0 + 9000, lambda t: 80 if t < lamp_off else 40,
                 moving_at=lambda t: lamp_off <= t < lamp_off + 1000)
    recs, times = ha.frame_records(lr)
    summary = ha.summarize((recs, times), ([], []))
    first = summary["first_activity_after_lights_out_utc"]
    assert first is not None
    assert first > ha.hhmm(lamp_off + ha.ACT_WINDOW)


def test_missing_parts_read_unknown_in_the_rhythm_line():
    line = ha.rhythm_line(ha.summarize(([], []), ([], [])))
    assert "lights out UNKNOWN" in line and "longest quiet block UNKNOWN" in line
    assert "provisional" in line.lower()


def test_sound_is_bucketed_by_utc_hour_of_day():
    rows = [{"t": T0 + k, "room": "livingroom", "rms": v} for k, v in ((0, 100), (30, 300), (60, 500))]
    rows.append({"t": "bad", "room": "livingroom", "rms": 1})
    out = ha.sound_by_hour(rows)
    hour = f"{ha.time.gmtime(T0).tm_hour:02d}"
    assert out["livingroom"][hour] == {"samples": 3, "median_rms": 300.0}


def test_no_usable_rows_returns_none_and_main_exits_unknown(tmp_path):
    site, base = _site(tmp_path, [{"room": "living room", "status": "blind"}])
    assert ha.build([{"room": "living room", "status": "blind"}], [], 0.0) is None
    assert ha.main(["--site", str(site)]) == 1
    assert not (base / "activity.json").exists()


def test_main_writes_activity_json_atomically_and_exits_zero(tmp_path):
    lr = _frames("living room", T0, T0 + 4000, lambda t: 60, moving_at=lambda t: False)
    site, base = _site(tmp_path, lr, [{"t": T0, "room": "livingroom", "rms": 120}])
    assert ha.main(["--site", str(site)]) == 0
    report = json.loads((base / "activity.json").read_text())
    assert report["rooms"]["living room"]["ok_samples"] == len(lr)
    assert "bedroom" not in report["rooms"]
    assert report["sound_by_hour"]["livingroom"]
    assert not (base / "activity.json.tmp").exists()


def test_household_activity_duty_is_declared_with_bounded_timing():
    manifest = json.loads((ROOT / "duties.json").read_text())
    item = next(d for d in manifest["duties"] if d["name"] == "household-activity")
    assert item["resolver"] == "senses"
    assert item["command"][1] == "{repo}/tools/household_activity.py"
    assert item["command"][-2:] == ["--site", "{home}"]
    assert item["every"] == 600 and item["max_age"] >= item["every"] + item["timeout"]
    assert item["max_age"] <= 6 * item["every"]


def test_unobserved_gap_is_unknown_and_never_part_of_a_quiet_block():
    # Still frames, then a 2 h hole (camera unreachable), then still frames again.
    part1 = _frames("living room", T0, T0 + 3000, lambda t: 40, moving_at=lambda t: False)
    part2 = _frames("living room", T0 + 3000 + 7200, T0 + 3000 + 7200 + 3000, lambda t: 40,
                    moving_at=lambda t: False)
    recs, times = ha.frame_records(part1 + part2)
    gaps = ha.unobserved_gaps(times)
    assert len(gaps) == 1 and 7200 <= gaps[0][1] - gaps[0][0] <= 7200 + 45
    summary = ha.summarize((recs, times), ([], []))
    assert summary["unknown_intervals"]["living room"][0]["minutes"] == 120
    quiet = summary["longest_quiet_block_living_room"]
    # Neither part alone is a 10-minute run across the hole: the quiet block must not span the gap.
    assert quiet is None or (ha.hhmm(gaps[0][0]) >= quiet["to_utc"] or quiet["from_utc"] >= ha.hhmm(gaps[0][1]))
    assert "UNKNOWN" in ha.gap_text(summary)


def test_operator_blind_interval_is_tagged_unknown_with_null_start(tmp_path):
    lr = _frames("living room", T0, T0 + 3000, lambda t: 40, moving_at=lambda t: False)
    blind = [{"room": "living room", "from_utc": None, "to_utc": "09:34Z", "minutes": None,
              "reason": "camera blind"}]
    report = ha.build(lr, [], float(T0 + 3000), blind)
    spans = report["summary"]["unknown_intervals"]["living room"]
    assert spans == blind and report["summary"]["unknown_intervals"]["bedroom"] == []
    assert ha.operator_blind(tmp_path) == []
