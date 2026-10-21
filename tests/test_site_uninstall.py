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
import subprocess
import sys
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
