"""Claude Code mind gate: hook receipts drive readiness; every failure is a problem."""
from __future__ import annotations

import json
import os

import pytest

from mishe_tauftauf import claude_gate as gate

ROLE = "field-review"


@pytest.fixture
def home(tmp_path):
    (tmp_path / "minds").mkdir()
    (tmp_path / "minds" / ROLE).write_text(f"#!/bin/sh\n# mishe-engine: claude\nexec claude\n")
    return tmp_path


def fire(home, event):
    assert gate.hook(event, home, ROLE) == 0


def idle_ready(home, command="claude", dead="0"):
    return gate.ready(home, ROLE, command, dead)


def test_lifecycle_readiness(home):
    assert not idle_ready(home)[0]                      # no SessionStart receipt: held
    fire(home, "SessionStart")
    assert idle_ready(home) == (True, "idle")
    fire(home, "UserPromptSubmit")
    assert idle_ready(home) == (False, "busy")          # prompt box visible but turn running
    fire(home, "Stop")
    assert idle_ready(home)[0]
    fire(home, "PermissionPrompt")
    assert idle_ready(home) == (False, "blocked")
    fire(home, "Stop")
    assert idle_ready(home)[0]
    fire(home, "SessionEnd")
    assert not idle_ready(home, command="zsh")[0]      # a really ended session: pane is no longer claude
    # /clear fires SessionEnd and SessionStart together, in either order; the new
    # session's receipt must survive both.
    for order in (("SessionEnd", "SessionStart"), ("SessionStart", "SessionEnd")):
        for event in order:
            fire(home, event)
        assert idle_ready(home) == (True, "idle")


def test_wrong_engine_or_dead_pane_never_ready(home):
    fire(home, "SessionStart")
    assert not idle_ready(home, command="python3")[0]
    assert not idle_ready(home, dead="1")[0]
    assert not idle_ready(home, command=None, dead=None)[0]


def test_hook_failure_is_recorded_not_swallowed(home):
    assert gate.hook("Bogus", home, ROLE) == 0
    assert "Bogus" in gate.journal(home, ROLE, "hook-errors").read_text()


def stub_panes(monkeypatch, panes):
    monkeypatch.setattr(gate, "_panes", lambda: panes)


def test_check_healthy_and_each_standing_failure(home, monkeypatch):
    fire(home, "SessionStart")
    stub_panes(monkeypatch, {ROLE: ("claude", "0")})
    assert gate.check(home)[0] == 0

    stub_panes(monkeypatch, {ROLE: ("python3", "0")})           # replaced engine
    assert gate.check(home)[0] == 2
    stub_panes(monkeypatch, {ROLE: ("claude", "1")})            # dead pane
    assert gate.check(home)[0] == 2
    stub_panes(monkeypatch, {})                                 # pane gone
    assert gate.check(home)[0] == 2
    stub_panes(monkeypatch, None)                               # monitoring blind: UNKNOWN, not OK
    assert gate.check(home)[0] == 3

    stub_panes(monkeypatch, {ROLE: ("claude", "0")})
    fire(home, "UserPromptSubmit")
    now = os.stat(gate.journal(home, ROLE, "busy")).st_mtime
    assert gate.check(home, now=now + 60)[0] == 0               # a turn in flight is fine
    code, lines = gate.check(home, now=now + gate.STALE_BUSY + 1)
    assert code == 2 and "busy" in lines[0]                     # stuck turn
    fire(home, "StopFailure")
    code, lines = gate.check(home)
    assert code == 2 and "failure" in lines[0]                  # API-error turn
    fire(home, "UserPromptSubmit")
    fire(home, "Stop")
    assert gate.check(home)[0] == 0                             # next good turn clears it
    fire(home, "PermissionPrompt")
    now = os.stat(gate.journal(home, ROLE, "blocked")).st_mtime
    assert gate.check(home, now=now + gate.STALE_BLOCKED + 1)[0] == 2
    fire(home, "Stop")
    gate.journal(home, ROLE, "alive").unlink()
    assert gate.check(home)[0] == 2                             # no SessionStart receipt


