from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coordination import site_sync
from mishe_tauftauf.coordination_checks import anomalies
from mishe_tauftauf.feed import FeedEntry


class FakeFeed:
    events: list[tuple[Path, str]] = []

    def __init__(self, home: Path):
        self.home = home

    def append(self, _source: str, body: str, *, once: bool = False):
        if not once or (self.home, body) not in self.events:
            self.events.append((self.home, body))

    def tail_sequence(self) -> int:
        return 0

    def entries(self):
        return []


def test_checked_sync_uses_detached_release_and_preserves_development_dirt(tmp_path, monkeypatch):
    kernel = tmp_path / "kernel"
    package = kernel / "src/mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    subprocess.run(["git", "-C", str(kernel), "init", "-q"], check=True)
    (kernel / ".git/info/exclude").write_text("/.mishe-tauftauf/\n")
    subprocess.run(["git", "-C", str(kernel), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(kernel), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "release"], check=True)
    sha = subprocess.check_output(["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(site_sync, "KERNEL", kernel)
    home = kernel / ".mishe-tauftauf"
    # Later development must not alter the committed release bytes.
    (package / "__init__.py").write_text("unlanded development")
    release = site_sync._release_source(home, sha)
    assert release != kernel
    assert (release / "src/mishe_tauftauf/__init__.py").read_text() == ""
    assert (package / "__init__.py").read_text() == "unlanded development"
    assert site_sync._release_source(home, sha) == release
    (release / "src/mishe_tauftauf/__init__.py").write_text("corrupt release")
    with pytest.raises(ValueError, match="runtime source changed"):
        site_sync._release_source(home, sha)


def setup_sites(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    kernel = tmp_path / "kernel"
    core_home = kernel / ".mishe-seed"
    target = tmp_path / "example" / ".mishe-tauftauf"
    core_home.mkdir(parents=True)
    target.mkdir(parents=True)
    (core_home / ".seed-raised").write_text("core $1\n", encoding="utf-8")
    (target / ".seed-raised").write_text("example $2\n", encoding="utf-8")
    monkeypatch.setattr(site_sync, "_docs_only_delta", lambda _baseline, _sha: False)
    monkeypatch.setattr(site_sync, "KERNEL", kernel)
    monkeypatch.setattr(site_sync, "owns_session", lambda _home, _session: True)
    monkeypatch.setattr(site_sync, "Feed", FakeFeed)
    monkeypatch.setattr(site_sync, "_release_source", lambda _home, _sha: kernel.parent / "release")
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
    assert str(site_sync.KERNEL.parent / "release/src") in child_environments[0]["PYTHONPATH"].split(":")
    assert site_sync.sync_registered_sites(core_home, ci) == []
    assert [item[0] for item in calls] == ["plant", "verify"]
    assert any(body.startswith(f"[sync] core sha={sha}") for _, body in FakeFeed.events)



def test_docs_only_disposition_has_no_target_or_runtime_writes(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "1" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync, "_docs_only_delta", lambda _base, _sha: True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("docs-only disposition must not inspect or write the target")

    monkeypatch.setattr(site_sync, "_target_state", forbidden)
    monkeypatch.setattr(site_sync, "_release_source", forbidden)
    monkeypatch.setattr(site_sync.subprocess, "run", forbidden)
    monkeypatch.setattr(site_sync, "_verify", forbidden)
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}) == [
        f"docs-only {target.parent} {sha[:12]}"]
    row = site_sync._load(core_home)[0]
    assert row["sha"] == "old"
    assert row["observed_sha"] == sha
    assert FakeFeed.events == []


def test_docs_only_disposition_is_not_repeated(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "2" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    classifications = []
    monkeypatch.setattr(site_sync, "_docs_only_delta", lambda base, current: (
        classifications.append((base, current)), True)[1])

    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}) == []
    assert classifications == [("old", sha)]


def test_docs_only_classifier_requires_markdown_only_changes(tmp_path, monkeypatch):
    kernel = tmp_path / "kernel"
    (kernel / "docs/nested").mkdir(parents=True)
    (kernel / "src").mkdir()
    subprocess.run(["git", "-C", str(kernel), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(kernel), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(kernel), "config", "user.email", "test@example.com"], check=True)
    (kernel / "docs/nested/book.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(kernel), "add", "."], check=True)
    subprocess.run(["git", "-C", str(kernel), "commit", "-qm", "base"], check=True)
    baseline = subprocess.check_output(
        ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip()
    (kernel / "docs/nested/book.md").write_text("updated\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(kernel), "add", "."], check=True)
    subprocess.run(["git", "-C", str(kernel), "commit", "-qm", "book"], check=True)
    docs_sha = subprocess.check_output(
        ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(site_sync, "KERNEL", kernel)
    assert site_sync._docs_only_delta(baseline, docs_sha)

    (kernel / "src/runtime.py").write_text("runtime = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(kernel), "add", "."], check=True)
    subprocess.run(["git", "-C", str(kernel), "commit", "-qm", "runtime"], check=True)
    runtime_sha = subprocess.check_output(
        ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip()
    assert not site_sync._docs_only_delta(baseline, runtime_sha)
    assert not site_sync._docs_only_delta(runtime_sha, runtime_sha)

    (kernel / "src/runtime.py").rename(kernel / "docs/nested/runtime.md")
    subprocess.run(["git", "-C", str(kernel), "add", "."], check=True)
    subprocess.run(["git", "-C", str(kernel), "commit", "-qm", "rename runtime into docs"], check=True)
    rename_sha = subprocess.check_output(
        ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip()
    assert not site_sync._docs_only_delta(runtime_sha, rename_sha)

def test_runtime_paths_still_plant_and_verify(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "3" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": ""}])
    monkeypatch.setattr(site_sync, "_docs_only_delta", lambda *_args: (
        (_ for _ in ()).throw(AssertionError("empty registration SHA has no diff baseline"))))
    monkeypatch.setattr(site_sync, "_target_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(site_sync, "_git", lambda _workspace, *args: (
        sha if args == ("rev-parse", "HEAD") else ""))
    calls = []
    monkeypatch.setattr(site_sync.subprocess, "run", lambda argv, **_kwargs: (
        calls.append("plant"), subprocess.CompletedProcess(argv, 0, "ready", ""))[1])
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: calls.append("verify"))
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}) == [
        f"synced {target.parent} {sha[:12]}"]
    assert calls == ["plant", "verify"]
    assert site_sync._load(core_home)[0]["observed_sha"] == sha


def test_docs_only_after_runtime_hold_preserves_hold(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "4" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old",
                                "error": "linked site remains dirty"}])
    monkeypatch.setattr(site_sync, "_docs_only_delta", lambda _base, _sha: True)
    monkeypatch.setattr(site_sync, "_target_state", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("must not touch target"))))
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}) == [
        f"docs-only {target.parent} {sha[:12]}"]
    row = site_sync._load(core_home)[0]
    assert row["sha"] == "old"
    assert row["observed_sha"] == sha
    assert row["error"] == "linked site remains dirty"

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
            # Multi-file dirty set: the hold must name every unlanded path,
            # not just the first porcelain line (witness DM 11125).
            return (" M AGENTS.md\n?? new-tool.py\n M docs/verification.md"
                    if dirty and workspace == target.parent else "")
        raise AssertionError(args)

    monkeypatch.setattr(site_sync, "_git", git)
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not plant")))
    result = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert len(result) == 1 and result[0].startswith("held ")
    assert site_sync._load(core_home)[0]["sha"] == "old"
    error = site_sync._load(core_home)[0]["error"]
    assert "unlanded changes" in error
    for path in ("AGENTS.md", "new-tool.py", "docs/verification.md"):
        assert path in error
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


