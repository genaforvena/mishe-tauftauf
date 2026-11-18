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


def test_a_low_reading_that_becomes_unreadable_is_unknown(check, monkeypatch, capsys, tmp_path):
    directory = leaf(tmp_path, "w", 1073741824)
    monkeypatch.setattr(check.time, "sleep", lambda seconds: (directory / "memory.high").unlink())
    monkeypatch.setattr(check, "guarded_leaves",
                        lambda root=check.PROC: {"mishe-producer-ci.service": directory})
    code = check.main(["--floor", str(FLOOR), "--persist", "3"])
    captured = capsys.readouterr()
    assert code == 3
    assert "unreadable" in captured.err


def test_a_vanished_oneshot_cgroup_is_skipped_not_unknown(check, monkeypatch, capsys, tmp_path):
    directory = tmp_path / "gone"  # enumerated from /proc, cgroup removed before the read
    code, text = run(check, monkeypatch, {"mishe-producer-ci.service": directory}, capsys)
    assert code == 0
    assert "GREEN 0 guarded unit(s)" in text


def test_present_cgroup_with_missing_memory_high_is_unknown(check, monkeypatch, capsys, tmp_path):
    directory = tmp_path / "present"
    directory.mkdir()
    code, text = run(check, monkeypatch, {"mishe-producer-ci.service": directory}, capsys)
    assert code == 3
    assert "unreadable" in text


def test_missing_file_with_unstatable_cgroup_is_unknown(check, monkeypatch, capsys, tmp_path):
    directory = tmp_path / "present"
    directory.mkdir()
    original_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == directory:
            raise PermissionError(13, "Permission denied", str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    code, text = run(check, monkeypatch, {"mishe-producer-ci.service": directory}, capsys)
    assert code == 3
    assert "unreadable" in text


def test_a_genuinely_unreadable_floor_is_unknown(check, monkeypatch, capsys, tmp_path):
    directory = leaf(tmp_path, "p", FLOOR)
    (directory / "memory.high").unlink()
    (directory / "memory.high").mkdir()  # EISDIR: present but unreadable, not vanished
    code, text = run(check, monkeypatch, {"mishe-producer-ci.service": directory}, capsys)
    assert code == 3
    assert "unreadable" in text


def test_a_vanished_oneshot_does_not_hide_a_real_low_floor(check, monkeypatch, capsys, tmp_path):
    gone = tmp_path / "gone"  # oneshot enumerated, then removed before the read
    low = leaf(tmp_path, "w", 1073741824)
    code, text = run(check, monkeypatch,
                     {"mishe-producer-ci.service": gone, "mishe-wake@health.service": low}, capsys)
    assert code == 2
    assert "mishe-wake@health.service" in text
    assert "mishe-producer-ci.service" not in text


def test_a_below_floor_oneshot_that_finishes_is_not_a_fault(check, monkeypatch, capsys, tmp_path):
    """A producer sampled below floor whose process exits during the confirmation
    window must not page health: its cgroup is gone, so no floor was violated. A
    still-running low unit keeps UNKNOWN/RED (7e87b07f, 8ad00645)."""
    directory = leaf(tmp_path, "w", 1073741824)

    def leaves_shrink(root=check.PROC):
        if leaves_shrink.calls == 0:
            leaves_shrink.calls += 1
            return {"mishe-producer-ci.service": directory}
        return {}  # the oneshot's last process exited during the window

    leaves_shrink.calls = 0

    def remove_cgroup(seconds):
        (directory / "memory.high").unlink()
        directory.rmdir()

    monkeypatch.setattr(check.time, "sleep", remove_cgroup)
    monkeypatch.setattr(check, "guarded_leaves", leaves_shrink)
    code = check.main(["--floor", str(FLOOR), "--persist", "3"])
    captured = capsys.readouterr()
    assert code == 0
    assert "GREEN" in captured.out


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


def test_a_nested_process_alone_still_maps_to_its_leaf(tmp_path, monkeypatch):
    """The nested row must not become the entry, in any /proc enumeration order.

    With only the nested process present there is no later leaf row to overwrite it,
    so a reader that stores the process's own path fails regardless of directory order
    (the promotion of a4a6e517 failed exactly that way under a different temp filesystem).
    """
    module = load()
    proc = tmp_path / "proc"
    (proc / "45").mkdir(parents=True)
    (proc / "45" / "cgroup").write_text(
        "0::/user.slice/x.service/mishe-plants.slice/mishe-wake@health.service/sub\n", encoding="utf-8")
    mount = tmp_path / "cgroup"
    monkeypatch.setattr(module, "cgroup_root", lambda: mount)

    leaves = module.guarded_leaves(proc)
    assert leaves == {"mishe-wake@health.service":
                      mount / "user.slice/x.service/mishe-plants.slice/mishe-wake@health.service"}


def test_unreadable_process_cgroup_makes_monitor_unknown(tmp_path, monkeypatch, capsys):
    module = load()
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    target = proc / "123" / "cgroup"
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError(13, "Permission denied", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(module, "cgroup_root", lambda: tmp_path / "cgroup")
    original_leaves = module.guarded_leaves
    monkeypatch.setattr(module, "guarded_leaves", lambda: original_leaves(proc))
    assert module.main(["--floor", str(FLOOR), "--persist", "0"]) == 3
    assert "123" in capsys.readouterr().err


def test_process_disappearing_during_enumeration_is_ignored(tmp_path, monkeypatch):
    module = load()
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    monkeypatch.setattr(module, "cgroup_root", lambda: tmp_path / "cgroup")
    assert module.guarded_leaves(proc) == {}
