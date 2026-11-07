from mishe_tauftauf.redmi10_accel import BIN_S, SENSE_ID, read_redmi10_accel_coverage

NOW = 1793964600.0
START = NOW - 3 * 3600


def write(tmp_path, lines):
    path = tmp_path / "redmi10.observations.log"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def valid_rows(seq0, start, end, every=5):
    rows = []
    seq = seq0
    t = start
    while t < end:
        rows.append(f"{seq} {t} acceleration x=0.1 y=0.0 z=9.8 accuracy=3")
        seq += 1
        t += every
    return rows, seq


def test_fresh_valid_acceleration_across_window_is_verified(tmp_path):
    rows, _ = valid_rows(1, START, NOW + 1, every=5)
    path = write(tmp_path, rows)
    out = read_redmi10_accel_coverage(path, now=NOW)
    assert out["id"] == SENSE_ID and out["state"] == "verified"
    assert out["coverage"] == 1.0 and out["longest_gap_bins"] == 0
    assert out["row_age_s"] <= 5


def test_stale_stretch_is_a_gap_never_still(tmp_path):
    # Valid rows for the first hour, then only "unavailable reason=stale" for the last two hours.
    rows, seq = valid_rows(1, START, START + 3600, every=5)
    stale = [f"{seq + i} {START + 3600 + i * 5} acceleration unavailable reason=stale age_ms={i * 5000}"
             for i in range(int(2 * 3600 / 5))]
    path = write(tmp_path, rows + stale)
    out = read_redmi10_accel_coverage(path, now=NOW)
    assert out["state"] == "unknown"
    assert "unavailable" in out["sample"] or "stale" in out["sample"]
    assert out["covered_bins"] == 6
    assert out["longest_gap_bins"] == 12
    assert "values" not in out


def test_fresh_row_after_stale_stretch_is_verified_with_gap_recorded(tmp_path):
    rows, seq = valid_rows(1, START, START + 3600, every=5)
    stale = [f"{seq + i} {START + 3600 + i * 5} acceleration unavailable reason=stale age_ms=1"
             for i in range(int(3600 / 5))]
    tail, _ = valid_rows(seq + len(stale), START + 7200, NOW + 1, every=5)
    path = write(tmp_path, rows + stale + tail)
    out = read_redmi10_accel_coverage(path, now=NOW)
    assert out["state"] == "verified"
    assert out["longest_gap_bins"] == 6
    assert 0 < out["coverage"] < 1


def test_old_valid_rows_are_unknown_not_current(tmp_path):
    rows, _ = valid_rows(1, START, START + 600, every=5)
    path = write(tmp_path, rows)
    out = read_redmi10_accel_coverage(path, now=NOW)
    assert out["state"] == "unknown"
    assert "stale" in out["sample"]


def test_no_acceleration_rows_is_unknown(tmp_path):
    path = write(tmp_path, [f"{i} {START + i} light lux=5.0 accuracy=3" for i in range(10)])
    out = read_redmi10_accel_coverage(path, now=NOW)
    assert out["state"] == "unknown"
    assert "no acceleration row" in out["sample"]


def test_missing_log_is_unknown(tmp_path):
    out = read_redmi10_accel_coverage(tmp_path / "absent.log", now=NOW)
    assert out["state"] == "unknown"
    assert BIN_S == 600
