from __future__ import annotations

import curses
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import access, cli
from mishe_tauftauf.feed import Feed


def requested(home: Path, identity: str = "long-request-20260930") -> access.Request:
    home.mkdir(exist_ok=True)
    return access.request(home, identity, "discover", "research", "public-source.read",
                          ["research/source"], "Measure the selected public source.")


@pytest.mark.parametrize("verb", ["menu", "grant", "revoke"])
def test_no_id_commands_require_terminal_without_deciding(tmp_path, verb):
    item = requested(tmp_path)
    result = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(tmp_path),
                             "access", verb], text=True, capture_output=True)
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr
    assert access.state(tmp_path, item.identity) == "pending"


def test_public_permit_alias_keeps_explicit_id_support(tmp_path):
    item = requested(tmp_path)
    assert cli.main(["--home", str(tmp_path), "permit", "grant", item.identity]) == 0
    assert access.state(tmp_path, item.identity) == "granted"


def test_selector_records_one_exact_request_and_refuses_stale_scope(tmp_path):
    from mishe_tauftauf.permission_menu import decide_selected
    first = requested(tmp_path, "first")
    second = requested(tmp_path, "second")
    assert decide_selected(tmp_path, second, "pending", "granted") == "granted"
    assert access.state(tmp_path, first.identity) == "pending"
    assert access.state(tmp_path, second.identity) == "granted"
    # Another operator's revoke after display must not be overwritten by stale selection.
    access.decide(tmp_path, second.identity, "revoked")
    with pytest.raises(ValueError, match="changed"):
        decide_selected(tmp_path, second, "granted", "granted")
    path = tmp_path / "access/requests/first.json"
    path.write_text(path.read_text().replace("public-source.read", "private-source.read"))
    with pytest.raises(ValueError, match="scope changed"):
        decide_selected(tmp_path, first, "pending", "granted")
    assert access.state(tmp_path, first.identity) == "pending"


def test_generated_panel_starts_menu_and_preserves_custom_shell(tmp_path):
    from mishe_tauftauf.seed_permission_panel import write_launchers
    write_launchers(tmp_path)
    shell = tmp_path / "bin/permissions-shell"
    assert 'access menu' in shell.read_text() or 'permit menu' in shell.read_text()
    assert '--noprofile --norc' in shell.read_text()
    shell.write_text("#!/bin/sh\n# operator custom shell\nexec bash\n")
    write_launchers(tmp_path)
    assert "operator custom shell" in shell.read_text()


def test_default_access_opens_menu_and_unknown_id_suggests_exact_match(tmp_path, monkeypatch, capsys):
    from mishe_tauftauf import permission_menu
    requested(tmp_path)
    calls = []
    monkeypatch.setattr(permission_menu, "run", lambda home, decision="granted": calls.append((home, decision)) or 0)
    assert cli.main(["--home", str(tmp_path), "access"]) == 0
    assert calls == [(tmp_path, "granted")]
    assert cli.main(["--home", str(tmp_path), "access", "grant", "long-request-2026093"]) == 2
    assert "long-request-20260930" in capsys.readouterr().err


class Screen:
    def __init__(self, keys, size=(30, 100)):
        self.keys = iter(keys)
        self.size = size
        self.text = []

    def getmaxyx(self):
        return self.size

    def addnstr(self, row, col, text, count, attr):
        self.text.append(text[:count])

    def keypad(self, value):
        pass

    def timeout(self, value):
        pass

    def erase(self):
        pass

    def refresh(self):
        pass

    def getch(self):
        return next(self.keys)


def test_keyboard_selection_grants_second_then_first_without_ids(tmp_path, monkeypatch):
    from mishe_tauftauf.permission_menu import interactive
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    requested(tmp_path, "first")
    requested(tmp_path, "second")
    screen = Screen([curses.KEY_DOWN, 10, 10, ord("q")])
    assert interactive(screen, tmp_path) == 0
    grants = [entry.body for entry in Feed(tmp_path).entries()
              if entry.source == "operator/permissions"]
    assert len(grants) == 2
    assert "id=second" in grants[0]
    assert "id=first" in grants[1]
    assert "No requests to grant" in " ".join(screen.text)
    assert any("Measure the selected public source" in line for line in screen.text)


def test_quit_and_small_terminal_never_grant(tmp_path, monkeypatch):
    from mishe_tauftauf.permission_menu import interactive
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    item = requested(tmp_path)
    assert interactive(Screen([10, ord("q")], size=(8, 20)), tmp_path) == 0
    assert interactive(Screen([ord("q")]), tmp_path) == 0
    assert access.state(tmp_path, item.identity) == "pending"


def test_revocation_selector_does_not_grant_pending_request(tmp_path, monkeypatch):
    from mishe_tauftauf.permission_menu import interactive
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    pending = requested(tmp_path, "pending")
    granted = requested(tmp_path, "granted")
    access.decide(tmp_path, granted.identity, "granted")
    assert interactive(Screen([10, ord("q")]), tmp_path, "revoked") == 0
    assert access.state(tmp_path, granted.identity) == "revoked"
    assert access.state(tmp_path, pending.identity) == "pending"
