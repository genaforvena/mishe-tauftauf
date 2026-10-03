from __future__ import annotations

import curses
import json
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


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                           "access", *args], text=True, capture_output=True)


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


def test_non_string_unblocks_are_malformed_not_a_menu_crash(tmp_path):
    # A hand-edited request file may carry dict-valued unblocks. Parsing must
    # reject it rather than let the value reach the menu's string join.
    item = requested(tmp_path, "hand-edited")
    path = tmp_path / "access/requests/hand-edited.json"
    data = json.loads(path.read_text())
    data["unblocks"] = [{"path": "/etc/shadow"}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="malformed request hand-edited"):
        access.get(tmp_path, item.identity)
    # The CLI grant path hits the same join, so it must fail cleanly too.
    assert run_cli(tmp_path, "grant", "hand-edited").returncode != 0
    # A well-formed request beside it stays decidable.
    other = requested(tmp_path, "well-formed")
    assert access.decide(tmp_path, other.identity, "granted") == "granted"


def test_new_permissions_bottom_pane_gets_its_own_import_root(tmp_path, monkeypatch):
    # A pane respawned without an explicit PYTHONPATH inherits the tmux
    # server's environment and imports whatever release that server started
    # with, so a freshly created selector would run stale code until the next
    # plant. ensure() must pass its own import root when it makes the pane.
    from pathlib import Path

    from mishe_tauftauf import seed_permission_panel
    from mishe_tauftauf.runtime_source import python_search_path

    home = (tmp_path / "site").resolve()
    calls = []

    def fake_tmux(*args, check=True):
        calls.append(args)

        class R:
            returncode = 0
            stdout = b""

        if args[0] == "list-panes":
            R.stdout = b"0\n"
        elif args[0] == "display-message":
            R.stdout = b"0\n"
        return R()

    monkeypatch.setattr(seed_permission_panel, "_tmux", fake_tmux)
    monkeypatch.setattr(seed_permission_panel, "owns_session", lambda home, session: True)
    monkeypatch.setenv("PYTHONPATH", "/stale/server/root")

    assert seed_permission_panel.ensure(home, "probe") == "permissions panel ready in probe"

    expected = python_search_path(Path(seed_permission_panel.__file__).resolve().parents[1],
                                  "/stale/server/root")
    bottom = [c for c in calls if c[0] == "respawn-pane" and c[-1].endswith("permissions-shell")]
    assert list(bottom[0]) == ["respawn-pane", "-k", "-t", "probe:permissions.1", "env",
                               f"PYTHONPATH={expected}", str(home / "bin" / "permissions-shell")]


def _write_cli(home: Path, package_root: Path, body: str | None = None) -> None:
    (home / "bin").mkdir(parents=True, exist_ok=True)
    (package_root / "mishe_tauftauf").mkdir(parents=True, exist_ok=True)
    (package_root / "mishe_tauftauf" / "__init__.py").write_text("", encoding="utf-8")
    (home / "bin" / "mishe-tauftauf").write_text(
        body if body is not None else
        "#!/bin/sh\n"
        f"export PYTHONPATH={package_root}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        'exec /usr/bin/python3 -m mishe_tauftauf "$@"\n', encoding="utf-8")


def _fake_tty(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr(sys, "stdout", type("T", (), {"isatty": staticmethod(lambda: True)})())


def test_cli_package_root_keeps_a_colon_in_the_path(tmp_path):
    # ``shlex.quote`` leaves a colon unquoted, so the pinned entry must not be
    # split on ``:`` or a site installed under a colon path never reloads.
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    target = tmp_path / "releases" / "odd:name" / "src"
    _write_cli(home, target)

    assert permission_menu._cli_package_root(home) == target


def test_selector_reloads_when_the_site_cli_repins(tmp_path, monkeypatch):
    # The selector is long-lived and its pane is respawned only when created or
    # dead, so after a checked release activation it must notice that the site
    # CLI wrapper now prepends a different release and reload in place.
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, tmp_path / "releases" / "new" / "src")
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    requested(home)

    with pytest.raises(permission_menu._Restart):
        permission_menu.interactive(Screen([ord("q")]), home)


def test_selector_run_reexecs_the_repinned_cli(tmp_path, monkeypatch):
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, tmp_path / "releases" / "new" / "src")
    requested(home)
    chdirs, execs = [], []
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    _fake_tty(monkeypatch)
    monkeypatch.setattr(permission_menu, "curses", type("C", (), {
        "curs_set": staticmethod(lambda value: None),
        "wrapper": staticmethod(lambda fn, *args: fn(Screen([ord("q")]), *args)),
        "error": curses.error,
    }))
    monkeypatch.setattr(permission_menu.os, "chdir", chdirs.append)
    monkeypatch.setattr(permission_menu.os, "execv", lambda path, argv: execs.append((path, argv)))

    permission_menu.run(home)
    assert chdirs == [home]
    assert execs == [(str(home / "bin" / "mishe-tauftauf"),
                      [str(home / "bin" / "mishe-tauftauf"), "--home", str(home), "access", "menu"])]