def test_replant_closes_superseded_sync_task_for_same_site(tmp_path, monkeypatch) -> None:
    """A hold at an older green sha must not leave its kernel-sync task open
    after a later sha replants the same site (witness 2026-10-11 finding)."""
    core_home, target = setup_sites(tmp_path, monkeypatch)
    old_sha = "b" * 40
    new_sha = "d" * 40
    dirty = True

    def git(workspace, *args):
        if args == ("rev-parse", "HEAD"):
            return new_sha
        if args == ("rev-parse", "--show-toplevel"):
            return str(workspace)
        if args == ("status", "--porcelain"):
            return " M AGENTS.md" if dirty and workspace == target.parent else ""
        raise AssertionError(args)

    monkeypatch.setattr(site_sync, "_git", git)
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not plant")))
    # Hold at the older green sha opens kernel-sync-<old>-<site>.
    site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": old_sha})
    held = [body.splitlines()[0] for home, body in FakeFeed.events
            if home == core_home and body.startswith("[task] kernel-sync-")]
    assert held == [f"[task] kernel-sync-{old_sha[:12]}-{target.parent.name} owner=genome source=ci"]

    # The green sha advances before the blocker clears; the replant succeeds.
    dirty = False
    monkeypatch.setattr(site_sync.subprocess, "run", lambda argv, **_kwargs: (
        subprocess.CompletedProcess(argv, 0, "plant ready", "")))
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: None)
    old_identity = f"kernel-sync-{old_sha[:12]}-{target.parent.name}"
    old_state = type("State", (), {"identity": old_identity})()
    # A different hyphenated site can share this target's terminal suffix.
    other_identity = f"kernel-sync-{old_sha[:12]}-x-{target.parent.name}"
    other_state = type("State", (), {"identity": other_identity})()
    monkeypatch.setattr(FakeFeed, "entries", lambda self: self.home)
    monkeypatch.setattr(site_sync, "task_states",
                         lambda feed_home: {old_identity: old_state, other_identity: other_state}
                         if feed_home == core_home else {})
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": new_sha}) == [
        f"synced {target.parent.name and target.parent} {new_sha[:12]}"]
    done = [body.splitlines()[0] for _, body in FakeFeed.events
            if body.startswith(f"[done] kernel-sync-{old_sha[:12]}-")]
    assert done == [f"[done] kernel-sync-{old_sha[:12]}-{target.parent.name}"]


