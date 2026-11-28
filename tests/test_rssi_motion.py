"""RSSI motion study: log decoding, AUC, day-shift null, verdicts and UNKNOWN guards."""
import importlib.util
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rt, rm = load("rssi_text"), load("rssi_motion")
DAY = 86400
T0 = 1_795_000_000 - 1_795_000_000 % rm.BUCKET_S


def test_decode_only_the_two_kinds_and_refuse_the_rest():
    assert rt.parse_obs_line("5 100 wifi_link_rssi_dbm=-61 source=x") == (100, "rssi", -61)
    assert rt.parse_obs_line("6 101 acceleration x=0.1 y=0.2 z=9.8 accuracy=3") == (101, "accel", (0.1, 0.2, 9.8))
    assert rt.parse_obs_line("7 102 acceleration unavailable reason=stale") is None
    assert rt.parse_obs_line("8 103 wifi_link_rssi_dbm unavailable reason=disconnected") is None
    assert rt.parse_obs_line("9 x wifi_link_rssi_dbm=-61") is None
    assert rt.parse_obs_line("10 104 light lux=1.0") is None


def test_auc_orders_ties_and_empty_sides():
    assert rm.auc([3, 4], [1, 2]) == 1.0
    assert rm.auc([1, 2], [3, 4]) == 0.0
    assert rm.auc([1, 1], [1, 1]) == 0.5
    assert rm.auc([], [1]) is None


def make(days, planted, seed=1):
    """Bucket features and a label; planted makes RSSI jitter higher exactly in active buckets."""
    rng = random.Random(seed)
    feats, label = {}, {}
    for i in range(days * 96):
        t = T0 + i * rm.BUCKET_S
        active = (i % 96) in range(30, 50) and rng.random() < 0.7 or rng.random() < 0.05
        base = 1.0 + (1.5 if (planted and active) else 0.0)
        step = base + rng.random()
        feats[t] = {"rssi_std": step, "rssi_step": step, "level_dev": rng.random(), "phone_acc_std": None}
        label[t] = active
    return feats, label


def test_planted_jitter_is_signal_and_noise_is_not():
    f, lab = make(18, planted=True)
    assert rm.study(f, lab)["state"] == "SIGNAL"
    f, lab = make(18, planted=False)
    assert rm.study(f, lab)["state"] == "NO_SIGNAL"


def test_daily_rhythm_alone_is_not_signal():
    """Jitter that follows only the clock matches the label's clock but must not beat the day-shift null."""
    rng = random.Random(3)
    feats, label = {}, {}
    for i in range(18 * 96):
        t = T0 + i * rm.BUCKET_S
        busy = (i % 96) in range(30, 50)
        step = (2.5 if busy else 1.0) + rng.random()
        feats[t] = {"rssi_std": step, "rssi_step": step, "level_dev": rng.random(), "phone_acc_std": None}
        label[t] = busy and rng.random() < 0.9
    assert rm.study(feats, label)["state"] == "NO_SIGNAL"


def test_too_little_data_is_unknown():
    f, lab = make(1, planted=True)
    res = rm.study(f, lab)
    assert res["state"] == "UNKNOWN" and "need" in res["reason"]


def write_site(tmp_path, log_epoch_end, quiet=False):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    (site / "senses/map").mkdir(parents=True)
    log = tmp_path / "obs.log"
    rng = random.Random(5)
    buckets, lines = [], []
    n = 0
    for i in range(10 * 96):
        t = T0 + i * rm.BUCKET_S
        act = 0.5 if (i % 96) in range(30, 50) else 0.01
        buckets.append({"activity": act, "frame_pairs": 3, "utc_iso": rm.rt.datetime.datetime.fromtimestamp(
            t, rm.rt.datetime.timezone.utc).isoformat()})
        for k in range(rm.MIN_RSSI + 5):
            if t + k * 5 <= log_epoch_end:
                n += 1
                lines.append(f"{n} {t + k * 5} wifi_link_rssi_dbm={-60 - (rng.randint(0, 3) if act > 0.1 else 0)} source=x")
    log.write_text("\n".join(lines) + "\n")
    doc = {"rooms": {"hall": {"activity_quantiles": {"p90": 0.2}, "buckets": buckets}}}
    (site / "spatial/work/household/activity.json").write_text(json.dumps(doc))
    (site / "senses/map/rssi-table.json").write_text(json.dumps({"vantages": [{"vantage": "redmi10", "source": str(log)}]}))
    return site


