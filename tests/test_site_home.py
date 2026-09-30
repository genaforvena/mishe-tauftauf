"""A mis-resolved --home must not plant a stray site at an arbitrary path.

Health removed two dormant plant-shaped trees (wakes 28 and 106) created this way, and
artifacts/stray-prevention-sense-20260930T0030Z reproduces the original shape: a typo'd
workspace path plus a nested `.mishe-tauftauf-example/.mishe-tauftauf` directory passed
straight through to lock writers that created the tree first and validated never.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mishe_tauftauf import seed
from mishe_tauftauf.access import request as access_request
from mishe_tauftauf.feed import Feed


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