def test_replant_closes_target_side_sync_task_after_blocker_clears(tmp_path, monkeypatch) -> None:
    core_home, target = setup_sites(tmp_path, monkeypatch)
    old_sha = "b" * 40
    new_sha = "d" * 40
    dirty = True

    def git(workspace, *args):
        if args == ("rev-parse", "HEAD"):
            return new_sha
        if args == ("rev-parse", "--show-toplevel"):
            return str(workspace)
        if args == ("status", "--porcelain"):
            return " M AGENTS.md" if dirty and workspace == target.parent else ""
        raise AssertionError(args)

    monkeypatch.setattr(site_sync, "_git", git)
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    monkeypatch.setattr(site_sync.subprocess, "run", lambda *_args, **_kwargs: (
        subprocess.CompletedProcess([], 0, "plant ready", "")))
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: None)

    # A dirty target opens the hold in both independent site feeds.
    held = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": old_sha})
    assert len(held) == 1 and held[0].startswith("held ")
    old_identity = f"kernel-sync-{old_sha[:12]}-{target.parent.name}"
    assert any(home == target and body.startswith(f"[task] {old_identity}")
               for home, body in FakeFeed.events)

    # After the blocker clears, the replant must close the target-feed task too.
    dirty = False
    old_state = type("State", (), {"identity": old_identity})()
    monkeypatch.setattr(FakeFeed, "entries", lambda self: self.home)
    monkeypatch.setattr(site_sync, "task_states",
                         lambda feed_home: {old_identity: old_state}
                         if feed_home == target else {})
    result = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": new_sha})
    assert result == [f"synced {target.parent} {new_sha[:12]}"]
    done = [body for home, body in FakeFeed.events
            if home == target and body.startswith(f"[done] {old_identity}")]
    assert len(done) == 1
    target_entries = [FeedEntry(sequence, "now", "sync", body)
                      for sequence, (_, body) in enumerate(
                          [event for event in FakeFeed.events if event[0] == target], start=1)]
    assert not any(item["kind"] == "stale-kernel-sync" for item in anomalies(target_entries))


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


def test_explicit_runtime_refresh_preserves_dirty_target_and_core(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "e" * 40
    core_dirty = False

    def git(workspace, *args):
        if args == ("rev-parse", "HEAD"):
            return sha
        if args == ("rev-parse", "--show-toplevel"):
            return str(workspace)
        if args == ("status", "--porcelain"):
            return " M application.py" if workspace == target.parent or (core_dirty and workspace == site_sync.KERNEL) else ""
        raise AssertionError(args)

    monkeypatch.setattr(site_sync, "_git", git)
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": "old"}])
    calls = []
    monkeypatch.setattr(site_sync.subprocess, "run", lambda argv, **kwargs: (
        calls.append(argv), subprocess.CompletedProcess(argv, 0, "ready", ""))[1])
    monkeypatch.setattr(site_sync, "_verify", lambda *_: None)
    result = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha}, runtime_only=True)
    assert result == [f"synced {target.parent} {sha[:12]}"]
    assert "--runtime-only" in calls[0]
    site_sync._update_site(core_home, target, sha="old", observed_sha="old")
    assert site_sync.sync_registered_sites(
        core_home, {"state": "pass", "sha": sha}, runtime_only=True) == [f"synced {target.parent} {sha[:12]}"]
    assert len(calls) == 2