def test_partial_tmux_view_is_unknown_not_absent(home, monkeypatch):
    fire(home, "SessionStart")
    monkeypatch.setattr(gate.glob, "glob", lambda pattern: ["/s1", "/s2"])
    answers = {"/s1": "field-review\t1\tclaude\t0\n"}

    def fake(socket, *args):
        if socket not in answers:
            raise OSError("dead socket")
        return type("R", (), {"returncode": 0, "stdout": answers[socket]})()

    monkeypatch.setattr(gate, "_tmux", fake)
    assert gate._panes() is None                      # one socket unreadable: blind
    assert gate.check(home)[0] == 3                   # UNKNOWN, never "no pane"
    answers["/s2"] = "witness\t1\tomp\t0\n"
    assert gate._panes() == {"field-review": ("claude", "0"), "witness": ("omp", "0")}


def test_install_refuses_life_support_role_until_guard_recognises_engine(tmp_path, monkeypatch):
    (tmp_path / "minds").mkdir()
    old = tmp_path / "old_guard.py"
    old.write_text('LIFE_SUPPORT_ENGINES = frozenset({"omp", "codex", "opencode"})\n')
    new = tmp_path / "new_guard.py"
    new.write_text('LIFE_SUPPORT_ENGINES = frozenset({"omp", "codex", "opencode", "claude"})\n')
    monkeypatch.setattr(gate, "life_support_roles", lambda: ("genome", "health"))
    assert not gate.guard_recognises_engine(old) and gate.guard_recognises_engine(new)
    assert not gate.guard_recognises_engine(tmp_path / "missing.py")   # unreadable: refuse
    monkeypatch.setattr(gate, "guard_recognises_engine", lambda: False)
    with pytest.raises(ValueError, match="protected life-support role"):
        gate.install(tmp_path, "genome", "claude-haiku-5-5", tmp_path)
    assert not (tmp_path / "minds" / "genome").exists()                # nothing written
    gate.install(tmp_path, "senses", "claude-haiku-5-5", tmp_path)     # unprotected role unaffected
    monkeypatch.setattr(gate, "guard_recognises_engine", lambda: True)
    gate.install(tmp_path, "genome", "claude-haiku-5-5", tmp_path)


def test_no_claude_roles_is_ok_not_unknown(tmp_path):
    (tmp_path / "minds").mkdir()
    assert gate.check(tmp_path)[0] == 0


def test_install_writes_gate_probe_settings_launcher_and_backup(tmp_path):
    (tmp_path / "minds").mkdir()
    (tmp_path / "minds" / ROLE).write_text("#!/bin/sh\nexec omp\n")
    out = gate.install(tmp_path, ROLE, "claude-haiku-5-5", tmp_path)
    launcher = (tmp_path / "minds" / ROLE).read_text()
    assert "claude-haiku-5-5" in launcher and "--setting-sources project,local" in launcher
    assert (tmp_path / "backups" / f"launcher-{ROLE}-pre-claude").read_text().endswith("exec omp\n")
    config = json.loads((tmp_path / "claude-gate" / f"{ROLE}.settings.json").read_text())
    assert config["permissions"]["defaultMode"] == "dontAsk" and "Bash" in config["permissions"]["allow"]
    assert "bypassPermissions" not in launcher and "--permission-mode dontAsk" in launcher
    hooks = config["hooks"]
    assert set(hooks) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "StopFailure", "Notification"}
    assert os.access(out["probe"], os.X_OK) and os.access(out["gate"], os.X_OK)
    gate.install(tmp_path, ROLE, "claude-haiku-5-5", tmp_path)   # re-install keeps the original backup
    assert (tmp_path / "backups" / f"launcher-{ROLE}-pre-claude").read_text().endswith("exec omp\n")
