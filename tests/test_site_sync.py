from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coordination import site_sync


class FakeFeed:
    events: list[tuple[Path, str]] = []

    def __init__(self, home: Path):
        self.home = home

    def append(self, _source: str, body: str, *, once: bool = False):
        if not once or (self.home, body) not in self.events:
            self.events.append((self.home, body))

    def tail_sequence(self) -> int:
        return 0


def setup_sites(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    kernel = tmp_path / "kernel"
    core_home = kernel / ".mishe-seed"
    target = tmp_path / "example" / ".mishe-tauftauf"
    core_home.mkdir(parents=True)
    target.mkdir(parents=True)
    (core_home / ".seed-raised").write_text("core $1\n", encoding="utf-8")
    (target / ".seed-raised").write_text("example $2\n", encoding="utf-8")
    monkeypatch.setattr(site_sync, "KERNEL", kernel)
    monkeypatch.setattr(site_sync, "owns_session", lambda _home, _session: True)
    monkeypatch.setattr(site_sync, "Feed", FakeFeed)
    FakeFeed.events = []
    return core_home, target


def test_registered_site_syncs_once_after_green_ci(tmp_path: Path, monkeypatch) -> None:
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "a" * 40
    monkeypatch.setattr(site_sync, "_git", lambda workspace, *args: (
        sha if args == ("rev-parse", "HEAD") else
        str(workspace) if args == ("rev-parse", "--show-toplevel") else ""))
    assert site_sync.register_site(target, "example")
    site_sync._update_site(core_home, target, sha="old")
    calls = []
    child_environments = []
    monkeypatch.setattr(site_sync, "_verify", lambda home, session: calls.append(("verify", home, session)))

    def run(argv, **_kwargs):
        calls.append(("plant", argv))
        child_environments.append(_kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "plant ready", "")

    monkeypatch.setattr(site_sync.subprocess, "run", run)
    ci = {"state": "pass", "sha": sha}
    assert site_sync.sync_registered_sites(core_home, ci) == [f"synced {target.parent} {sha[:12]}"]
    assert site_sync._load(core_home)[0]["sha"] == sha
    assert [item[0] for item in calls] == ["plant", "verify"]
    assert str(site_sync.KERNEL / "src") in child_environments[0]["PYTHONPATH"].split(":")
    assert site_sync.sync_registered_sites(core_home, ci) == []
    assert [item[0] for item in calls] == ["plant", "verify"]
    assert any(body.startswith(f"[sync] core sha={sha}") for _, body in FakeFeed.events)


def test_sync_holds_dirty_target_without_planting(tmp_path: Path, monkeypatch) -> None:
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "b" * 40
    dirty = True

    def git(workspace, *args):
        if args == ("rev-parse", "HEAD"):
            return sha
        if args == ("rev-parse", "--show-toplevel"):
            return str(workspace)
        if args == ("status", "--porcelain"):
            return " M AGENTS.md" if dirty and workspace == target.parent else ""
        raise AssertionError(args)

    monkeypatch.setattr(site_sync, "_git", git)
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not plant")))
    result = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert len(result) == 1 and result[0].startswith("held ")
    assert site_sync._load(core_home)[0]["sha"] == "old"
    assert "unlanded changes" in site_sync._load(core_home)[0]["error"]
    assert any(home == target and body.startswith("[task] kernel-sync-") for home, body in FakeFeed.events)

    dirty = False
    calls = []
    monkeypatch.setattr(site_sync.subprocess, "run", lambda argv, **_kwargs: (
        calls.append(argv), subprocess.CompletedProcess(argv, 0, "plant ready", ""))[1])
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: None)
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}) == [
        f"synced {target.parent} {sha[:12]}"]
    assert len(calls) == 1
    assert any(home == target and body.startswith("[done] kernel-sync-") for home, body in FakeFeed.events)


