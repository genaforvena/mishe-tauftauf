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


def _fixed_width_log(path):
    # Ten 25-byte lines ('{"at": N, "cgroups": {}}' plus newline), 250 bytes in all.
    path.write_bytes(b"".join(json.dumps({"at": i, "cgroups": {}}).encode() + b"\n"
                              for i in range(10)))
    assert path.stat().st_size == 250


def test_tail_seek_landing_on_line_start_keeps_that_record(tmp_path, monkeypatch):
    # A 75-byte tail seeks to byte 175, exactly the start of line 7: lines 7, 8, 9 are whole.
    import mishe_tauftauf.unit_psi_reading as reading

    path = tmp_path / "sampler.jsonl"
    _fixed_width_log(path)
    monkeypatch.setattr(reading, "TAIL_BYTES", 75)
    records, malformed = reading._tail_records(path)
    assert [r["at"] for r in records] == [7, 8, 9]
    assert malformed == 0


def test_tail_seek_inside_a_line_drops_only_the_partial_line(tmp_path, monkeypatch):
    # A 70-byte tail seeks to byte 180, inside line 7: that partial line goes, 8 and 9 stay.
    import mishe_tauftauf.unit_psi_reading as reading

    path = tmp_path / "sampler.jsonl"
    _fixed_width_log(path)
    monkeypatch.setattr(reading, "TAIL_BYTES", 70)
    records, malformed = reading._tail_records(path)
    assert [r["at"] for r in records] == [8, 9]
    assert malformed == 0


def _hier_record(at, parent_total, child_total, parent_high=5):
    return {"at": at, "cgroups": {
        "mishe-lifeline.slice": {"psi": {"some": {"avg10": 1.0, "total": parent_total}},
                                 "events": {"high": parent_high}},
        "mishe-lifeline.slice/mishe-genome.service": {
            "psi": {"some": {"avg10": 0.5, "total": child_total}}, "events": {"high": 1}},
    }}


def test_stall_inversion_is_detected_both_sides():
    from mishe_tauftauf.unit_psi_reading import stall_inversions
    # senses wake 160438: child +102.62 s over the same interval as parent +70.86 s.
    assert stall_inversions({"mishe-lifeline.slice": 70_860_000,
                             "mishe-lifeline.slice/mishe-genome.service": 102_620_000}) == {
        "mishe-lifeline.slice", "mishe-lifeline.slice/mishe-genome.service"}


def test_stall_within_skew_tolerance_and_consistent_hierarchy_are_not_flagged():
    from mishe_tauftauf.unit_psi_reading import stall_inversions
    assert stall_inversions({"a": 5_000_000, "a/b": 5_400_000}) == set()   # sampling skew
    assert stall_inversions({"a": 70_000_000, "a/b": 10_000_000}) == set()  # healthy


def test_sample_refuses_to_attribute_stall_on_an_inverted_hierarchy(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_hier_record(900.0, 1_000_000_000, 1_000_000_000, parent_high=5),
                  _hier_record(1000.0, 1_070_860_000, 1_102_620_000, parent_high=8)])
    out = read_unit_psi(path, now=1010.0)
    assert "stall-hierarchy=inverted:" in out["sample"]
    assert "some-stall=unknown(hierarchy-inverted)" in out["sample"]
    assert "some-stall=+" not in out["sample"]


def test_sample_attributes_stall_when_hierarchy_is_consistent(tmp_path):
    path = tmp_path / "p.jsonl"
    _write(path, [_hier_record(900.0, 1_000_000_000, 100_000_000, parent_high=5),
                  _hier_record(1000.0, 1_070_860_000, 150_000_000, parent_high=8)])
    out = read_unit_psi(path, now=1010.0)
    assert "stall-hierarchy=ok" in out["sample"]
    assert "some-stall=+70860.0ms" in out["sample"]
