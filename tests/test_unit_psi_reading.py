import json

from mishe_tauftauf.unit_psi_reading import MAX_AGE_S, SENSE_ID, read_unit_psi


def _record(at, high, some=1.5):
    return {"at": at, "cgroups": {
        "mishe-plants.slice": {"psi": {"some": {"avg10": some}}, "events": {"high": high},
                               "swap": 100, "swap_max": 1000},
        "mishe-plants.slice/worker.service": {"psi": {"some": {"avg10": 0.25}},
                                              "events": {"high": 4}},
    }}


def _write(path, records, extra=()):
    lines = [json.dumps(r) for r in records] + list(extra)
    path.write_text("\n".join(lines) + "\n")


def test_missing_file_is_unknown_not_calm(tmp_path):
    out = read_unit_psi(tmp_path / "absent.jsonl", now=1000.0)
    assert out["id"] == SENSE_ID
    assert out["state"] == "unknown"
    assert "absent" in out["sample"]


def test_no_parseable_record_is_unknown(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text("not json\n{\"at\": \"x\"}\n")
    out = read_unit_psi(path, now=1000.0)
    assert out["state"] == "unknown"
    assert "malformed=2" in out["sample"]


def test_fresh_record_is_verified_with_window_delta(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_record(900.0, high=10), _record(1000.0, high=13)])
    out = read_unit_psi(path, now=1010.0)
    assert out["state"] == "verified"
    assert "age=10s" in out["sample"]
    assert "top-high-delta=mishe-plants.slice:+3/100s" in out["sample"]
    assert "plants-some-avg10=1.50" in out["sample"]
    assert "swap-plants=10.0%" in out["sample"]


def test_swap_at_cap_states_are_explicit():
    from mishe_tauftauf.unit_psi_reading import swap_at_cap
    assert swap_at_cap({"swap": 990, "swap_max": 1000}) == "yes"
    assert swap_at_cap({"swap": 989, "swap_max": 1000}) == "no"
    assert swap_at_cap({"swap": 5, "swap_max": "max"}) == "uncapped"
    assert swap_at_cap({"swap": 5, "swap_max": 0}) == "unknown"
    assert swap_at_cap({"swap": 5}) == "unknown"
    assert swap_at_cap({"swap_max": 10}) == "unknown"


def test_sample_carries_at_cap_line(tmp_path):
    path = tmp_path / "p.jsonl"
    rec = _record(1000.0, high=13)
    rec["cgroups"]["mishe-plants.slice"]["swap"] = 995
    _write(path, [rec])
    out = read_unit_psi(path, now=1000.0)
    assert "swap-plants-at-cap>=0.99=yes" in out["sample"]


def test_no_earlier_record_in_window_reports_unknown_delta_not_zero(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_record(1000.0, high=13)])
    out = read_unit_psi(path, now=1000.0)
    assert out["state"] == "verified"
    assert "high-delta=unknown(no-window)" in out["sample"]


def test_old_record_is_stale_and_labelled_historical(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_record(1000.0, high=13)])
    out = read_unit_psi(path, now=1000.0 + MAX_AGE_S + 1)
    assert out["state"] == "stale"
    assert out["sample"].startswith("historical:")


def test_malformed_lines_are_skipped_and_counted(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_record(1000.0, high=5)], extra=["garbage", "{}"])
    out = read_unit_psi(path, now=1001.0)
    assert out["state"] == "verified"
    assert "malformed=2" in out["sample"]


def test_empty_cgroups_is_unknown(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [{"at": 1000.0, "cgroups": {}}])
    out = read_unit_psi(path, now=1000.0)
    assert out["state"] == "unknown"


def _stall_record(at, high, total):
    rec = _record(at, high=high)
    rec["cgroups"]["mishe-plants.slice"]["psi"]["some"]["total"] = total
    return rec


def test_sample_reports_exact_some_stall_on_top_high_unit(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_stall_record(900.0, high=10, total=1_000_000),
                  _stall_record(1000.0, high=13, total=1_091_000)])
    out = read_unit_psi(path, now=1010.0)
    assert "top-high-delta=mishe-plants.slice:+3/100s some-stall=+91.0ms" in out["sample"]


def test_stall_is_unknown_when_total_missing_and_absent_when_no_high_delta(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_record(900.0, high=10), _record(1000.0, high=13)])
    out = read_unit_psi(path, now=1010.0)
    assert "some-stall=unknown" in out["sample"]

    path2 = tmp_path / "q.jsonl"
    _write(path2, [_stall_record(900.0, high=10, total=5), _stall_record(1000.0, high=10, total=9)])
    out2 = read_unit_psi(path2, now=1010.0)
    assert "high-delta=0/100s" in out2["sample"]
    assert "some-stall" not in out2["sample"]