def test_failed_live_verification_keeps_old_sha_for_retry(tmp_path: Path, monkeypatch) -> None:
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "c" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync, "_git", lambda workspace, *args: (
        sha if args == ("rev-parse", "HEAD") else
        str(workspace) if args == ("rev-parse", "--show-toplevel") else ""))

    def plant(argv, **_kwargs):
        # The planting command registers its own success before the sync check.
        site_sync._update_site(core_home, target, sha=sha)
        return subprocess.CompletedProcess(argv, 0, "plant ready", "")

    monkeypatch.setattr(site_sync.subprocess, "run", plant)
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: (_ for _ in ()).throw(RuntimeError("dead top pane")))
    result = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert result == [f"held {target.parent}: dead top pane"]
    assert site_sync._load(core_home)[0]["sha"] == "old"


def test_core_change_during_plant_keeps_old_site_sha(tmp_path: Path, monkeypatch) -> None:
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "d" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    changed = False

    def git(workspace, *args):
        if args == ("rev-parse", "HEAD"):
            return sha
        if args == ("rev-parse", "--show-toplevel"):
            return str(workspace)
        if args == ("status", "--porcelain"):
            return " M README.md" if changed and workspace == site_sync.KERNEL else ""
        raise AssertionError(args)

    def plant(argv, **_kwargs):
        nonlocal changed
        changed = True
        return subprocess.CompletedProcess(argv, 0, "plant ready", "")

    monkeypatch.setattr(site_sync, "_git", git)
    monkeypatch.setattr(site_sync.subprocess, "run", plant)
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: None)
    outcome = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert len(outcome) == 1 and "core checkout changed" in outcome[0]
    assert site_sync._load(core_home)[0]["sha"] == "old"


def test_sync_verification_rejects_dead_top_with_live_bottom(tmp_path: Path, monkeypatch) -> None:
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions", "operator"]', encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")

    def panes(*_args):
        rows = [f"{role} 0 {1 if role == 'health' else 0}\n{role} 1 0" for role in site_sync.ROLES]
        rows.append("operator 0 0")
        return subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b"")

    monkeypatch.setattr(site_sync, "_tmux", panes)
    with pytest.raises(RuntimeError, match="dead top panes"):
        site_sync._verify(target, "example")


@pytest.mark.parametrize(("advances", "feed_exists"), [(True, True), (False, True), (True, False)])
def test_sync_verifies_advancing_leases_and_allows_unrelated_window(tmp_path: Path, monkeypatch,
                                                                    advances: bool, feed_exists: bool) -> None:
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions", "operator"]', encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")
    if feed_exists:
        (target / "chat.log").write_text("", encoding="utf-8")
    names = (*site_sync.ROLES, "operator")
    count = 0
    start = datetime.now(timezone.utc) - timedelta(seconds=7)

    def tmux(*args):
        nonlocal count
        if args[0] == "list-panes":
            rows = [f"{name} 0 0" for name in names]
            rows += [f"{name} 1 0" for name in site_sync.ROLES]
            rows += ["scratch 0 0"]
            return subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b"")
        assert args[0] == "capture-pane"
        count += 1
        stamp = start + timedelta(seconds=7 if advances and count > len(site_sync.ROLES) else 0)
        frame = f"-- pane live {stamp.isoformat().replace('+00:00', 'Z')} · refresh 5s · ticks every frame --\n"
        return subprocess.CompletedProcess([], 0, frame.encode(), b"")

    monkeypatch.setattr(site_sync, "_tmux", tmux)
    monkeypatch.setattr(site_sync.time, "sleep", lambda _seconds: None)
    if advances and feed_exists:
        site_sync._verify(target, "example")
    else:
        with pytest.raises(RuntimeError, match="did not advance" if not advances else "feed is missing"):
            site_sync._verify(target, "example")


def test_follower_ignores_stale_or_malformed_ci_readings(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    sample = {"state": "pass", "sha": "a" * 40, "checked": now.isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(site_sync, "latest", lambda _home: sample)
    assert site_sync.fresh_ci(tmp_path) == sample
    sample["checked"] = (now - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    assert site_sync.fresh_ci(tmp_path) is None
    sample["checked"] = "broken"
    assert site_sync.fresh_ci(tmp_path) is None