def test_matching_sha_with_error_is_retried(tmp_path, monkeypatch):
    core_home, target = setup_sites(tmp_path, monkeypatch)
    sha = "f" * 40
    site_sync._save(core_home, [{"home": str(target), "session": "example", "sha": sha,
                                "error": "previous verification failed"}])
    monkeypatch.setattr(site_sync, "_git", lambda workspace, *args: (
        sha if args == ("rev-parse", "HEAD") else
        str(workspace) if args == ("rev-parse", "--show-toplevel") else ""))
    monkeypatch.setattr(site_sync.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, "ready", ""))
    checked = []
    monkeypatch.setattr(site_sync, "_verify", lambda *_: checked.append(True))
    assert site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert checked == [True]
    assert "error" not in site_sync._load(core_home)[0]


def test_release_change_during_plant_keeps_old_site_sha(tmp_path: Path, monkeypatch) -> None:
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
            return " M README.md" if changed and workspace == site_sync.KERNEL.parent / "release" else ""
        raise AssertionError(args)

    def plant(argv, **_kwargs):
        nonlocal changed
        changed = True
        return subprocess.CompletedProcess(argv, 0, "plant ready", "")

    monkeypatch.setattr(site_sync, "_git", git)
    monkeypatch.setattr(site_sync.subprocess, "run", plant)
    monkeypatch.setattr(site_sync, "_verify", lambda *_args: None)
    outcome = site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": sha})
    assert len(outcome) == 1 and "release changed" in outcome[0]
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


def test_sync_verification_rejects_dead_docs_mind_pane(tmp_path: Path, monkeypatch) -> None:
    # `docs` is a chartered, wall-addressable mind (plant.ROLES). Omitting it from
    # the verified mind panes let a site be recorded healthy with its docs mind
    # pane dead (health wake 14959).
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions", "docs", "operator"]',
        encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")
    (target / "chat.log").write_text("", encoding="utf-8")
    names = ["genome", "witness", "discover", "senses", "health", "permissions", "docs"]

    def panes(*_args):
        rows = [f"{name} 0 0" for name in (*names, "operator")]
        rows += [f"{name} 1 {1 if name == 'docs' else 0}" for name in names]
        return subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b"")

    stamps = iter(range(1000))
    monkeypatch.setattr(site_sync, "_tmux", panes)
    monkeypatch.setattr(site_sync, "_leases", lambda _session, leased: {
        name: datetime.now(timezone.utc) + timedelta(seconds=next(stamps)) for name in leased})
    with pytest.raises(RuntimeError, match="dead mind pane"):
        site_sync._verify(target, "example")


def test_sync_verifies_every_planted_mind_role() -> None:
    # The verified mind set must cover every chartered mind the plant launches;
    # a role missing here is silently unchecked on every refresh.
    from mishe_tauftauf.plant import ROLES as PLANTED

    assert set(PLANTED) <= set(site_sync.ROLES)


@pytest.mark.parametrize(("advances", "feed_exists", "delayed"), [(True, True, False), (False, True, False), (True, False, False), (True, True, True)])
def test_sync_verifies_advancing_leases_and_allows_unrelated_window(tmp_path: Path, monkeypatch,
                                                                    advances: bool, feed_exists: bool, delayed: bool) -> None:
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
        stamp = start + timedelta(seconds=7 if advances and count > (2 if delayed else 1) * len(site_sync.ROLES) else 0)
        frame = f"-- pane live {stamp.isoformat().replace('+00:00', 'Z')} · refresh 5s · ticks every frame --\n"
        return subprocess.CompletedProcess([], 0, frame.encode(), b"")

    monkeypatch.setattr(site_sync, "_tmux", tmux)
    monkeypatch.setattr(site_sync.time, "sleep", lambda _seconds: None)
    if advances and feed_exists:
        site_sync._verify(target, "example")
    else:
        with pytest.raises(RuntimeError, match="did not advance" if not advances else "feed is missing"):
            site_sync._verify(target, "example")


def test_sync_allows_one_refresh_cycle_after_a_pane_respawn(tmp_path: Path, monkeypatch) -> None:
    # A promotion or replant respawns every watcher pane; the pane it recreates
    # keeps the replaced process's footer until its own first frame lands. The
    # first lease read therefore fails while the new renderer is starting, and
    # the site must not be recorded as frozen on that evidence. The stamps below
    # model a first frame that lands 12s after the respawn: one renderer frame.
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions"]',
        encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")
    (target / "chat.log").write_text("", encoding="utf-8")
    rows = [f"{name} 0 0" for name in site_sync.ROLES]
    rows += [f"{name} 1 0" for name in site_sync.ROLES]
    monkeypatch.setattr(site_sync, "_tmux",
                        lambda *_args: subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b""))
    monkeypatch.setattr(site_sync.time, "sleep", lambda _seconds: None)
    stamps = iter([
        datetime.now(timezone.utc) - timedelta(seconds=40),  # first read: respawned pane
        datetime.now(timezone.utc) - timedelta(seconds=4),   # second read: new renderer live
        datetime.now(timezone.utc) + timedelta(seconds=2),   # advancing check
    ])
    reads = 0

    def leases(_session, leased):
        nonlocal reads
        if reads == 0:
            reads += 1
            raise RuntimeError("linked site top-pane lease is stale: example:genome age=40.0s")
        stamp = next(stamps)
        return {name: stamp for name in leased}

    monkeypatch.setattr(site_sync, "_leases", leases)
    site_sync._verify(target, "example")


