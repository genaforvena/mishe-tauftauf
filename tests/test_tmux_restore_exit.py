import importlib.util
import json
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("tmux_restore", Path(__file__).resolve().parent.parent / "tools" / "tmux_restore.py")
tr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tr)

REG = {"sessions": {"outer": {"socket": "/nonexistent", "session": "plants"}},
       "windows": [{"session": "outer", "name": "delivery", "restore": True, "command": ["sh", "-c", "true"]}]}


def run(tmp_path, monkeypatch, tmux_rc, registry=REG, live=None):
    reg = tmp_path / "reg.json"
    if registry is not None:
        reg.write_text(json.dumps(registry))
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    monkeypatch.setattr(tr, "observe", lambda sock, session: {} if live is None else live)
    monkeypatch.setattr(tr, "tmux", lambda sock, *a: subprocess.CompletedProcess(a, tmux_rc, "", "boom"))
    monkeypatch.setattr(tr, "restore_arguments", lambda: type("A", (), {"apply": True, "registry": str(reg)})())
    return tr.main()


def test_nothing_to_restore_exits_zero(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, 0, live={"delivery": [False]}) == 0


def test_restored_window_exits_zero(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, 0) == 0


def test_failed_restore_command_exits_two(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, 1) == 2


def test_missing_or_malformed_registry_exits_three(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, 0, registry=None) == 3
    assert run(tmp_path, monkeypatch, 0, registry={"windows": []}) == 3


def test_multi_pane_registry_creates_each_pane(tmp_path, monkeypatch):
    calls = []
    registry = {"sessions": {"outer": {"socket": "/nonexistent", "session": "plants"}},
                "windows": [{"session": "outer", "name": "ruby2shell", "restore": True,
                             "panes": [{"index": 0, "command": ["tail", "-F", "{SITE}/top.log"]},
                                       {"index": 1, "command": ["tail", "-F", "{SITE}/bottom.log"]}]}]}
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps(registry))
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    monkeypatch.setattr(tr, "observe", lambda sock, session: {})
    monkeypatch.setattr(tr, "tmux", lambda sock, *a: (calls.append(a), subprocess.CompletedProcess(a, 0, "", ""))[1])
    monkeypatch.setattr(tr, "restore_arguments", lambda: type("A", (), {"apply": True, "registry": str(reg)})())
    assert tr.main() == 0
    assert any("new-window" in a for a in calls)
    assert any("split-window" in a for a in calls)
    assert any(str(tmp_path / "top.log") in a for a in calls)
    assert any(str(tmp_path / "bottom.log") in a for a in calls)
