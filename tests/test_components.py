"""Per-component promotion: atomic pointer, check gate, rollback and the RED detector.

The builder is injected (a shell script stands in for cargo) so the tests need no
toolchain; the git repository and the committed-sha check are real.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import components as c

NAME = "tool-x"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def make_builder(payload: str):
    def builder(staging, repo, name, sha):
        out = staging / "target" / "release"
        out.mkdir(parents=True)
        binary = out / name
        binary.write_text(f"#!/bin/sh\necho {payload}\n")
        binary.chmod(0o755)
        return binary
    return builder


@pytest.fixture
def site(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    home = tmp_path / "home"; home.mkdir()
    (home / "runtime" / "current" / "bin").mkdir(parents=True)
    git(repo, "init", "-q"); git(repo, "config", "user.name", "t"); git(repo, "config", "user.email", "t@example.org")
    (repo / "f").write_text("one"); git(repo, "add", "."); git(repo, "commit", "-qm", "one")
    first = git(repo, "rev-parse", "HEAD")
    (repo / "f").write_text("two"); git(repo, "add", "."); git(repo, "commit", "-qm", "two")
    second = git(repo, "rev-parse", "HEAD")
    return repo, home, first, second


CHECK = [sys.executable, "-c", "import os,sys; sys.exit(0 if os.path.isfile(os.path.join(os.getcwd(),'bin','tool-x')) else 1)"]


def test_promote_then_rollback_moves_one_pointer_and_stays_green(site):
    repo, home, first, second = site
    r1 = c.promote(home, repo, NAME, first, check=CHECK, builder=make_builder("A"))
    assert r1["phase"] == "promoted" and r1["previous"] is None
    assert c.pointed_sha(home, NAME) == first
    assert os.readlink(c.current(home, NAME)) == os.path.join("releases", first)

    r2 = c.promote(home, repo, NAME, second, check=CHECK, builder=make_builder("B"))
    assert r2["previous"] == first and c.pointed_sha(home, NAME) == second
    assert c.verify(home, NAME)["state"] == "GREEN"

    back = c.rollback(home, NAME)
    assert back["phase"] == "rolled-back" and back["to"] == first
    assert c.pointed_sha(home, NAME) == first
    assert subprocess.check_output([str(c.current(home, NAME) / "bin" / NAME)], text=True).strip() == "A"
    assert c.verify(home, NAME)["state"] == "GREEN"


def test_failed_check_leaves_pointer_and_no_release(site):
    repo, home, first, second = site
    c.promote(home, repo, NAME, first, check=CHECK, builder=make_builder("A"))
    with pytest.raises(RuntimeError, match="check failed"):
        c.promote(home, repo, NAME, second, check=[sys.executable, "-c", "raise SystemExit(4)"],
                  builder=make_builder("B"))
    assert c.pointed_sha(home, NAME) == first
    assert not c.release_dir(home, NAME, second).exists()
    assert not any(p.name.startswith(".staging") for p in (home / "components" / NAME / "releases").iterdir())


def test_tampered_binary_turns_detector_red(site):
    repo, home, first, _ = site
    c.promote(home, repo, NAME, first, check=CHECK, builder=make_builder("A"))
    (c.release_dir(home, NAME, first) / "bin" / NAME).write_text("#!/bin/sh\necho evil\n")
    verdict = c.verify(home, NAME)
    assert verdict["state"] == "RED"
    assert any("sha256" in p for p in verdict["problems"])


def test_missing_pointer_is_red_and_runtime_copy_is_red(site):
    repo, home, first, _ = site
    assert c.verify(home, NAME)["state"] == "RED"
    c.promote(home, repo, NAME, first, check=CHECK, builder=make_builder("A"))
    (home / "runtime" / "current" / "bin" / NAME).write_text("old")
    verdict = c.verify(home, NAME)
    assert verdict["state"] == "RED"
    assert any("runtime/current" in p for p in verdict["problems"])


def test_rejects_short_sha_and_unknown_commit(site):
    repo, home, first, _ = site
    with pytest.raises(ValueError, match="40-hex"):
        c.promote(home, repo, NAME, first[:12], check=CHECK, builder=make_builder("A"))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        c.promote(home, repo, NAME, "0" * 40, check=CHECK, builder=make_builder("A"))


def test_rollback_without_previous_is_refused(site):
    repo, home, first, _ = site
    c.promote(home, repo, NAME, first, check=CHECK, builder=make_builder("A"))
    with pytest.raises(ValueError, match="no previous"):
        c.rollback(home, NAME)