def test_sync_still_fails_a_frozen_renderer_after_one_refresh_cycle(tmp_path: Path, monkeypatch) -> None:
    # The tolerance is bounded: a renderer whose footer never becomes fresh fails
    # the re-raised read on the second attempt, so the repair hides no failure.
    # The stub has to raise, because the real reader refuses any age outside
    # [0, 20] and a frozen renderer never produces a stamp it would return.
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions"]',
        encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")
    (target / "chat.log").write_text("", encoding="utf-8")
    rows = [f"{name} 0 0" for name in site_sync.ROLES]
    rows += [f"{name} 1 0" for name in site_sync.ROLES]
    monkeypatch.setattr(site_sync, "_tmux",
                        lambda *_args: subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b""))
    monkeypatch.setattr(site_sync.time, "sleep", lambda _seconds: None)
    reads = 0

    def leases(_session, leased):
        nonlocal reads
        reads += 1
        raise RuntimeError("linked site top-pane lease is stale: example:genome age=41.0s")

    monkeypatch.setattr(site_sync, "_leases", leases)
    with pytest.raises(RuntimeError, match="top-pane lease is stale"):
        site_sync._verify(target, "example")
    assert reads == 2, "a frozen renderer must be re-read once before the hold"


def test_sync_respawn_tolerance_waits_a_full_renderer_frame(tmp_path: Path, monkeypatch) -> None:
    # One frame is the bounded probe (10s, 20s for the witness role) plus its
    # retry backoff and 5s sleep, so a short grace is not enough: the recreated
    # pane can still show the replaced footer and the site would be held.
    _core_home, target = setup_sites(tmp_path, monkeypatch)
    (target / "health").mkdir()
    (target / "health" / "windows.json").write_text(
        '["genome", "witness", "discover", "senses", "health", "permissions"]',
        encoding="utf-8")
    (target / "health" / "services.json").write_text("[]", encoding="utf-8")
    (target / "chat.log").write_text("", encoding="utf-8")
    rows = [f"{name} 0 0" for name in site_sync.ROLES]
    rows += [f"{name} 1 0" for name in site_sync.ROLES]
    monkeypatch.setattr(site_sync, "_tmux",
                        lambda *_args: subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b""))
    waited: list[float] = []
    monkeypatch.setattr(site_sync.time, "sleep", lambda seconds: waited.append(seconds))
    calls = 0

    def leases(_session, leased):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("linked site top-pane lease is stale: example:genome age=41.0s")
        return {name: datetime.now(timezone.utc) + timedelta(seconds=calls) for name in leased}

    monkeypatch.setattr(site_sync, "_leases", leases)
    site_sync._verify(target, "example")
    assert waited[0] == site_sync.RENDERER_FRAME >= 27.0, waited


def test_follower_ignores_stale_or_malformed_ci_readings(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    sample = {"state": "pass", "sha": "a" * 40, "checked": now.isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(site_sync, "latest", lambda _home: sample)
    assert site_sync.fresh_ci(tmp_path) == sample
    sample["checked"] = (now - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    assert site_sync.fresh_ci(tmp_path) is None
    sample["checked"] = "broken"
    assert site_sync.fresh_ci(tmp_path) is None


def test_lease_age_is_measured_after_the_pane_is_captured(monkeypatch):
    clock = [datetime.now(timezone.utc)]
    class Clock:
        @staticmethod
        def now(zone):
            return clock[0]
        fromisoformat = staticmethod(datetime.fromisoformat)
    def capture(*args):
        clock[0] += timedelta(seconds=0.5)
        stamp = clock[0].isoformat().replace('+00:00', 'Z')
        frame = f"-- pane live {stamp} · refresh 5s · ticks every frame --\n"
        return subprocess.CompletedProcess([], 0, frame.encode(), b"")
    monkeypatch.setattr(site_sync, "datetime", Clock)
    monkeypatch.setattr(site_sync, "_tmux", capture)
    assert site_sync._leases("example", {"genome"})["genome"] == clock[0]
