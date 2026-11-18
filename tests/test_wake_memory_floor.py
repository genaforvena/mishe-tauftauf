"""The running floor of a wake or producer, read from its own cgroup leaf."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / "checks" / "wake-memory-floor.py"
FLOOR = 1610612736


def load():
    spec = importlib.util.spec_from_file_location("wake_memory_floor_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def check(monkeypatch):
    module = load()
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    return module


def leaf(tmp_path: Path, name: str, high) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "memory.high").write_text("max" if high is None else str(high), encoding="utf-8")
    return directory


def run(check, monkeypatch, leaves, capsys, floor=FLOOR):
    monkeypatch.setattr(check, "guarded_leaves", lambda root=check.PROC: leaves)
    code = check.main(["--home", "/nonexistent", "--floor", str(floor), "--persist", "0"])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_leaves_at_the_floor_are_green(check, monkeypatch, capsys, tmp_path):
    code, text = run(check, monkeypatch,
                     {"mishe-wake@health.service": leaf(tmp_path, "w", FLOOR),
                      "mishe-producer-ci.service": leaf(tmp_path, "p", FLOOR + 1)}, capsys)
    assert code == 0
    assert "GREEN 2 guarded unit(s)" in text


def test_a_leaf_below_the_floor_is_red_and_names_it(check, monkeypatch, capsys, tmp_path):
    code, text = run(check, monkeypatch,
                     {"mishe-wake@rust-plan.service": leaf(tmp_path, "w", 1073741824)}, capsys)
    assert code == 2
    assert "mishe-wake@rust-plan.service" in text
    assert "1073741824" in text and str(FLOOR) in text


def test_a_low_reading_that_recovers_is_a_repair_not_a_fault(check, monkeypatch, capsys, tmp_path):
    directory = leaf(tmp_path, "w", 1073741824)

    def repair_during_the_window(seconds):
        (directory / "memory.high").write_text(str(FLOOR), encoding="utf-8")  # the guard's write

    monkeypatch.setattr(check.time, "sleep", repair_during_the_window)
    monkeypatch.setattr(check, "guarded_leaves",
                        lambda root=check.PROC: {"mishe-wake@health.service": directory})
    code = check.main(["--home", "/nonexistent", "--floor", str(FLOOR), "--persist", "3"])
    text = capsys.readouterr().out
    assert code == 0
    assert "GREEN" in text


def test_an_unreadable_floor_is_unknown_not_green(check, monkeypatch, capsys, tmp_path):
    directory = tmp_path / "gone"  # enumerated, then gone before the read
    directory.mkdir()
    code, text = run(check, monkeypatch, {"mishe-producer-ci.service": directory}, capsys)
    assert code == 3
    assert "unreadable" in text


def test_a_non_numeric_memory_high_is_unknown(check, monkeypatch, capsys, tmp_path):
    code, text = run(check, monkeypatch,
                     {"mishe-producer-ci.service": leaf(tmp_path, "p", None)}, capsys)
    assert code == 3
    assert "unreadable" in text


def test_only_guarded_leaves_of_the_plant_slice_are_collected(tmp_path, monkeypatch):
    module = load()
    proc = tmp_path / "proc"
    proc.mkdir()
    mount = tmp_path / "cgroup"

    def proc_row(pid: str, path: str):
        d = proc / pid
        d.mkdir()
        (d / "cgroup").write_text(f"0::/user.slice/x.service/mishe-plants.slice/{path}\n", encoding="utf-8")

    proc_row("41", "mishe-wake@health.service")
    proc_row("42", "mishe-wake@health.service")           # same leaf, two processes
    proc_row("43", "mishe-producer-ci.service")
    proc_row("44", "android-body-perception.service")     # plant slice, not guarded
    proc_row("45", "mishe-wake@health.service/sub")       # nested, judged by its leaf
    other = proc / "46"
    other.mkdir()
    (other / "cgroup").write_text("0::/user.slice/other.slice/x.service\n", encoding="utf-8")
    monkeypatch.setattr(module, "cgroup_root", lambda: mount)

    leaves = module.guarded_leaves(proc)
    assert sorted(leaves) == ["mishe-producer-ci.service", "mishe-wake@health.service"]
    assert leaves["mishe-wake@health.service"] == mount / "user.slice/x.service/mishe-plants.slice/mishe-wake@health.service"
