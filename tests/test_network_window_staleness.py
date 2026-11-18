"""A network window that runs an old copy of the view must not look healthy.

The view records the running copy's fingerprint and load time in SITE/network/window.json and
re-executes itself when its own files change; the window detector (checks/network-care.py) turns a
copy that is still old more than RELOAD_GRACE_S after the code changed into RED with the exact respawn
command.  A difference within that grace is an edit racing the check, not a stale window.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
care = load(REPO / "checks" / "network-care.py", "network_care_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_under_test")


def write_state(home, fingerprint, started=None, loaded=None):
    (home / "network").mkdir(parents=True, exist_ok=True)
    state = {"pid": 1, "started": started if started is not None else time.time(), "code": "x",
             "fingerprint": fingerprint}
    if loaded is not None:
        state["loaded"] = loaded
    (home / "network" / "window.json").write_text(json.dumps(state))


def fake_repo(tmp_path, age_s):
    """A repo whose two view files were last written age_s seconds ago, so the grace is predictable."""
    repo = tmp_path / "repo"
    stamp = time.time() - age_s
    for rel in ("tools/network_view.py", "tools/network_boundary.py"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel)
        os.utime(path, (stamp, stamp))
    return repo


def test_missing_state_is_stale(tmp_path, capsys):
    assert care.window_copy_stale(tmp_path, REPO) == 2
    assert "window.json missing" in capsys.readouterr().err


def test_copy_with_other_code_is_stale(tmp_path, capsys):
    repo = fake_repo(tmp_path, age_s=3600)
    write_state(tmp_path, "0" * 64)
    assert care.window_copy_stale(tmp_path, repo) == 2
    assert "differs from the files on disk" in capsys.readouterr().err


def test_a_just_changed_file_gets_one_reload_redraw(tmp_path, capsys):
    # the five-minute check can tick while the view is still one redraw behind an edit: not stale yet
    repo = fake_repo(tmp_path, age_s=2)
    write_state(tmp_path, "0" * 64)
    assert care.window_copy_stale(tmp_path, repo) == 0
    assert capsys.readouterr().err == ""


def test_the_same_mismatch_is_stale_after_the_grace(tmp_path, capsys):
    repo = fake_repo(tmp_path, age_s=care.RELOAD_GRACE_S + 1)
    write_state(tmp_path, "0" * 64)
    assert care.window_copy_stale(tmp_path, repo) == 2
    assert "copy loaded" in capsys.readouterr().err


def test_the_red_message_ages_the_running_copy_not_the_process(tmp_path, capsys):
    # an os.execv reload keeps the pid and kernel start time, so "started" says nothing about the code
    repo = fake_repo(tmp_path, age_s=3600)
    loaded = time.time() - 600
    write_state(tmp_path, "0" * 64, started=time.time() - 7200, loaded=loaded)
    assert care.window_copy_stale(tmp_path, repo) == 2
    err = capsys.readouterr().err
    assert time.strftime("%H:%M:%SZ", time.gmtime(loaded)) in err
    assert time.strftime("%H:%M:%SZ", time.gmtime(loaded - 7200)) not in err


def test_copy_with_the_code_on_disk_is_current(tmp_path):
    write_state(tmp_path, view.code_fingerprint(care_paths()))
    assert care.window_copy_stale(tmp_path, REPO) == 0


def test_committing_unchanged_code_is_not_stale(tmp_path):
    # the fingerprint is of content, not of commit time: a commit that changes nothing leaves the copy current
    write_state(tmp_path, view.code_fingerprint(care_paths()))
    assert care.window_copy_stale(tmp_path, REPO) == 0


def care_paths():
    return [REPO / "tools" / "network_view.py", REPO / "tools" / "network_boundary.py"]


def test_window_state_is_written_by_the_view(tmp_path, monkeypatch):
    monkeypatch.setattr(view, "WINDOW_STATE", tmp_path / "network" / "window.json")
    view.report_window_state()
    state = json.loads((tmp_path / "network" / "window.json").read_text())
    assert state["pid"] > 0 and state["started"] <= time.time()
    assert state["loaded"] == pytest.approx(view.STARTED)
    assert state["fingerprint"] == view.code_fingerprint()


def test_unchanged_own_files_do_not_reexec(monkeypatch):
    calls = []
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    stamp = view.own_stamp()
    assert view.maybe_reexec(stamp) == stamp
    assert calls == []


def test_changed_file_that_does_not_compile_is_not_run(tmp_path, monkeypatch):
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("def broken(:\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    old = (0, 0)
    assert view.maybe_reexec(old) == old  # keeps the old stamp, retried later
    assert calls == []


def test_copy_that_compiles_but_fails_at_import_is_not_run(tmp_path, monkeypatch):
    # 2026-11-18: a half-saved view compiled but its SPEED line raised at import; the pane died.
    # Compiling is not enough: the new copy must start before it may replace the running one.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("VALUE = missing_name + 1\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    assert view.maybe_reexec(old) == old  # keeps the old stamp, retried later
    assert calls == []
    assert "NameError" in view.REFUSED[0]


def test_copy_that_starts_is_run(tmp_path, monkeypatch):
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("import sys\nimport time\n\n\nif __name__ == '__main__':\n"
                    "    while True:\n        sys.stdout.write('MISHE NETWORK\\n')\n"
                    "        sys.stdout.flush()\n        time.sleep(0.05)\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", ("stale reason", 0.0))
    view.maybe_reexec((0, 0))  # a copy that starts is exec'd, so this call does not return here
    assert len(calls) == 1
    assert view.REFUSED is None


def test_a_copy_that_draws_one_frame_then_exits_is_not_run(tmp_path, monkeypatch):
    # 2026-11-18: liveness alone passes a copy cut right after its write+flush, because it prints
    # one frame and then exits.  The window would be replaced by a process that dies at once.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("import sys\n\n\nif __name__ == '__main__':\n    sys.stdout.write('MISHE NETWORK\\n')\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    assert view.maybe_reexec(old) == old
    assert calls == []
    assert "draws one frame and exits" in view.REFUSED[0]


def test_a_copy_that_dies_before_its_first_frame_keeps_its_error_line(tmp_path, monkeypatch):
    # 2026-11-18: the first-frame probe must not throw away the copy's own diagnosis.  A copy that
    # raises before it draws (outside draw()'s try) is refused with its last stderr line, as the
    # liveness probe did before the frame check.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("if __name__ == '__main__':\n    raise SystemExit('cannot write window state')\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    assert view.maybe_reexec(old) == old
    assert calls == []
    assert "exits at once" in view.REFUSED[0]
    assert "cannot write window state" in view.REFUSED[0]


def test_a_copy_that_spins_without_drawing_is_not_run(tmp_path, monkeypatch):
    # 2026-11-18: a save cut inside the redraw loop after `STAMP = maybe_reexec(STAMP)` keeps the
    # guard and the loop, imports cleanly and stays up forever, but never draws (and burns CPU).
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("if __name__ == '__main__':\n    while True:\n        pass\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    monkeypatch.setattr(view, "FIRST_FRAME_S", 0.3)
    old = (0, 0)
    assert view.maybe_reexec(old) == old
    assert calls == []
    assert "without drawing" in view.REFUSED[0]


def test_a_copy_cut_inside_its_main_guard_is_not_run(tmp_path, monkeypatch):
    # 2026-11-18, the shape the main-block check cannot see: the save stopped inside the guard, after
    # a complete statement, so the guard is present and the top level imports.  Run as the window runs
    # it (no --once) the copy prints one frame and exits; the pane would vanish the same way.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("VALUE = 1\n\n\nif __name__ == '__main__':\n    print(VALUE)\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    assert view.maybe_reexec(old) == old
    assert calls == []
    assert "exits at once" in view.REFUSED[0]


def test_a_copy_that_closes_its_stdout_before_exiting_is_not_called_a_spin(tmp_path, monkeypatch):
    # 2026-11-18: the kernel closes a dying copy's stdout a moment before the parent can reap it,
    # so end-of-stdout with proc.poll() still None is not evidence of a silent spin.  The probe must
    # wait out that exit and report the copy's own error instead of spending FIRST_FRAME_S on it.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("import os, sys, time\n\n\nif __name__ == '__main__':\n"
                    "    sys.stdout.write('1\\n')\n    sys.stdout.flush()\n"
                    "    sys.stderr.write('RuntimeError: window state unreadable\\n')\n"
                    "    sys.stderr.flush()\n    os.close(1)\n    time.sleep(0.2)\n"
                    "    raise SystemExit(1)\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    started = time.monotonic()
    assert view.maybe_reexec(old) == old
    assert time.monotonic() - started < view.FIRST_FRAME_S
    assert calls == []
    assert "exits at once" in view.REFUSED[0]
    assert "window state unreadable" in view.REFUSED[0]


def test_a_copy_truncated_before_its_main_block_is_not_run(tmp_path, monkeypatch):
    # 2026-11-18: the common half-save is a prefix of the file, not a syntax error.  A prefix that
    # ends before the main guard compiles and imports, then exits at once and takes the pane down.
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("VALUE = 1\n")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    old = (0, 0)
    assert view.maybe_reexec(old) == old
    assert calls == []
    assert "main block" in view.REFUSED[0]


def test_an_empty_copy_is_not_run(tmp_path, monkeypatch):
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("")
    (tmp_path / "boundary.py").write_text("")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr(view, "REFUSED", None)
    assert view.maybe_reexec((0, 0)) == (0, 0)
    assert calls == []
    assert "main block" in view.REFUSED[0]


def test_an_edit_back_to_the_running_stamp_clears_the_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(view, "REFUSED", ("stale reason", 0.0))
    stamp = view.own_stamp()
    assert view.maybe_reexec(stamp) == stamp
    assert view.REFUSED is None


def test_refused_line_says_the_copy_still_runs(monkeypatch):
    monkeypatch.setattr(view, "REFUSED", None)
    assert view.refused_line() == ""
    monkeypatch.setattr(view, "REFUSED", ("NameError: name 'SPEED_BASELINE_NIGHTS' is not defined", 0.0))
    line = view.refused_line()
    assert "does not start" in line and "still running the copy above" in line
    assert "SPEED_BASELINE_NIGHTS" in line


def test_refused_line_is_bounded(monkeypatch):
    monkeypatch.setattr(view, "REFUSED", ("x" * 500, 0.0))
    line = view.refused_line()
    assert len(line) <= view.REFUSED_CHARS + len("  EDIT REFUSED   the file on disk does not start ();"
                                                  " still running the copy above")
