"""disk-budget: observe root/scratch free space and owned bytes; reclaim only unreferenced releases."""
import json
import os
from collections import namedtuple

import pytest

from mishe_tauftauf import disk_budget, release_retention

Usage = namedtuple("Usage", "total used free")
GIB = disk_budget.GIB


def _setup(monkeypatch, tmp_path, root_free=100, scratch_free=100, owned=10, releases=1):
    monkeypatch.setattr(disk_budget.shutil, "disk_usage",
                        lambda p: Usage(0, 0, (root_free if p == "/" else scratch_free) * GIB))
    home = tmp_path / "repo" / "site"
    home.mkdir(parents=True)
    sizes = {str(home.parent): owned * GIB, str(home / "releases"): releases * GIB}
    (home / "releases").mkdir()
    monkeypatch.setattr(disk_budget, "_tree_bytes", lambda path, timeout=0: sizes[str(path)])
    return home


def test_green_within_limits(tmp_path, monkeypatch):
    home = _setup(monkeypatch, tmp_path)
    value, reason = disk_budget.read(home, now=1000.0)
    assert value == "GREEN" and "root_free=100GiB" in reason


@pytest.mark.parametrize("kw,needle", [
    ({"root_free": 25}, "root free 25 GiB"),
    ({"scratch_free": 5}, "free 5 GiB"),
    ({"owned": 70}, "owned 70 GiB"),
    ({"releases": 3}, "releases 3.0 GiB"),
])
def test_red_names_the_limit_crossed(tmp_path, monkeypatch, kw, needle):
    home = _setup(monkeypatch, tmp_path, **kw)
    value, reason = disk_budget.read(home, now=1000.0)
    assert value == "RED" and needle in reason


def test_unreadable_size_raises_not_green(tmp_path, monkeypatch):
    home = _setup(monkeypatch, tmp_path)
    def boom(path, timeout=0):
        raise OSError("du failed")
    monkeypatch.setattr(disk_budget, "_tree_bytes", boom)
    with pytest.raises(OSError):
        disk_budget.read(home, now=1000.0)


def test_scan_is_cached(tmp_path, monkeypatch):
    home = _setup(monkeypatch, tmp_path)
    disk_budget.read(home, now=1000.0)
    monkeypatch.setattr(disk_budget, "_tree_bytes", lambda *a, **k: pytest.fail("rescanned inside ttl"))
    disk_budget.read(home, now=1000.0 + 60)


def test_prune_keeps_live_current_and_newest(tmp_path, monkeypatch):
    home = tmp_path / "site"
    names = [f"{i:040x}" for i in range(1, 13)]
    for i, name in enumerate(names):
        wt = home / "releases" / name
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: x")
        os.utime(wt, (1000 + i, 1000 + i))
    (home / "runtime").mkdir()
    (home / "runtime" / "current").symlink_to(home / "releases" / names[0])  # oldest, pinned by the symlink
    monkeypatch.setattr(release_retention, "live_release_names", lambda h, **k: {names[1]})
    gone = []
    removed = disk_budget.prune_releases(home, keep=3, remove=lambda p: gone.append(p.name) or True)
    assert names[0] not in removed and names[1] not in removed
    assert not set(names[-3:]) & set(removed)  # newest 3 kept
    assert sorted(removed) == sorted(names[2:9])


def test_prune_does_nothing_without_a_readable_moving_pin(tmp_path, monkeypatch):
    home = tmp_path / "site"
    wt = home / "releases" / ("a" * 40)
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: x")
    monkeypatch.setattr(release_retention, "live_release_names", lambda h, **k: set())
    assert disk_budget.prune_releases(home, keep=0, remove=lambda p: True) == []
