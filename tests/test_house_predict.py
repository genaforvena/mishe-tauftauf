"""house_predict: forecasts are written before the hour, built from past hours only, scored pairwise, never from absence."""
import importlib.util
import json
import math
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("house_predict", Path(__file__).resolve().parents[1] / "tools" / "house_predict.py")
hp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hp)

D0 = 498_648 * 3600            # an exact hour boundary


def cam_row(t, luma, motion=0.02):
    return {"v": 1, "t": t, "hour": "x", "camera": {"living room": {"n": 5, "luma_mean": luma, "motion": motion}}}


def make_site(tmp_path, hours, fn=None):
    fn = fn or (lambda t: 100 + 40 * math.sin((t % 86400) / 86400 * 2 * math.pi))
    (tmp_path / "features").mkdir(exist_ok=True)
    with open(tmp_path / "features" / "house-hourly.jsonl", "w") as fh:
        for i in range(hours):
            t = D0 + i * 3600
            fh.write(json.dumps(cam_row(t, fn(t))) + "\n")
    return tmp_path


def read_log(tmp_path):
    p = tmp_path / "features" / "predictions.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def score(tmp_path):
    return json.loads((tmp_path / "features" / "predictions-score.json").read_text())


def test_forecasts_are_strictly_future_from_past_hours_and_only_for_targets_with_data(tmp_path):
    make_site(tmp_path, 72)
    now = D0 + 72 * 3600 + 600                       # 10 minutes into the first hour with no row yet
    code, detail = hp.run(tmp_path, now)
    recs = read_log(tmp_path)
    assert code == 0 and detail["forecasts"] == len(recs) == 8          # 2 targets with data x 2 nets x 2 horizons
    assert all(r["k"] == "forecast" and r["t"] > r["made"] == int(now) for r in recs)
    assert {r["slug"] for r in recs} == {"lr_light", "lr_motion"}          # no forecast for targets with no data at all
    by = {(r["net"], r["h"]): r for r in recs if r["slug"] == "lr_light"}
    assert by[("persist", 1)]["t"] == D0 + 73 * 3600 and by[("how", 24)]["t"] == D0 + 96 * 3600
    last_hour_value = 100 + 40 * math.sin(((D0 + 71 * 3600) % 86400) / 86400 * 2 * math.pi)
    assert abs(by[("persist", 1)]["v"] - last_hour_value) < 1e-9
    assert by[("how", 1)]["basis"].startswith("hour-of-day")
    assert hp.verify_chain(tmp_path / "features" / "predictions.jsonl")["state"] == "ok"


def test_rerunning_inside_the_same_hour_changes_nothing(tmp_path):
    make_site(tmp_path, 72)
    hp.run(tmp_path, D0 + 72 * 3600 + 60)
    before = (tmp_path / "features" / "predictions.jsonl").read_bytes()
    code, detail = hp.run(tmp_path, D0 + 72 * 3600 + 1200)
    assert detail["forecasts"] == 0 and (tmp_path / "features" / "predictions.jsonl").read_bytes() == before


def test_a_forecast_is_scored_when_its_hour_arrives_and_both_nets_are_paired(tmp_path):
    make_site(tmp_path, 72)
    hp.run(tmp_path, D0 + 72 * 3600 + 60)             # forecasts hour 73 (h=1)
    with open(tmp_path / "features" / "house-hourly.jsonl", "a") as fh:     # rows for hours 72 and 73 arrive
        for i in (72, 73):
            t = D0 + i * 3600
            fh.write(json.dumps(cam_row(t, 130.0)) + "\n")
    code, detail = hp.run(tmp_path, D0 + 74 * 3600 + 60)
    assert detail["scored"] == 4 and detail["unobserved"] == 0            # light and motion, both nets
    outs = [r for r in read_log(tmp_path) if r["k"] == "outcome" and r["slug"] == "lr_light"]
    assert {o["net"] for o in outs} == {"persist", "how"} and all(o["state"] == "scored" and o["obs"] == 130.0 for o in outs)
    row = score(tmp_path)["live"]["lr_light|1"]
    assert row["n"] == 1 and row["mae"]["persist"] is not None and row["mae"]["how"] is not None
    assert row["verdict"].startswith("too few")


