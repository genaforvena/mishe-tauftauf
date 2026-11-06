from mishe_tauftauf.redmi10_stream import SENSE_ID, parse_row, read_redmi10_stream

NOW = 1793964600.0


def write(tmp_path, lines):
    path = tmp_path / "redmi10.observations.log"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def test_parse_row_reads_kind_and_fields_including_leading_key_value():
    row = parse_row("460706 1793964577 wifi_link_rssi_dbm=-56 source=androidbody_wifi_api")
    assert row == {"epoch": 1793964577.0, "kind": "", "fields": {"wifi_link_rssi_dbm": "-56",
                                                                 "source": "androidbody_wifi_api"}}
    assert parse_row("garbage") is None
    assert parse_row("1 notanumber light lux=3") is None


def test_fresh_stream_reports_last_lux_battery_and_rssi(tmp_path):
    path = write(tmp_path, [
        "1 1793964500 light lux=10.0 accuracy=3",
        "2 1793964577 battery level=1.0 charging=true",
        "3 1793964578 wifi_link_rssi_dbm=-56 source=androidbody_wifi_api",
        "4 1793964595 light lux=53.0 accuracy=3",
    ])
    out = read_redmi10_stream(path, now=NOW)
    assert out["id"] == SENSE_ID and out["state"] == "verified"
    assert out["values"]["lux"] == {"value": 53.0, "epoch": 1793964595.0}
    assert out["values"]["battery"]["charging"] is True
    assert out["values"]["wifi_rssi_dbm"]["value"] == -56.0
    assert "lux=53" in out["sample"] and "battery=1 charging=yes" in out["sample"]
    assert out["source"] == str(path)


def test_stale_stream_is_unknown_and_values_are_history_only(tmp_path):
    path = write(tmp_path, ["1 1793964000 light lux=53.0 accuracy=3"])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "unknown"
    assert "history" in out and "values" not in out
    assert "stale" in out["sample"]


def test_missing_log_and_empty_tail_are_unknown(tmp_path):
    missing = read_redmi10_stream(tmp_path / "absent.log", now=NOW)
    assert missing["state"] == "unknown" and "unreadable" in missing["sample"]
    empty = write(tmp_path, ["not a row", "also not"])
    assert read_redmi10_stream(empty, now=NOW)["state"] == "unknown"


def test_fresh_rows_without_any_quantity_stay_unknown(tmp_path):
    path = write(tmp_path, ["1 1793964599 ble_scan_state windows=0 phase=none"])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "unknown" and "no current lux" in out["sample"]


def test_partial_first_line_of_large_tail_is_dropped(tmp_path):
    filler = "9 1793900000 ble_scan_state windows=0 " + "x" * 200
    path = write(tmp_path, [filler] * 400 + ["10 1793964599 light lux=7.5 accuracy=3"])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "verified" and out["values"]["lux"]["value"] == 7.5


def test_reads_live_log_without_writing(tmp_path):
    from mishe_tauftauf.redmi10_stream import DEFAULT_LOG
    import pytest
    if not DEFAULT_LOG.exists():
        pytest.skip("live Redmi10 stream not present on this host")
    before = DEFAULT_LOG.stat().st_mtime_ns
    out = read_redmi10_stream(DEFAULT_LOG, now=__import__("time").time())
    assert out["state"] in ("verified", "unknown")
    assert DEFAULT_LOG.stat().st_mtime_ns == before


def test_partially_appended_final_row_is_not_read_as_current(tmp_path):
    path = tmp_path / "redmi10.observations.log"
    path.write_text("1 1793964500 light lux=10.0 accuracy=3\n2 1793964595 light lux=5", encoding="utf-8")
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "verified"
    assert out["values"]["lux"] == {"value": 10.0, "epoch": 1793964500.0}
    assert out["last_row_epoch"] == 1793964500.0


def test_non_finite_row_epoch_is_not_a_row(tmp_path):
    assert parse_row("1 nan light lux=3") is None
    assert parse_row("1 inf light lux=3") is None
    path = write(tmp_path, ["1 1793964599 light lux=2.0 accuracy=3", "2 nan light lux=99"])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "verified" and out["values"]["lux"]["value"] == 2.0


def test_row_without_numeric_sequence_is_not_a_row(tmp_path):
    assert parse_row("bad 1793964599 light lux=99") is None
    assert parse_row("-3 1793964599 light lux=99") is None
    path = write(tmp_path, ["bad 1793964599 light lux=99", "1 1793964599 light lux=2.0 accuracy=3"])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "verified" and out["values"]["lux"]["value"] == 2.0


def test_old_quantity_is_history_even_when_another_kind_is_fresh(tmp_path):
    path = write(tmp_path, [
        "1 1793964000 light lux=53.0 accuracy=3",
        "2 1793964599 ble_scan_state windows=0 phase=none",
        "3 1793964598 wifi_link_rssi_dbm=-60 source=androidbody_wifi_api",
    ])
    out = read_redmi10_stream(path, now=NOW)
    assert out["state"] == "verified"
    assert "lux" not in out["values"] and "lux" in out["history"]
    assert "lux=" not in out["sample"] and "no current lux" in out["sample"]
    assert out["values"]["wifi_rssi_dbm"]["value"] == -60.0
