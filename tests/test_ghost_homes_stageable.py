"""The Git-visibility fact the ghost-home reading publishes beside a stray name.

A site-home literal that reaches a checkout root is not equally dangerous in
every shape: the checkout's ignore rule covers the reserved ``.mishe-*``
prefix, so a tree under it is invisible to Git and to every peer, while an
unexpanded-variable name (``$MISHE_SEED_HOME``) is not covered and a reviewer
can stage it by accident. The reading publishes which stray names Git would
stage, so a consumer does not have to run Git to learn it.

These fixtures live in their own file because the shared
``tests/test_discovery.py`` carries other roles' uncommitted work.
"""

import subprocess
from pathlib import Path


def _checkout(tmp_path: Path) -> Path:
    """A checkout root holding this site's own home, with the plant's ignore rule."""
    checkout = tmp_path / "checkout"
    (checkout / ".mishe-tauftauf").mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / ".gitignore").write_text(".mishe-*\n", encoding="utf-8")
    return checkout


def _git(checkout: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(checkout), *args], check=True,
                   capture_output=True)


def test_a_literal_variable_tree_is_named_as_stageable(tmp_path):
    """The ignore rule does not cover an unexpanded-variable name, so Git
    would stage it: the reading says so beside the ghost's name."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "t@t")
    _git(checkout, "config", "user.name", "t")
    (checkout / "$MISHE_SEED_HOME").mkdir()
    (checkout / "$MISHE_SEED_HOME" / "note.md").write_text("note\n", encoding="utf-8")

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "drift"
    assert reading["stageable"] == ["$MISHE_SEED_HOME"]
    assert "stageable=$MISHE_SEED_HOME" in reading["sample"]


def test_a_reserved_prefix_tree_is_not_named_as_stageable(tmp_path):
    """The ignore rule covers the reserved prefix, so Git never stages it: the
    reading must not name it as stageable."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "t@t")
    _git(checkout, "config", "user.name", "t")
    reserved = checkout / ".mishe-seed-home"
    reserved.mkdir()
    (reserved / "note.md").write_text("note\n", encoding="utf-8")

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["ghosts"] == [".mishe-seed-home"]
    assert reading["stageable"] == []
