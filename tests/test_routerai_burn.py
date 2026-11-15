import importlib.util
import json
from pathlib import Path

from mishe_tauftauf import budget_producer as bp

CHECK = Path(__file__).resolve().parent.parent / "checks" / "routerai-burn.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("routerai_burn", CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_producer_appends_balance_only_for_successful_reads(tmp_path):
    bp.append_routerai_balance(tmp_path, 1000.0, 200.0)
    bp.append_routerai_balance(tmp_path, 2000.0, None)  # failed read: a gap, not a row
    bp.append_routerai_balance(tmp_path, 3000.0, 190.0)
    rows = [json.loads(line) for line in (tmp_path / "engine" / "routerai-balance.jsonl").read_text().splitlines()]
    assert rows == [{"epoch": 1000.0, "balance": 200.0}, {"epoch": 3000.0, "balance": 190.0}]


def test_shared_balance_cannot_establish_llm_target():
    burn = _load_check()
    now = 10 * 3600.0
    # Drops could be LLM spending, decision spending, or both.
    for hourly_drop in (200 / 24, 25, 1):
        rows = [(now - 10 * 3600 + i * 3600, 300 - hourly_drop * i) for i in range(11)]
        code, report = burn.judge(rows, now, 200, 24, 0.25, 1.0, 900)
        assert code == 3 and report["verdict"] == "UNKNOWN", report
        assert report["balance_drop_per_h"] == round(hourly_drop, 3), report
        assert "shared" in report["reason"] and "rubles" in report["reason"]


def test_missing_stale_and_topped_up_balance():
    burn = _load_check()
    now = 10 * 3600.0
    topped = [(now - 2 * 3600, 100.0), (now - 3600, 150.0), (now, 140.0)]
    code, report = burn.judge(topped, now, 200, 24, 0.25, 1.0, 900)
    assert code == 3 and report["balance_drops"] == 10.0
    assert burn.judge([], now, 200, 24, 0.25, 1.0, 900)[0] == 3
    assert burn.judge([(now - 600, 200.0), (now, 199.0)], now, 200, 24, 0.25, 1.0, 900)[0] == 3
    assert burn.judge([(now - 10 * 3600, 200.0), (now - 3 * 3600, 180.0)], now, 200, 24, 0.25, 1.0, 900)[0] == 3


def test_a_zero_limit_or_window_is_unknown_not_a_crash(tmp_path, capsys):
    # Independent review of release 20c65d73: --window-h 0 and --limit 0 divided
    # by zero, raised ZeroDivisionError and exited 1 -- a code outside the
    # check's own 0/2/3 contract, so the check emitted no verdict at all.  An
    # unusable argument proves nothing about the burn rate: it reads UNKNOWN.
    mod = _load_check()
    home = tmp_path
    (home / "engine").mkdir()
    rows = [{"epoch": 1000.0 + i * 3600, "balance": 200.0 - 8.0 * i} for i in range(4)]
    (home / "engine" / "routerai-balance.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    now = rows[-1]["epoch"] + 60

    for argv in (["--window-h", "0"], ["--limit", "0"], ["--window-h", "-1"], ["--limit", "-5"]):
        assert mod.main(["--home", str(home), "--now", str(now)] + argv) == 3, argv
        report = json.loads(capsys.readouterr().out)
        assert report["verdict"] == "UNKNOWN", report
        assert "must both be positive" in report["reason"], report

    # UNKNOWN is not RED, so the initiative mapping leaves it alone.
    assert mod.main(["--home", str(home), "--now", str(now), "--window-h", "0", "--as-initiative"]) == 3


def test_as_initiative_preserves_unknown_for_shared_balance(tmp_path, capsys):
    mod = _load_check()
    (tmp_path / "engine").mkdir()
    rows = [{"epoch": 1000.0 + i * 3600, "balance": 200.0 - 14.0 * i} for i in range(4)]
    (tmp_path / "engine" / "routerai-balance.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    now = rows[-1]["epoch"] + 60
    assert mod.main(["--home", str(tmp_path), "--now", str(now), "--as-initiative"]) == 3
    assert json.loads(capsys.readouterr().out)["verdict"] == "UNKNOWN"
    assert mod.main(["--home", str(tmp_path / "empty"), "--now", str(now), "--as-initiative"]) == 3
