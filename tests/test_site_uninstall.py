"""Uninstall refuses to act on anything but one exact owned site.

These tests exercise the ownership gates only — identity, foreign files,
tracked source, execute confirmation and preview purity — through the same
inspect() path a real removal uses. They deliberately do not execute a
removal; the destructive path is exercised against a planted fixture in
docs/operating.md's documented procedure, not from the test suite.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mishe_tauftauf.site_uninstall import inspect, uninstall


def planted_site(tmp_path: Path, name: str = ".mishe-tauftauf") -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    site = repository / name
    (site / "minds").mkdir(parents=True)
    (site / "health").mkdir()
    (site / "health/services.json").write_text("[]\n")
    (site / "chat.log").write_text("")
    (site / ".seed-raised").write_text("mishe-seed $123\n")
    with open(repository / ".git/info/exclude", "a") as exclude:
        exclude.write(f"/{name}\n")
    return site


def test_inspect_requires_exact_owned_site_receipt(tmp_path: Path) -> None:
    site = planted_site(tmp_path)
    (site / ".seed-raised").write_text("mishe-seed $\n")  # malformed session identity
    with pytest.raises(ValueError, match="malformed or mismatched .seed-raised"):
        inspect(site, "mishe-seed")


def test_inspect_refuses_foreign_owned_content(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("ownership check needs a foreign owner; running as root cannot create one")
    site = planted_site(tmp_path)
    foreign = site / "foreign-root-file"
    foreign.write_text("data\n")
    subprocess.run(["sudo", "-n", "chown", "0:0", str(foreign)], check=False)
    if foreign.stat().st_uid == os.getuid():
        pytest.skip("no privilege path available to create a foreign-owned file")
    try:
        with pytest.raises(ValueError, match="foreign file owner"):
            inspect(site, "mishe-seed")
    finally:
        subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(foreign)], check=False)


def test_execute_requires_exact_confirm_home(tmp_path: Path) -> None:
    site = planted_site(tmp_path)
    with pytest.raises(ValueError, match="confirm-home"):
        uninstall(site, "mishe-seed", execute=True)
    with pytest.raises(ValueError, match="confirm-home"):
        uninstall(site, "mishe-seed", execute=True, confirm_home=str(site) + "/x")


def test_preview_changes_nothing(tmp_path: Path) -> None:
    site = planted_site(tmp_path)
    before = sorted(str(p.relative_to(site)) for p in site.rglob("*"))
    plan = uninstall(site, "mishe-seed")
    assert plan["status"] == "preview"
    assert plan["home"] == str(site)
    assert plan["credentials"] == []
    after = sorted(str(p.relative_to(site)) for p in site.rglob("*"))
    assert before == after


def test_preview_lists_site_credentials_for_removal(tmp_path: Path) -> None:
    site = planted_site(tmp_path)
    access = site / "pub/access"
    access.mkdir(parents=True)
    (access / "devto.env").write_text("DEVTO_API_KEY=x\n")
    plan = uninstall(site, "mishe-seed")
    assert str(access) in plan["credentials"]


def test_refuses_tracked_source_inside_site(tmp_path: Path) -> None:
    site = planted_site(tmp_path)
    repository = site.parent
    tracked = site / "tracked.txt"
    tracked.write_text("x\n")
    subprocess.run(["git", "-C", str(repository), "add", "-f", str(tracked)], check=True)
    subprocess.run(["git", "-C", str(repository),
                    "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "x"], check=True)
    with pytest.raises(ValueError, match="tracked source"):
        inspect(site, "mishe-seed")


def _private_socket_root(tmp_path: Path) -> tuple[Path, Path]:
    """The root TMUX_TMPDIR would name, and the socket inside it."""
    root = tmp_path / "tmux-root"
    socket_path = root / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(parents=True)
    return root, socket_path


def test_preview_survives_a_stale_tmux_socket_file(tmp_path: Path, monkeypatch) -> None:
    """A socket file left behind by a dead server holds no session.

    tmux keeps its socket file after the last server exits, so the file's
    existence is not evidence of a live server. The preview must read such a
    socket as empty instead of refusing to inspect it.
    """
    site = planted_site(tmp_path)
    root, socket_path = _private_socket_root(tmp_path)
    environment = dict(os.environ, TMUX_TMPDIR=str(root))
    subprocess.run(["tmux", "-S", str(socket_path), "new-session", "-d", "-s", "stale"],
                   check=True, env=environment)
    subprocess.run(["tmux", "-S", str(socket_path), "kill-server"], check=False, env=environment)
    assert socket_path.exists(), "tmux is expected to leave the socket file behind"
    monkeypatch.setenv("TMUX_TMPDIR", str(root))
    plan = uninstall(site, "mishe-seed")
    assert plan["status"] == "preview"
    assert plan["sessions"] == []


def test_preview_refuses_a_socket_that_is_not_a_tmux_server(tmp_path: Path, monkeypatch) -> None:
    """A socket that answers but is not tmux stays fail-closed."""
    site = planted_site(tmp_path)
    root, socket_path = _private_socket_root(tmp_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    stop = threading.Event()

    def serve() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            time.sleep(0.05)
            connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("TMUX_TMPDIR", str(root))
        with pytest.raises(ValueError, match="uninspectable tmux socket"):
            uninstall(site, "mishe-seed")
    finally:
        stop.set()
        server.close()
