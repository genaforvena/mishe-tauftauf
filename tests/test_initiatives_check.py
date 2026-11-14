"""Controlled-failure tests for checks/initiatives.py (stdlib only, run from the committed check file)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "initiatives.py"
spec = importlib.util.spec_from_file_location("initiatives_check", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 1_000_000.0


def _site(tmp_path, items, roles=("health", "steward")):
    (tmp_path / "charters").mkdir()
    for role in roles:
        (tmp_path / "charters" / f"{role}.md").write_text("x", encoding="utf-8")
    if items is not None:
        (tmp_path / "initiatives.json").write_text(json.dumps({"initiatives": items}), encoding="utf-8")
    return tmp_path


def _run(home, owner, capsys):
    code = check.main(["--home", str(home), "--repo", str(home), "--owner", owner, "--now", str(NOW)])
    return code, capsys.readouterr().err


def _item(**over):
    base = {"id": "i1", "owner": "health", "goal": "g", "due": NOW - 3600, "escalation": "steward",
            "check": {"kind": "path", "path": "done.flag"}}
    base.update(over)
    return base


def test_overdue_and_not_done_is_red(tmp_path, capsys):
    code, err = _run(_site(tmp_path, [_item()]), "health", capsys)
    assert code == 2 and "i1" in err and "1.0 h past due" in err and "escalate to steward" in err


def test_done_is_green_even_when_overdue(tmp_path, capsys):
    home = _site(tmp_path, [_item()])
    (home / "done.flag").write_text("", encoding="utf-8")
    assert _run(home, "health", capsys)[0] == 0


def test_open_and_not_yet_due_is_green(tmp_path, capsys):
    assert _run(_site(tmp_path, [_item(due=NOW + 3600)]), "health", capsys)[0] == 0


def test_other_owners_items_do_not_wake_this_owner(tmp_path, capsys):
    assert _run(_site(tmp_path, [_item()]), "steward", capsys)[0] == 0


def test_missing_or_invalid_register_is_unknown(tmp_path, capsys):
    assert _run(_site(tmp_path, None), "health", capsys)[0] == 3
    (tmp_path / "initiatives.json").write_text("not json", encoding="utf-8")
    assert _run(tmp_path, "health", capsys)[0] == 3


def test_owner_without_charter_is_unknown_in_steward_duty_only(tmp_path, capsys):
    home = _site(tmp_path, [_item(owner="freeze-owner")])
    code, err = _run(home, "steward", capsys)
    assert code == 3 and "freeze-owner" in err and "no charter" in err
    assert _run(home, "health", capsys)[0] == 0


def test_unreadable_check_is_unknown_not_green(tmp_path, capsys):
    home = _site(tmp_path, [_item(check={"kind": "signal", "name": "nope", "equals": "x"})])
    assert _run(home, "health", capsys)[0] == 3
    home2 = tmp_path / "b"
    home2.mkdir()
    assert _run(_site(home2, [_item(check={"kind": "path", "path": "../etc"})]), "health", capsys)[0] == 3


def test_signal_and_json_checks(tmp_path, capsys):
    home = _site(tmp_path, [_item(id="s", check={"kind": "signal", "name": "sig", "state": "OK"}),
                            _item(id="j", check={"kind": "json", "file": "m.json", "key": ["a", "b"], "max": 5})])
    (home / "signals").mkdir()
    (home / "signals" / "sig.json").write_text(json.dumps({"state": "OK", "value": 1}), encoding="utf-8")
    (home / "m.json").write_text(json.dumps({"a": {"b": 3}}), encoding="utf-8")
    assert _run(home, "health", capsys)[0] == 0
    (home / "m.json").write_text(json.dumps({"a": {"b": 9}}), encoding="utf-8")
    code, err = _run(home, "health", capsys)
    assert code == 2 and "j" in err


def test_commit_check_finds_subject_text(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                    "--allow-empty", "-m", "footprint signal lands"], check=True)
    ok = _item(check={"kind": "commit", "grep": "footprint signal", "since": 0})
    home = _site(tmp_path, [ok])
    assert _run(home, "health", capsys)[0] == 0
    (home / "initiatives.json").write_text(json.dumps({"initiatives": [
        _item(check={"kind": "commit", "grep": "no such subject", "since": 0})]}), encoding="utf-8")
    assert _run(home, "health", capsys)[0] == 2


def test_commit_check_ignores_body_text(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                    "--allow-empty", "-m", "unrelated subject", "-m", "mentions footprint in the body only"],
                   check=True)
    home = _site(tmp_path, [_item(check={"kind": "commit", "grep": "footprint", "since": 0})])
    assert _run(home, "health", capsys)[0] == 2


# --- argv acceptance (ported from the retired checks/initiative.py drill tests) ---

def _exit_with(code):
    return [sys.executable, "-c", f"import sys; sys.exit({code})"]


def _argv_item(argv, **over):
    return _item(check={"kind": "argv", "argv": argv}, **over)


def _eval(home, argv, repo=None, timeout=None):
    check_dict = {"kind": "argv", "argv": argv}
    if timeout is not None:
        check_dict["timeout"] = timeout
    return check.evaluate(check_dict, home, repo or home)


def test_argv_done_is_done(tmp_path):
    assert _eval(tmp_path, _exit_with(0))[0] == check.DONE


def test_argv_open_is_open(tmp_path):
    assert _eval(tmp_path, _exit_with(1))[0] == check.OPEN


def test_argv_open_past_due_is_red_and_names_owner_and_escalation(tmp_path, capsys):
    home = _site(tmp_path, [_argv_item(_exit_with(1), id="pin-at-head", owner="health")])
    code, err = _run(home, "health", capsys)
    assert code == 2 and "pin-at-head" in err and "escalate to steward" in err and "1.0 h past due" in err


def test_argv_unknown_exit_status_is_unknown_not_green(tmp_path):
    status, detail = _eval(tmp_path, _exit_with(7))
    assert status == check.UNKNOWN and "exited 7" in detail


def test_argv_missing_program_is_unknown(tmp_path):
    status, detail = _eval(tmp_path, ["/nonexistent/definitely-not-a-program"])
    assert status == check.UNKNOWN and "cannot run" in detail


def test_argv_timeout_is_unknown(tmp_path):
    status, detail = _eval(tmp_path, [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
    assert status == check.UNKNOWN and "timed out" in detail


def test_argv_not_a_list_is_unknown(tmp_path):
    status, detail = _eval(tmp_path, "echo done")
    assert status == check.UNKNOWN and "argv" in detail


def test_argv_bad_timeout_is_unknown(tmp_path):
    assert _eval(tmp_path, _exit_with(0), timeout=0)[0] == check.UNKNOWN


def test_argv_home_and_repo_tokens_substituted(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "marker.txt").write_text("x", encoding="utf-8")
    probe = [sys.executable, "-c", "import os,sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)",
             "{home}/marker.txt"]
    assert _eval(home, probe)[0] == check.DONE


def test_argv_repo_token_is_the_checkout(tmp_path):
    (tmp_path / "repo-marker").write_text("x", encoding="utf-8")
    probe = [sys.executable, "-c", "import os,sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)",
             "{repo}/repo-marker"]
    assert _eval(tmp_path / "home", probe, repo=tmp_path)[0] == check.DONE


def test_argv_python_token_is_the_running_interpreter(tmp_path):
    assert _eval(tmp_path, ["{python}", "-c", "import sys; sys.exit(0)"])[0] == check.DONE


def test_argv_has_no_shell(tmp_path):
    # a shell metacharacter in one argv element is a literal argument, not a pipeline
    status, _ = _eval(tmp_path, [sys.executable, "-c", "import sys; sys.exit(0 if sys.argv[1]=='a;exit 3' else 1)",
                                 "a;exit 3"])
    assert status == check.DONE