def test_an_hour_without_a_value_is_unobserved_never_an_error_of_zero(tmp_path):
    make_site(tmp_path, 72)
    hp.run(tmp_path, D0 + 72 * 3600 + 60)
    with open(tmp_path / "features" / "house-hourly.jsonl", "a") as fh:
        fh.write(json.dumps({"v": 1, "t": D0 + 72 * 3600, "camera": {"living room": {"n": 3, "unavailable": 3}}}) + "\n")
        fh.write(json.dumps({"v": 1, "t": D0 + 73 * 3600, "camera": {"living room": {"n": 3, "unavailable": 3}}}) + "\n")
    code, detail = hp.run(tmp_path, D0 + 74 * 3600 + 60)
    outs = [r for r in read_log(tmp_path) if r["k"] == "outcome"]
    assert detail["scored"] == 0 and detail["unobserved"] == 4 and all(o["state"] == "unobserved" and "err" not in o for o in outs)
    assert score(tmp_path)["live"].get("lr_light|1", {"n": 0})["n"] == 0


def test_a_stale_store_makes_no_forecast_and_exits_unknown(tmp_path):
    make_site(tmp_path, 72)
    code, detail = hp.run(tmp_path, D0 + 72 * 3600 + 5 * 3600)
    assert code == 4 and detail["forecasts"] == 0 and "no forecast was made" in detail["reason"]
    assert read_log(tmp_path) == []
    assert hp.run(tmp_path / "nowhere", D0)[0] == 4


def test_missing_history_means_no_forecast_not_a_guess(tmp_path):
    make_site(tmp_path, 2)
    code, detail = hp.run(tmp_path, D0 + 2 * 3600 + 5)
    kinds = {(r["net"], r["h"]) for r in read_log(tmp_path)}
    assert ("persist", 1) in kinds and not any(n == "how" for n, _ in kinds)      # <3 same-hour samples


def test_an_edited_or_removed_log_line_is_detected(tmp_path):
    make_site(tmp_path, 72)
    hp.run(tmp_path, D0 + 72 * 3600 + 60)
    log = tmp_path / "features" / "predictions.jsonl"
    lines = log.read_bytes().split(b"\n")
    tampered = lines[1].replace(b'"v":', b'"v":9', 1) if b'"v":' in lines[1] else lines[1] + b" "
    log.write_bytes(b"\n".join([lines[0], tampered] + lines[2:]))
    assert hp.verify_chain(log)["state"] == "broken"
    log.write_bytes(b"\n".join([lines[0]] + lines[2:]))                     # a line removed
    assert hp.verify_chain(log)["state"] == "broken" and hp.verify_chain(tmp_path / "none")["state"] == "unknown"


def test_a_lost_state_file_is_rebuilt_from_the_log_without_duplicates(tmp_path):
    make_site(tmp_path, 72)
    hp.run(tmp_path, D0 + 72 * 3600 + 60)
    with open(tmp_path / "features" / "house-hourly.jsonl", "a") as fh:
        for i in (72, 73):
            fh.write(json.dumps(cam_row(D0 + i * 3600, 120.0)) + "\n")
    hp.run(tmp_path, D0 + 74 * 3600 + 60)
    state = json.loads((tmp_path / "features" / "predictions.state.json").read_text())
    n_lines = len(read_log(tmp_path))
    (tmp_path / "features" / "predictions.state.json").unlink()
    code, detail = hp.run(tmp_path, D0 + 74 * 3600 + 120)
    assert detail["forecasts"] == 0 and len(read_log(tmp_path)) == n_lines
    rebuilt = json.loads((tmp_path / "features" / "predictions.state.json").read_text())
    assert rebuilt["agg"] == state["agg"] and rebuilt["counts"] == state["counts"] and rebuilt["last"] == state["last"]


def test_surprise_is_a_big_miss_by_a_net_with_a_record(tmp_path):
    state = hp.fresh_state()
    slot = hp.agg_slot(state, "lr_light", 1)
    slot["nets"]["how"].update(n=30, abs=30 * 2.0)                          # usual miss: 2
    hp.apply_scored(state, [{"net": "how", "slug": "lr_light", "h": 1, "t": D0, "abs_err": 9.0, "forecast": 100, "obs": 109},
                            {"net": "how", "slug": "lr_light", "h": 1, "t": D0 + 3600, "abs_err": 2.5, "forecast": 100, "obs": 102.5}])
    assert [s["t"] for s in state["surprises"]] == [D0] and state["surprises"][0]["z"] == 4.5
    fresh = hp.fresh_state()
    hp.apply_scored(fresh, [{"net": "how", "slug": "lr_light", "h": 1, "t": D0, "abs_err": 99.0, "forecast": 1, "obs": 100}])
    assert fresh["surprises"] == []                                         # no record yet: not a surprise