def test_selector_run_reexecs_revoke_selector(tmp_path, monkeypatch):
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, tmp_path / "releases" / "new" / "src")
    requested(home)
    execs = []
    monkeypatch.setattr(permission_menu, "curses", type("C", (), {
        "curs_set": staticmethod(lambda value: None),
        "wrapper": staticmethod(lambda fn, *args: fn(Screen([ord("q")]), *args)),
        "error": curses.error,
    }))
    monkeypatch.setattr(permission_menu.os, "chdir", lambda path: None)
    monkeypatch.setattr(permission_menu.os, "execv", lambda path, argv: execs.append(argv))
    _fake_tty(monkeypatch)

    permission_menu.run(home, "revoked")
    assert execs[0][-1] == "revoke"


def test_selector_surfaces_a_failed_reload(tmp_path, monkeypatch):
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, tmp_path / "releases" / "new" / "src")
    requested(home)
    monkeypatch.setattr(permission_menu, "curses", type("C", (), {
        "curs_set": staticmethod(lambda value: None),
        "wrapper": staticmethod(lambda fn, *args: fn(Screen([ord("q")]), *args)),
        "error": curses.error,
    }))
    monkeypatch.setattr(permission_menu.os, "chdir", lambda path: None)

    def gone(path, argv):
        raise OSError("wrapper gone")

    monkeypatch.setattr(permission_menu.os, "execv", gone)
    _fake_tty(monkeypatch)

    with pytest.raises(ValueError, match="could not reload"):
        permission_menu.run(home)


def test_selector_stays_when_the_cli_matches_its_own_release(tmp_path, monkeypatch):
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, Path(permission_menu.__file__).resolve().parents[1])
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    requested(home)

    assert permission_menu.interactive(Screen([ord("q")]), home) == 0


def test_selector_never_restarts_for_a_wrapper_without_a_package(tmp_path, monkeypatch):
    # A custom or unparseable wrapper cannot be converged by re-executing it;
    # reloading must not loop on it.
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    _write_cli(home, tmp_path / "custom",
               body='#!/bin/sh\nexport PYTHONPATH=/a/src:/b/src\nexec python3 -m mishe_tauftauf "$@"\n')
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    requested(home)

    assert permission_menu._release_changed(home) is False
    assert permission_menu.interactive(Screen([ord("q")]), home) == 0


def test_selector_without_a_site_cli_never_restarts(tmp_path, monkeypatch):
    from mishe_tauftauf import permission_menu

    home = (tmp_path / "site").resolve()
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    requested(home)

    assert permission_menu._cli_package_root(home) is None
    assert permission_menu.interactive(Screen([ord("q")]), home) == 0
