"""Controlled-failure tests for checks/pin-delivery.py (stdlib only, run from the committed check file)."""
import importlib.util
import json
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "pin-delivery.py"
spec = importlib.util.spec_from_file_location("pin_delivery_check", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 1_000_000.0
SHA = "a" * 40


def _site(tmp_path, records, pointer=SHA):
    (tmp_path / "work" / "releases").mkdir(parents=True)
    for i, record in enumerate(records):
        (tmp_path / "work" / "releases" / f"{i}.json").write_text(json.dumps(record), encoding="utf-8")
    if pointer:
        (tmp_path / "releases" / pointer).mkdir(parents=True)
        (tmp_path / "runtime").mkdir()
        (tmp_path / "runtime" / "current").symlink_to(tmp_path / "releases" / pointer)
    return tmp_path


def _run(home, capsys, **extra):
    argv = ["--home", str(home), "--now", str(NOW)]
    code = check.main(argv)
    return code, capsys.readouterr()


def _rec(phase, **over):
    base = {"sha": SHA, "phase": phase, "created_at": NOW - 600, "paths": ["a"]}
    base.update(over)
    return base


def test_delivered_and_pointer_agrees_is_green(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("delivered")]), capsys)
    assert code == 0 and "delivered" in out.out


def test_delivered_but_pointer_elsewhere_is_unknown(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("delivered")], pointer="b" * 40), capsys)
    assert code == 3 and "runtime/current names" in out.err


def test_failed_check_is_red_and_names_the_failing_test(tmp_path, capsys):
    rec = _rec("check-failed", check={"output": "..\nFAILED tests/test_x.py::test_y - boom\n1 failed"})
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 2 and "check-failed" in out.err and "tests/test_x.py::test_y" in out.err


def test_rolled_back_is_red_with_the_recorded_failure(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("rolled-back", failure="functional probe failed")]), capsys)
    assert code == 2 and "functional probe failed" in out.err


def test_only_the_newest_record_counts(tmp_path, capsys):
    old = _rec("check-failed", created_at=NOW - 7200)
    new = _rec("delivered", created_at=NOW - 60)
    assert _run(_site(tmp_path, [old, new]), capsys)[0] == 0


def test_running_is_green_until_stuck_then_unknown(tmp_path, capsys):
    assert _run(_site(tmp_path, [_rec("checking", created_at=NOW - 300)]), capsys)[0] == 0
    stuck = tmp_path / "b"
    stuck.mkdir()
    assert _run(_site(stuck, [_rec("checking", created_at=NOW - 7200)]), capsys)[0] == 3


def test_no_record_is_unknown(tmp_path, capsys):
    (tmp_path / "work" / "releases").mkdir(parents=True)
    assert _run(tmp_path, capsys)[0] == 3
