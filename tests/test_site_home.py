"""A mis-resolved --home must not plant a stray site at an arbitrary path.

Health removed two dormant plant-shaped trees (wakes 28 and 106) created this way, and
artifacts/stray-prevention-sense-20260930T0030Z reproduces the original shape: a typo'd
workspace path plus a nested `.mishe-tauftauf-example/.mishe-tauftauf` directory passed
straight through to lock writers that created the tree first and validated never.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import seed
from mishe_tauftauf.access import request as access_request
from mishe_tauftauf.feed import Feed


@pytest.fixture(autouse=True)
def clear_inherited_site_binding(monkeypatch) -> None:
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)


def cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), *args],
                          text=True, capture_output=True, timeout=30)


def tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")
                  if ".git" not in path.parts)

def planted_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    return repository


def test_bogus_cli_homes_refuse_and_create_nothing(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    bogus = repository / ".mishe-tauftauf-example" / ".mishe-tauftauf"
    before = tree(repository)
    for argv in (
        ("handoff", "witness", "/etc/hostname"),
        ("seed", "yield", "--slug", "witness", "--wake", "352", "--file", "/etc/hostname"),
        ("seed", "tick", "--slug", "witness"),
        ("access", "request", "id-1", "--owner", "senses", "--task", "t",
         "--capability", "c", "--unblocks", "u", "--reason", "r"),
        ("pain", "read", "witness", "--launcher", "tmux", "--session", "absent"),
        ("doctor",),
    ):
        refused = cli(bogus, *argv)
        assert refused.returncode != 0, argv
        assert "site home does not exist" in refused.stderr, argv
    assert tree(repository) == before, "a refused home must not change the filesystem"


def test_bogus_library_writers_refuse_and_create_nothing(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    bogus = repository / ".mishe-tauftauf-example" / ".mishe-tauftauf"
    before = tree(repository)
    for attempt in (
        lambda: Feed(bogus).append("witness", "test body\n"),
        lambda: access_request(bogus, "id-1", "senses", "t", "c", ("u",), "r"),
        lambda: seed._lock(bogus).__enter__(),
    ):
        try:
            attempt()
            raise AssertionError("writer must refuse a non-existent site home")
        except ValueError as exc:
            assert "site home does not exist" in str(exc)
    assert tree(repository) == before, "a refused home must not change the filesystem"


def test_bogus_nested_home_is_refused_even_inside_a_repo(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    nested = repository / "checkout" / ".mishe-tauftauf"
    refused = cli(nested, "doctor")
    assert refused.returncode != 0
    assert "site home does not exist" in refused.stderr
    assert nested.exists() is False


def test_seed_home_variable_names_the_default_home(tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf.cli import default_home

    site = tmp_path / "planted" / ".mishe-tauftauf"
    monkeypatch.setenv("MISHE_SEED_HOME", str(site))
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)
    assert default_home() == site

    monkeypatch.delenv("MISHE_SEED_HOME")
    monkeypatch.setenv("MISHE_TAUFTAUF_HOME", str(site))
    assert default_home() == site, "the legacy variable still resolves"

    monkeypatch.delenv("MISHE_TAUFTAUF_HOME")
    assert default_home() == Path(".mishe-tauftauf")


def test_mind_home_binding_rejects_another_initialized_site(tmp_path: Path, monkeypatch) -> None:
    repository = planted_repository(tmp_path)
    canonical = repository / ".mishe-tauftauf"
    wrong = repository / ".mishe-other"
    assert cli(canonical, "seed", "init", "--slug", "genome").returncode == 0
    assert cli(wrong, "seed", "init", "--slug", "witness").returncode == 0
    before = tree(repository)
    monkeypatch.setenv("MISHE_SEED_HOME", str(canonical))

    refused = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(wrong),
         "append", "--source", "test", "misrouted"],
        text=True, capture_output=True, timeout=30, env=os.environ.copy())
    fresh_wrong = repository / ".mishe-typo"
    refused_init = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(fresh_wrong),
         "seed", "init", "--slug", "witness"],
        text=True, capture_output=True, timeout=30, env=os.environ.copy())
    assert refused_init.returncode != 0
    assert "does not match this mind's MISHE_SEED_HOME" in refused_init.stderr
    assert not fresh_wrong.exists()
    try:
        Feed(wrong).append("test", "misrouted")
    except ValueError as exc:
        assert "does not match this mind's MISHE_SEED_HOME" in str(exc)
    else:
        raise AssertionError("library writer accepted a different initialized site")


def test_binding_refusal_writes_audit_record_to_bound_site(tmp_path: Path, monkeypatch) -> None:
    """A binding refusal produces a durable record in the caller's bound site.

    The record lands where the write happens (the caller's bound site), not the
    attempted foreign home, so a non-CLI writer stays visible.
    """
    import json
    repository = planted_repository(tmp_path)
    canonical = repository / ".mishe-tauftauf"
    wrong = repository / ".mishe-other"
    assert cli(canonical, "seed", "init", "--slug", "genome").returncode == 0
    assert cli(wrong, "seed", "init", "--slug", "witness").returncode == 0
    monkeypatch.setenv("MISHE_SEED_HOME", str(canonical))

    refused = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(wrong),
         "append", "--source", "test", "misrouted"],
        text=True, capture_output=True, timeout=30, env=os.environ.copy())
    assert refused.returncode == 2
    assert "does not match this mind's MISHE_SEED_HOME" in refused.stderr

    # Record exists in the caller's bound site.
    audit_path = canonical / "logs" / "binding-refusal-audit.jsonl"
    assert audit_path.is_file()
    entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    assert entries, "binding-refusal record is empty"
    entry = entries[-1]
    assert entry["attempted_home"] == str(wrong.resolve())
    assert entry["bound_home"] == str(canonical.resolve())
    assert "append" in entry["command"]
    assert "pid" in entry
    assert "ppid" in entry
    assert "uid" in entry

    # No record in the attempted foreign home.
    foreign_audit = wrong / "logs" / "binding-refusal-audit.jsonl"
    assert not foreign_audit.exists()



def test_artifact_writers_reject_uninitialized_typo_home_before_creating_children(
        tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf import delivery, session_watch

    repository = planted_repository(tmp_path)
    typo = repository / ".mishe-tauftauft"
    typo.mkdir()

    with pytest.raises(ValueError, match="not an initialized site"):
        delivery._snapshot(typo, {"identity": "fixture"})
    assert not (typo / "artifacts").exists()

    class Halt(Exception):
        pass

    monkeypatch.setattr(session_watch, "observe", lambda *_: {"available": True})
    monkeypatch.setattr(session_watch, "assess", lambda *_: {"state": "UNKNOWN", "reason": "fixture"})
    monkeypatch.setattr(session_watch, "notify", lambda *_: None)
    monkeypatch.setattr(session_watch.time, "sleep", lambda *_: (_ for _ in ()).throw(Halt()))
    with pytest.raises(ValueError, match="not an initialized site"):
        session_watch.run(typo, "session")
    assert not (typo / "health").exists()

def test_init_still_plants_a_site_directly_inside_a_worktree(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    site = repository / ".mishe-tauftauf"
    planted = cli(site, "seed", "init", "--slug", "genome")
    assert planted.returncode == 0, planted.stderr
    assert ".mishe-tauftauf/chat.log" in tree(repository)
    healthy = cli(site, "doctor")
    assert healthy.returncode == 0, healthy.stderr
    assert "PASS local plant out of Git" in healthy.stdout

    # A nested plant-home-shaped subtree is not a site the kernel will plant.
    nested = repository / ".mishe-tauftauf-example" / ".mishe-tauftauf"
    refused = cli(nested, "seed", "init", "--slug", "genome")
    assert refused.returncode != 0
    assert "must be directly inside a Git worktree" in refused.stderr
    assert not (repository / ".mishe-tauftauf-example").exists()


def test_existing_repository_root_and_typo_are_not_sites(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    site = repository / ".mishe-tauftauf"
    assert cli(site, "seed", "init", "--slug", "genome").returncode == 0
    typo = repository / ".mishe-tuftauf"
    typo.mkdir()
    before = tree(repository)
    for wrong in (repository, typo):
        for argv in (("append", "--source", "test", "misrouted"),
                     ("seed", "tick", "--slug", "genome"),
                     ("access", "request", "bad", "--owner", "senses", "--task", "t",
                      "--capability", "c", "--unblocks", "u", "--reason", "r")):
            refused = cli(wrong, *argv)
            assert refused.returncode != 0, (wrong, argv)
            assert "not an initialized site" in refused.stderr
        try:
            Feed(wrong).append("test", "misrouted")
        except ValueError as exc:
            assert "not an initialized site" in str(exc)
        else:
            raise AssertionError("library writer accepted a wrong existing home")
    assert tree(repository) == before
    assert cli(site, "append", "--source", "test", "canonical").returncode == 0


def test_existing_plant_shaped_home_outside_worktree_is_refused(tmp_path: Path) -> None:
    wrong = tmp_path / "typo-project" / ".mishe-tuftauf"
    wrong.mkdir(parents=True)
    before = tree(tmp_path)
    refused = cli(wrong, "append", "--source", "test", "misrouted")
    assert refused.returncode != 0
    assert "not an initialized site" in refused.stderr
    assert tree(tmp_path) == before


def test_creation_entrypoints_cannot_initialize_a_nested_repository_root(tmp_path: Path) -> None:
    outer = planted_repository(tmp_path)
    nested = outer / "nested"
    nested.mkdir()
    subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
    before = tree(outer)
    for argv in (("init",), ("seed", "init", "--slug", "genome")):
        refused = cli(nested, *argv)
        assert refused.returncode != 0
        assert "repository root" in refused.stderr
    assert tree(outer) == before


def test_wall_mutations_require_a_role_registered_with_the_site(tmp_path: Path) -> None:
    repository = planted_repository(tmp_path)
    site = repository / ".mishe-tauftauf"
    assert cli(site, "seed", "init", "--slug", "genome").returncode == 0
    before = (site / "chat.log").read_bytes()

    refused_dm = cli(site, "wall", "dm", "--source", "outsider", "--to", "genome",
                     "--text", "forged attribution")
    assert refused_dm.returncode == 2
    assert "not registered" in refused_dm.stderr
    assert (site / "chat.log").read_bytes() == before

    refused_wall = cli(site, "wall", "write", "--owner", "outsider",
                       "--file", str(tmp_path / "missing-notes"))
    assert refused_wall.returncode == 2
    assert "not registered" in refused_wall.stderr

    refused_yield = cli(site, "seed", "yield", "--slug", "outsider", "--wake", "1",
                        "--file", str(tmp_path / "missing-notes"))
    assert refused_yield.returncode == 2
    assert "not registered" in refused_yield.stderr
    assert not (site / "walls" / "outsider.md").exists()

    (site / "charters").mkdir(exist_ok=True)
    (site / "minds").mkdir(exist_ok=True)
    (site / "charters" / "resident.md").write_text("resident charter\n")
    launcher = site / "minds" / "resident"
    launcher.write_text("#!/bin/sh\n")
    refused_nonexec = cli(site, "wall", "dm", "--source", "resident", "--to", "genome",
                          "--text", "forged attribution")
    assert refused_nonexec.returncode == 2
    assert "not registered" in refused_nonexec.stderr
    assert (site / "chat.log").read_bytes() == before

    launcher.chmod(0o755)
    accepted = cli(site, "wall", "dm", "--source", "resident", "--to", "genome",
                   "--text", "registered attribution")
    assert accepted.returncode == 0, accepted.stderr
    assert b"registered attribution" in (site / "chat.log").read_bytes()


def test_wall_dm_accepts_known_linked_site_source(tmp_path: Path) -> None:
    """Cross-plant DM sources (<site>/<role>) are accepted when the site is linked.

    The _require_site_role guard must not block cross-plant sources — the role
    belongs to the source site, not the target site. Instead, the site prefix
    is validated against health/linked-sites.json.
    """
    repository = planted_repository(tmp_path)
    site = repository / ".mishe-tauftauf"
    assert cli(site, "seed", "init", "--slug", "genome").returncode == 0

    # Without a linked-sites registry, cross-plant sources are refused.
    refused = cli(site, "wall", "dm", "--source", "android-body/health",
                  "--to", "genome", "--text", "cross-plant message")
    assert refused.returncode == 2
    assert "not a known linked site" in refused.stderr

    # With the site in linked-sites.json, the same source is accepted.
    health_dir = site / "health"
    health_dir.mkdir(exist_ok=True)
    (health_dir / "linked-sites.json").write_text(
        json.dumps({"version": 1, "sites": [{"session": "android-body", "home": "/home/user/android-body/.mishe-tauftauf"}]}),
        encoding="utf-8")
    accepted = cli(site, "wall", "dm", "--source", "android-body/health",
                  "--to", "genome", "--text", "cross-plant message")
    assert accepted.returncode == 0, accepted.stderr
    assert b"cross-plant message" in (site / "chat.log").read_bytes()

    # An unknown site prefix is still refused.
    refused_unknown = cli(site, "wall", "dm", "--source", "unknown-site/health",
                         "--to", "genome", "--text", "forged cross-plant")
    assert refused_unknown.returncode == 2
    assert "not a known linked site" in refused_unknown.stderr

