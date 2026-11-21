"""house_view: plain words first, UNKNOWN when unmeasured, fits the 41 x 170 pane."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


house_predict, house_view = load("house_predict"), load("house_view")

D0 = 498_648 * 3600


def build(tmp_path, hours=96):
    (tmp_path / "features").mkdir()
    with open(tmp_path / "features" / "house-hourly.jsonl", "w") as fh:
        for i in range(hours):
            t = D0 + i * 3600
            row = {"v": 1, "t": t, "camera": {"living room": {"n": 4, "luma_mean": 50 + (i % 24) * 3, "motion": 0.02}}}
            if i % 5 == 0:
                row["camera"] = {"living room": {"n": 4, "unavailable": 4}}       # an unmeasured hour
            fh.write(json.dumps(row) + "\n")
    return D0 + hours * 3600 + 120


def test_render_fits_the_pane_names_unknown_and_never_invents_numbers(tmp_path):
    now = build(tmp_path)
    house_predict.run(tmp_path, now)
    score = json.loads((tmp_path / "features" / "predictions-score.json").read_text())
    text = house_view.render(tmp_path, now, score, house_view.store_tail(tmp_path / "features" / "house-hourly.jsonl"))
    lines = text.splitlines()
    assert len(lines) <= house_view.MAX_ROWS and max(len(l) for l in lines) <= house_view.MAX_COLS
    assert "hash chain OK" in text and "no live forecast has been scored yet" in text and "retrospective" in text
    bedroom = next(l for l in lines if l.startswith("  bedroom light") and "·" in l)
    assert "UNKNOWN" in bedroom                                            # no bedroom data at all: UNKNOWN, not 0
    assert "·" in next(l for l in lines if l.startswith("  living-room light") and "▁" in l)   # an unmeasured hour is a dot


def test_no_score_is_unknown_and_exit_3(tmp_path, capsys):
    assert house_view.main(["--home", str(tmp_path)]) == 3
    assert "UNKNOWN" in capsys.readouterr().err


def test_a_stale_scoreboard_is_flagged_and_a_broken_chain_is_loud(tmp_path):
    now = build(tmp_path)
    house_predict.run(tmp_path, now)
    score = json.loads((tmp_path / "features" / "predictions-score.json").read_text())
    score["chain"] = {"state": "broken", "bad_line": 7, "lines": 9}
    text = house_view.render(tmp_path, now + 7200, score, {})
    assert "STALE: the scoreboard is old" in text and "hash chain BROKEN at line 7" in text


def test_numbers_are_formatted_without_inventing_precision():
    f = house_view.fmt
    assert f(None) == "UNKNOWN" and f(float("nan")) == "UNKNOWN" and f(1757.77) == "1758" and f(0.015432) == "0.0154" and f(35.5) == "35.5"
