"""household_features: hourly house-level store; only complete hours, no zero-fill, idempotent, evidence kept."""
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("household_features", Path(__file__).resolve().parents[1] / "tools" / "household_features.py")
hf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hf)

H0 = 498_612 * 3600           # an exact UTC hour start (an exact hour boundary)
assert H0 % 3600 == 0


def site(tmp_path):
    d = tmp_path / "spatial" / "work" / "household"
    d.mkdir(parents=True)
    return tmp_path, d


def put(path, rows, raw=()):
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        for line in raw:
            fh.write(line)


def audio(t, rms, status="NO_SPEECH"):
    return {"t": t, "rms": rms, "status": status, "room": "livingroom"}


def sig(t, luma, grid, room="living room", status="ok"):
    return {"captured_at_epoch_s": t, "mean_luma": luma, "grid": grid, "room": room, "status": status}


def store_rows(tmp_path):
    p = tmp_path / "features" / "house-hourly.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_only_complete_hours_are_written_each_once_and_absence_is_not_zero(tmp_path):
    home, d = site(tmp_path)
    put(d / "audio.jsonl", [audio(H0 + 10, 100), audio(H0 + 20, 300, "SILENT_OR_DROPPED"), audio(H0 + 3700, 500)])
    put(d / "signatures.jsonl", [sig(H0 + 5, 50.0, [0, 0, 0, 0]), sig(H0 + 65, 52.0, [51, 0, 0, 0])])
    code, detail = hf.run(home, H0 + 3700 + 100)          # now is inside hour 1: only hour 0 is complete
    rows = store_rows(tmp_path)
    assert code == 0 and detail["written"] == 1 and len(rows) == 1
    r = rows[0]
    assert r["t"] == H0
    assert r["audio"]["livingroom"]["n"] == 2 and r["audio"]["livingroom"]["status"] == {"NO_SPEECH": 1, "SILENT_OR_DROPPED": 1}
    assert r["camera"]["living room"]["motion"] == round(51 / 4 / 255, 5)
    assert "phones" not in r                                 # no phone rows: UNKNOWN, never a zero block
    code, detail = hf.run(home, H0 + 3700 + 200)          # same hour still open: nothing new
    assert detail["written"] == 0 and len(store_rows(tmp_path)) == 1
    code, detail = hf.run(home, H0 + 2 * 3600 + 5)        # hour 1 completes
    assert detail["written"] == 1 and [x["t"] for x in store_rows(tmp_path)] == [H0, H0 + 3600]
    assert store_rows(tmp_path)[1]["audio"]["livingroom"]["rms_max"] == 500


def test_a_second_run_never_duplicates_and_a_lost_state_file_does_not_either(tmp_path):
    home, d = site(tmp_path)
    put(d / "audio.jsonl", [audio(H0 + 10, 100), audio(H0 + 3610, 200)])
    hf.run(home, H0 + 2 * 3600 + 1)
    hf.run(home, H0 + 2 * 3600 + 2)
    (tmp_path / "features" / "house-hourly.state.json").unlink()      # offsets lost: re-read from byte 0
    hf.run(home, H0 + 2 * 3600 + 3)
    assert [x["t"] for x in store_rows(tmp_path)] == [H0, H0 + 3600]


def test_malformed_and_unusable_lines_are_counted_not_repaired(tmp_path):
    home, d = site(tmp_path)
    put(d / "audio.jsonl", [audio(H0 + 10, 100), {"rms": 1}], raw=["{not json\n"])
    put(d / "audio.jsonl", [audio(H0 + 20, 120)])
    code, detail = hf.run(home, H0 + 3600 + 1)
    assert detail["malformed"]["audio"] == 1 and detail["unusable"]["audio"] == 1
    assert store_rows(tmp_path)[0]["audio"]["livingroom"]["n"] == 2


def test_a_truncated_source_is_reread_without_duplicating_old_hours(tmp_path):
    home, d = site(tmp_path)
    put(d / "audio.jsonl", [audio(H0 + 10, 100), audio(H0 + 3610, 200)])
    hf.run(home, H0 + 2 * 3600 + 1)
    (d / "audio.jsonl").write_text(json.dumps(audio(H0 + 7210, 300)) + "\n")   # rotated, shorter than the offset
    hf.run(home, H0 + 3 * 3600 + 1)
    assert [x["t"] for x in store_rows(tmp_path)] == [H0, H0 + 3600, H0 + 7200]


def test_phones_are_per_device_with_stale_counted_and_no_person_keys(tmp_path):
    home, d = site(tmp_path)
    ok = {"status": "ok", "accel": [0.0, 0.0, 10.0], "battery_pct": 80}
    ok2 = {"status": "ok", "accel": [0.0, 0.0, 12.0], "battery_pct": 79}
    put(d / "phones.jsonl", [{"t": H0 + 10, "note3": ok, "redmi10": {"status": "stale", "lux": None}},
                             {"t": H0 + 130, "note3": ok2, "redmi10": {"status": "ok", "lux": 40.0}}])
    hf.run(home, H0 + 3601)
    p = store_rows(tmp_path)[0]["phones"]
    assert p["note3"]["ok"] == 2 and p["note3"]["accel_std"] == 1.0 and p["note3"]["battery_last"] == 79
    assert p["redmi10"]["stale"] == 1 and p["redmi10"]["lux_mean"] == 40.0


def test_no_source_files_is_unknown_not_green(tmp_path):
    code, detail = hf.run(tmp_path, H0)
    assert code == 4 and "no household source file" in detail["reason"]


def test_retention_apply_refuses_the_feature_store_under_every_spelling(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("retention_apply", Path(__file__).resolve().parents[1] / "checks" / "retention-apply.py")
    ra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra)
    (tmp_path / "features" / "sub").mkdir(parents=True)
    (tmp_path / "features" / "house-hourly.jsonl").write_text("{}\n")
    (tmp_path / "link").symlink_to(tmp_path / "features")
    for name in ("features", "features/", "./features", "features/sub", "x/../features", str(tmp_path / "features"), "link",
                 ".", "./", "x/..", str(tmp_path), "link/.."):
        assert ra.main(["--home", str(tmp_path), "--class", name, "--apply"]) == 2, name
        assert "not a retention class" in capsys.readouterr().out
    assert (tmp_path / "features" / "house-hourly.jsonl").read_text() == "{}\n"
