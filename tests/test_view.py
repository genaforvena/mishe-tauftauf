import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def test_no_owned_session_instructive(tmp_path, monkeypatch):
    home = tmp_path / "site" / ".mishe-tauftauf"
    home.mkdir(parents=True)
    monkeypatch.setenv("MISHE_SEED_HOME", str(home))
    # ensure no TMUX
    monkeypatch.delenv("TMUX", raising=False)
    from mishe_tauftauf.view import attach_view

    # mock owned_socket to None by ensuring no tmux server
    # if tmux exists, owned_socket will be None for missing session
    res = attach_view(home=home, session="nope-nope")
    assert res["ok"] is False
    assert "seed.init" in res["message"] or "seed init" in res["message"] or "plant" in res["message"]


def test_unit_inside_outside_branches(tmp_path, monkeypatch):
    home = tmp_path / "site" / ".mishe-tauftauf"
    home.mkdir(parents=True)
    session = "mishe-tauftauf"
    isolated = tmp_path / "tmux" / "default"
    # fake owned_socket
    import mishe_tauftauf.view as viewmod

    monkeypatch.setattr("mishe_tauftauf.view.shutil.which", lambda x: "/usr/bin/tmux")

    fake = str(isolated)
    monkeypatch.setattr("mishe_tauftauf.tmux.owned_socket", lambda h, s: fake)

    # Outside tmux
    monkeypatch.delenv("TMUX", raising=False)
    res = viewmod.attach_view(home=home, session=session)
    assert res["ok"] is True
    assert res["action"] == "exec-attach"
    assert fake in res["message"]

    # Inside tmux - mock subprocess.run
    outer = tmp_path / "outer" / "default"
    outer.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX", f"{outer},123,0")

    calls = []

    orig_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        # display-message outer session
        if "display-message" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="plants\n", stderr="")
        if "show-options" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="prefix C-b\n", stderr="")
        if "list-windows" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "new-window" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="@1\n", stderr="")
        if "set-option" in cmd or "bind-key" in cmd or "link-window" in cmd or "list-clients" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    res2 = viewmod.attach_view(home=home, session=session, create_view=True)
    assert res2["ok"] is True
    assert res2["action"] in ("created", "reuse")

    # second call should reuse
    def fake_run2(cmd, *a, **kw):
        if "list-windows" in cmd and "plants" in " ".join(cmd):
            # pretend window already exists
            return subprocess.CompletedProcess(cmd, 0, stdout=f"@1|{fake}\n", stderr="")
        if "show-options" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="prefix C-b\n", stderr="")
        if "display-message" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="plants\n", stderr="")
        if "list-clients" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="plants\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run2)
    res3 = viewmod.attach_view(home=home, session=session, create_view=True)
    assert res3["action"] == "reuse"
    assert res3["window_id"] == "@1"


def test_view_creates_outer_window_integration(tmp_path):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not available")
    # create disposable homes with two isolated servers
    ws = tmp_path / "ws"
    ws.mkdir()
    # fake git worktree: need .git common dir? seed requires worktree; instead manually start tmux servers
    # We'll create two isolated sockets and a recorded session
    uid = os.getuid()
    outer_tmp = tmp_path / "outer_root"
    outer_tmp.mkdir()
    iso_tmp = tmp_path / "iso_root"
    iso_tmp.mkdir()
    outer_sock = str(outer_tmp / f"tmux-{uid}" / "default")
    iso_sock = str(iso_tmp / f"tmux-{uid}" / "default")
    for p in [Path(outer_sock).parent, Path(iso_sock).parent]:
        p.mkdir(parents=True, exist_ok=True)

    session = "mishe-tauftauf-int-test"
    home = tmp_path / "site" / ".mishe-tauftauf"
    home.mkdir(parents=True)
    (home / ".seed-raised").write_text(session + " abc\n")

    # Create isolated server with session
    env_iso = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")}
    env_iso["TMUX_TMPDIR"] = str(iso_tmp)
    subprocess.run(["tmux", "-S", iso_sock, "new-session", "-d", "-s", session], check=True, env=env_iso)
    subprocess.run(["tmux", "-S", iso_sock, "set-option", "-t", session, "@mishe-tauftauf-home", str(home.resolve())], check=True, env=env_iso)
    subprocess.run(["tmux", "-S", iso_sock, "set-option", "-g", "prefix", "C-a"], check=True, env=env_iso)
    subprocess.run(["tmux", "-S", iso_sock, "bind-key", "C-a", "send-prefix"], check=True, env=env_iso)

    # Create outer server with plants session
    env_outer = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")}
    subprocess.run(["tmux", "-S", outer_sock, "new-session", "-d", "-s", "plants"], check=True, env=env_outer)
    # give outer an extra pane window to test preservation
    subprocess.run(["tmux", "-S", outer_sock, "new-window", "-d", "-t", "plants:", "-n", "keepme", "sleep 100"], check=True, env=env_outer)
    subprocess.run(["tmux", "-S", outer_sock, "set-option", "-g", "prefix", "C-b"], check=True, env=env_outer)
    before_windows = subprocess.run(["tmux", "-S", outer_sock, "list-windows", "-t", "plants", "-F", "#{window_name}"], capture_output=True, text=True, env=env_outer).stdout.splitlines()

    # Call view inside tmux (outer)
    # Need to set TMUX to outer socket and default_home via env
    import mishe_tauftauf.view as viewmod

    orig_tmux = os.environ.get("TMUX")
    orig_tmpdir = os.environ.get("TMUX_TMPDIR")
    try:
        os.environ["TMUX"] = f"{outer_sock},1,0"
        # Need TMUX_TMPDIR to resolve iso socket? Use mishe_tauftauf resolution: TMUX_TMPDIR derived from home.parent.name
        # For test, home.parent is /tmp/.../site ; parent.name is "site" => not matching our iso_tmp.
        # So patch owned_socket to return iso_sock directly for integration check, then verify real list-windows.
        # Do patch after verifying attach_view handles prefix correctly
        import unittest.mock as mock

        with mock.patch("mishe_tauftauf.tmux.owned_socket", return_value=iso_sock):
            res = viewmod.attach_view(home=home, session=session, create_view=True)
            assert res["ok"] is True
            assert res["window_id"] is not None
            # second call reuses
            res2 = viewmod.attach_view(home=home, session=session, create_view=True)
            assert res2["window_id"] == res["window_id"]
            assert res2["action"] == "reuse"
        after_windows = subprocess.run(["tmux", "-S", outer_sock, "list-windows", "-t", "plants", "-F", "#{window_name}"], capture_output=True, text=True, env=env_outer).stdout.splitlines()
        # preserved keepme window
        assert "keepme" in after_windows
        assert "mishe" in after_windows
        # check existing panes preserved (no kill)
        assert set(before_windows).issubset(set(after_windows))
    finally:
        if orig_tmux is None:
            os.environ.pop("TMUX", None)
        else:
            os.environ["TMUX"] = orig_tmux
        if orig_tmpdir is None:
            os.environ.pop("TMUX_TMPDIR", None)
        else:
            os.environ["TMUX_TMPDIR"] = orig_tmpdir
        # cleanup
        subprocess.run(["tmux", "-S", iso_sock, "kill-server"], capture_output=False, env=env_iso)
        subprocess.run(["tmux", "-S", outer_sock, "kill-server"], capture_output=False, env=env_outer)
