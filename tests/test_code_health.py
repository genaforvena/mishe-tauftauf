import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("code_health", REPO / "checks" / "code-health.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_counts_above_baseline_are_red_per_rule():
    level, text = check.judge_counts("lint", {"F401": 3, "F841": 1}, {"F401": 2, "F841": 5})
    assert level == "RED" and "F401 2->3" in text and "F841" not in text


def test_a_new_rule_class_is_red_even_if_the_total_fell():
    level, _ = check.judge_counts("lint", {"F821": 1}, {"F401": 10})
    assert level == "RED"


def test_counts_below_baseline_are_amber_and_equal_is_green():
    assert check.judge_counts("types", {"errors": 3}, {"errors": 5})[0] == "AMBER"
    assert check.judge_counts("types", {"errors": 5}, {"errors": 5})[0] == "GREEN"


def test_coverage_tolerance_and_unknown_baseline():
    assert check.judge_coverage({"percent": 70.0}, {"percent": 70.4})[0] == "GREEN"
    assert check.judge_coverage({"percent": 69.0}, {"percent": 70.4})[0] == "RED"
    assert check.judge_coverage({"percent": 75.0}, {"percent": 70.4})[0] == "AMBER"
    assert check.judge_coverage({"percent": 70.0}, {})[0] == "UNKNOWN"


def test_write_baseline_only_lowers(tmp_path):
    (tmp_path / check.BASELINE).write_text(json.dumps({"lint": {"F401": 5}, "coverage": {"percent": 60.0}}))
    check.write_baseline(tmp_path, {"lint": {"F401": 4}, "coverage": {"percent": 61.0}})
    with pytest.raises(ValueError):
        check.write_baseline(tmp_path, {"lint": {"F401": 4, "F841": 1}})
    with pytest.raises(ValueError):
        check.write_baseline(tmp_path, {"coverage": {"percent": 50.0}})
    assert json.loads((tmp_path / check.BASELINE).read_text())["lint"] == {"F401": 4}


def test_coverage_report_missing_stale_or_malformed_is_unknown(tmp_path):
    now = time.time()
    with pytest.raises(check.Unknown):
        check.read_coverage(tmp_path / "none.json", now, 9)
    report = tmp_path / "c.json"
    report.write_text("{}")
    with pytest.raises(check.Unknown):
        check.read_coverage(report, now, 9)
    report.write_text(json.dumps({"totals": {"percent_covered": 71.234}}))
    assert check.read_coverage(report, now, 9) == {"percent": 71.23}
    os.utime(report, (now - 20 * 86400,) * 2)
    with pytest.raises(check.Unknown):
        check.read_coverage(report, now, 9)


def test_missing_tool_is_unknown_not_green(tmp_path, capsys):
    (tmp_path / check.BASELINE).write_text(json.dumps({"lint": {}}))
    code = check.main(["--home", str(tmp_path), "--repo", str(tmp_path), "--only", "lint",
                       "--tools-python", str(tmp_path / "no-such-python")])
    assert code == 3 and "UNKNOWN" in capsys.readouterr().err


def test_missing_baseline_is_unknown(tmp_path, capsys):
    assert check.main(["--home", str(tmp_path), "--repo", str(tmp_path), "--only", "lint"]) == 3
    assert "baseline" in capsys.readouterr().err
