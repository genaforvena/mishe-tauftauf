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