def test_replay_is_walk_forward_labelled_and_finds_the_daily_rhythm(tmp_path):
    make_site(tmp_path, 24 * 12)
    code, detail = hp.run(tmp_path, D0 + 24 * 12 * 3600 + 60)
    rep = score(tmp_path)["replay"]
    day = rep["lr_light|24"]
    assert day["n"] > 100 and day["mae"]["how"] < day["mae"]["persist"] and day["verdict"] == "same-hour beats last-value"
    assert score(tmp_path)["live"] == {}                                    # replay never leaks into the live board



def test_legacy_saved_state_refreshes_and_scores_without_three_net_totals(tmp_path):
    make_site(tmp_path, 72)
    state = hp.fresh_state()
    state["agg"]["lr_light|1"] = {
        "nets": {"persist": {"n": 1, "abs": 3.0}, "how": {"n": 1, "abs": 1.0}},
        "pair": {"n": 1, "abs": {"persist": 3.0, "how": 1.0}},
        "recent": [],
    }
    (tmp_path / "features" / "predictions.state.json").write_text(json.dumps(state))

    code, detail = hp.run(tmp_path, D0 + 72 * hp.HOUR + 60)
    assert code == 0 and detail["scored"] == 0
    before = score(tmp_path)["live"]["lr_light|1"]
    assert before["n"] == 1 and before["mae"] == {"persist": 3.0, "how": 1.0}
    assert before["damp"]["n"] == 0 and before["damp"]["edge"] is None

    make_site(tmp_path, 74)
    code, detail = hp.run(tmp_path, D0 + 74 * hp.HOUR + 60)
    assert code == 0 and detail["scored"] == 4 and detail["chain"] == "ok"
    after = score(tmp_path)["live"]["lr_light|1"]
    assert after["n"] == 2 and after["damp"]["n"] == 0


def test_the_damped_net_forecasts_from_its_own_fitted_pull_and_is_logged_beside_the_baselines(tmp_path):
    make_site(tmp_path, 24 * 12)
    now = D0 + 24 * 12 * 3600 + 60
    hp.run(tmp_path, now)
    recs = [r for r in read_log(tmp_path) if r["k"] == "forecast" and r["net"] == "damp"]
    assert recs and {r["slug"] for r in recs} == {"lr_light"}     # motion is constant here: no fit, so no damped forecast
    rec = next(r for r in recs if r["slug"] == "lr_light" and r["h"] == 1)
    a = float(rec["basis"].split("=")[1])
    assert 0.0 <= a <= 1.0
    c = hp.hour_start(now)
    rows = hp.load_store(tmp_path / "features" / "house-hourly.jsonl")
    series = hp.Series({t: hp.value_of(r, "lr_light") for t, r in rows.items()})
    p, s = series.values[c - hp.HOUR], series.forecast("how", c, 1)[0]
    assert abs(rec["v"] - (p + a * (s - p))) < 1e-9               # exactly persist + a * (how - persist)


def test_the_damped_net_must_not_block_the_baselines_pair_and_old_state_slots_gain_it(tmp_path):
    state = hp.fresh_state()
    slot = hp.agg_slot(state, "lr_light", 1)
    del slot["nets"]["damp"], slot["pair"]["abs"]["damp"], slot["tri"]["abs"]["damp"]   # a slot written before the net
    hp.apply_scored(state, [{"net": "persist", "slug": "lr_light", "h": 1, "t": D0, "abs_err": 3.0, "forecast": 1, "obs": 4},
                            {"net": "how", "slug": "lr_light", "h": 1, "t": D0, "abs_err": 1.0, "forecast": 1, "obs": 4}])
    slot = hp.agg_slot(state, "lr_light", 1)
    assert slot["pair"]["n"] == 1 and slot["tri"]["n"] == 0       # baselines counted; no damp, so no tri
    assert hp.table_row(slot)["damp"]["verdict"].startswith("too few")


def test_damped_is_judged_against_the_better_baseline_with_enough_pairs_only():
    row = hp.net_row(30, {"persist": 10.0, "how": 12.0, "damp": 8.5})
    assert row["edge"] == 0.15 and row["verdict"] == "damped beats the better baseline"
    assert hp.net_row(30, {"persist": 10.0, "how": 12.0, "damp": 10.2})["verdict"] == "no difference"
    assert hp.net_row(30, {"persist": 10.0, "how": 12.0, "damp": 11.0})["verdict"] == "damped loses to the better baseline"
    assert hp.net_row(23, {"persist": 10.0, "how": 12.0, "damp": 1.0})["verdict"].startswith("too few")
    assert hp.net_row(30, {"persist": 10.0, "how": 12.0, "damp": None})["verdict"].startswith("too few")