def test_run_reads_site_and_stale_log_is_unknown(tmp_path):
    end = T0 + 10 * DAY
    site = write_site(tmp_path, end)
    fresh = rm.run(str(site), None, rm.rt.datetime.datetime.fromtimestamp(end + 60, rm.rt.datetime.timezone.utc))
    assert fresh["state"] in {"SIGNAL", "NO_SIGNAL"} and fresh["house"]["joined"] > 900
    stale = rm.run(str(site), None, rm.rt.datetime.datetime.fromtimestamp(end + 2 * DAY, rm.rt.datetime.timezone.utc))
    assert stale["state"] == "UNKNOWN" and "not current" in stale["reason"]


def test_missing_inputs_are_unknown(tmp_path):
    now = rm.rt.datetime.datetime.fromtimestamp(T0, rm.rt.datetime.timezone.utc)
    assert rm.run(str(tmp_path), None, now)["state"] == "UNKNOWN"
    site = write_site(tmp_path, T0 + 10 * DAY)
    (site / "senses/map/rssi-table.json").unlink()
    assert "names no redmi10 log" in rm.run(str(site), None, now)["reason"]
    assert rm.run(str(site), str(tmp_path / "nope.log"), now)["state"] == "UNKNOWN"


def test_main_writes_file_and_exit_code(tmp_path, capsys):
    site = write_site(tmp_path, T0 + 10 * DAY)
    code = rm.main(["--site", str(site), "--now", rm.rt.datetime.datetime.fromtimestamp(
        T0 + 10 * DAY + 60, rm.rt.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")])
    assert code == 0 and (site / "senses/map/rssi-motion.json").exists()
    assert capsys.readouterr().out.startswith("rssi-motion: ")


def test_stratified_by_hour_sees_planted_jitter_and_drops_the_clock_only_rhythm():
    f, lab = make(18, planted=True)
    hours, s = rm.stratified(f, lab)
    assert hours >= 2 and s["rssi_std"] > 0.6
    # whole-hour rhythm: hour strata remove it. A rhythm that starts mid-hour leaks into the boundary hours
    # (the stratified score is per hour, so it cannot separate buckets within one hour); see stratified().
    rng = random.Random(3)
    feats, label = {}, {}
    for i in range(18 * 96):
        t = T0 + i * rm.BUCKET_S
        busy = (i % 96) in range(32, 64)
        step = (2.5 if busy else 1.0) + rng.random()
        feats[t] = {"rssi_std": step, "rssi_step": step, "level_dev": rng.random(), "phone_acc_std": None}
        label[t] = busy and rng.random() < 0.9
    _, s_rhythm = rm.stratified(feats, label)
    assert s_rhythm["rssi_std"] < 0.6


def test_stratified_skips_thin_hours_and_reports_none_when_no_hour_qualifies():
    f, lab = make(1, planted=True)
    hours, s = rm.stratified(f, lab)
    assert hours <= 24 and all((v is None) == (hours == 0) or hours > 0 for v in s.values())
    assert rm.stratified({}, {})[0] == 0 and rm.stratified({}, {})[1]["rssi_std"] is None


def test_router_vantage_reads_only_the_named_client_and_keeps_sparse_buckets(tmp_path):
    site = write_site(tmp_path, T0 + 10 * DAY)
    rows = []
    for i in range(10 * 96 * 3):   # one router poll every 300 s over the camera window
        t = T0 + i * 300
        cl = [{"name": "Redmi-10", "dbm": -30 - (i * 7) % 5}, {"name": "iPad-Rozalia", "dbm": -20}]
        rows.append(json.dumps({"ts": t, "clients": cl}))
    rows.append(json.dumps({"ts": T0 + 50, "clients": [{"name": "Redmi-10", "dbm": None}]}))
    rows.append("not json")
    (tmp_path / "router.jsonl").write_text("\n".join(rows) + "\n")
    buckets, newest = rm.read_router(str(tmp_path / "router.jsonl"), T0)
    assert newest == T0 + (10 * 96 * 3 - 1) * 300
    assert all(-40 < v < -20 for b in buckets.values() for v in b["rssi"])
    assert sum(len(b["rssi"]) for b in buckets.values()) == 10 * 96 * 3
    now = rm.rt.datetime.datetime.fromtimestamp(newest, rm.rt.datetime.timezone.utc)
    res = rm.run(str(site), str(tmp_path / "router.jsonl"), now, vantage="router-station")
    assert res["vantage"] == "router-station"
    assert res["min_rssi_rows_per_bucket"] == rm.MIN_RSSI_ROUTER
    assert res["state"] in ("NO_SIGNAL", "SIGNAL", "UNKNOWN")
    assert res.get("rssi_buckets", 0) > 0
    bad = rm.run(str(site), None, now, vantage="nope")
    assert bad["state"] == "UNKNOWN"


def test_router_refuses_non_finite_json_and_keeps_the_median_key(tmp_path):
    # NaN / Infinity are not JSON: a NaN ts used to pass isinstance and crash int(ts) in read_router
    rows = [
        '{"ts": NaN, "clients": [{"name": "Redmi-10", "dbm": -40}]}',
        '{"ts": ' + str(T0 + 10) + ', "clients": [{"name": "Redmi-10", "dbm": Infinity}]}',
        '{"ts": ' + str(T0 + 20) + ', "clients": [{"name": "Redmi-10", "dbm": -41}]}',
        '{"ts": -Infinity, "clients": [{"name": "Redmi-10", "dbm": -41}]}',
    ]
    path = tmp_path / "router.jsonl"
    path.write_text("\n".join(rows) + "\n")
    buckets, newest = rm.read_router(str(path), T0)
    assert newest == T0 + 20
    assert [v for b in buckets.values() for v in b["rssi"]] == [-41]
    try:
        rm.rt.parse_json_line('{"ts": NaN}')
    except ValueError:
        pass
    else:
        raise AssertionError("parse_json_line accepted NaN")
    site = write_site(tmp_path, T0 + 10 * DAY)
    now = rm.rt.datetime.datetime.fromtimestamp(T0 + 20, rm.rt.datetime.timezone.utc)
    res = rm.run(str(site), str(path), now, vantage="router-station")
    assert "rssi_median_dbm" in res


def test_router_archive_keeps_each_poll_once_and_only_the_named_client(tmp_path):
    # network/router.jsonl keeps two days; the senses archive must keep every Redmi-10 poll once.
    src = tmp_path / "router.jsonl"
    def row(ts, name, dbm):
        return json.dumps({"ts": ts, "clients": [{"name": name, "dbm": dbm}]})
    src.write_text("\n".join([row(T0 + 300, "Redmi-10", -61), row(T0 + 300, "Other", -40),
                              row(T0 + 600, "Redmi-10", -63), "not json"]) + "\n")
    dest = rm.archive_router(str(tmp_path), str(src))
    assert len(open(dest).read().splitlines()) == 2
    src.write_text(src.read_text() + row(T0 + 900, "Redmi-10", -62) + "\n" + row(T0 + 600, "Redmi-10", -63) + "\n")
    rm.archive_router(str(tmp_path), str(src))
    kept = [json.loads(line) for line in open(dest).read().splitlines()]
    assert [k["ts"] for k in kept] == [T0 + 300, T0 + 600, T0 + 900]
    assert all([c["name"] for c in k["clients"]] == ["Redmi-10"] for k in kept)
    buckets, newest = rm.read_router(dest, T0)
    assert newest == T0 + 900
