"""The repo-change producer reads git once, keeps per-subscriber cursors and never loses a commit."""
import json
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf import repo_change_signal as rcs


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(repo: Path, path: str, subject: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(subject + "\n", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "README.md", "readme")
    _commit(root, "docs/README.md", "docs readme")
    _commit(root, "src/mishe_tauftauf/alpha.py", "add alpha")
    return root


def test_area_for_maps_known_prefixes_and_marks_unknown_as_other():
    assert rcs.area_for(["src/x.py", "docs/a.md", "weird/z"]) == ["docs", "other", "src"]


def test_is_noise_drops_chore_but_keeps_behaviour_docs_change():
    assert rcs.is_noise({"subject": "chore: bump", "areas": ["src"]})
    assert not rcs.is_noise({"subject": "docs: name the path", "areas": ["docs"]})
    assert rcs.is_noise({"subject": "fix thing", "areas": ["other"]})


def test_parse_log_drops_torn_records_and_reads_trailers():
    sha = "a" * 40
    text = (f"\x1e{sha}\x1ffix: thing\x1fbody\n\nMishe-Role: system1\x1d\n"
            "\nsrc/a.py\ndocs/b.md\n"
            "\x1etorn-without-sha\x1fsubject\x1f\n\ndocs/c.md\n")
    commits = rcs.parse_log(text)
    assert [c["sha"] for c in commits] == [sha]
    assert commits[0]["paths"] == ["src/a.py", "docs/b.md"]
    assert commits[0]["trailers"] == {"Mishe-Role": "system1"}


def test_rev_range_starts_at_oldest_cursor_so_pub_is_not_starved(repo):
    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "src/mishe_tauftauf/beta.py", "add beta")
    # pub is behind (first), docs is ahead (second): the range must include the commit after first.
    assert rcs.rev_range(repo, [first, second], 50) == [f"{first}..HEAD"]
    assert rcs.rev_range(repo, [second, first], 50) == [f"{first}..HEAD"]
    assert rcs.rev_range(repo, [None, second], 50) == ["HEAD", "--max-count=50"]


def test_pub_cursor_behind_docs_still_sees_the_commits_it_missed(repo):
    pub_cursor = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "src/mishe_tauftauf/beta.py", "add beta")
    docs_cursor = _commit(repo, "docs/recovery.md", "docs: add recovery note")
    commits = rcs.read_log(repo, rcs.rev_range(repo, [pub_cursor, docs_cursor], 50))
    report = rcs.build_report(commits, {"pub": pub_cursor, "docs": docs_cursor}, docs_cursor,
                              repo_root=repo)
    assert report["subscribers"]["pub"]["count"] == 2   # beta and the docs note
    assert report["subscribers"]["docs"]["count"] == 0  # docs consumed up to its own cursor


def test_produce_publishes_head_and_advances_only_the_named_cursor(tmp_path, repo):
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    report = rcs.produce(home, repo, now=1_000_000.0)
    assert report["summary"]["commits"] == 3   # the fixture's three commits, no cursor yet
    detail = json.loads((home / "signals" / "repo-change.detail").read_text(encoding="utf-8"))
    assert detail["head"] == report["head"]
    cursors = rcs.read_cursors(home)
    assert cursors == {}  # nothing consumed yet: a fresh subscriber sees the tail
    rcs.produce(home, repo, wake="docs", now=1_000_001.0)
    assert rcs.read_cursors(home) == {"docs": report["head"]}
    assert (home / "signals" / "repo-change-cursors.json").exists()


def test_unreadable_repo_raises_so_the_caller_can_observe_unknown(tmp_path):
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    with pytest.raises(Exception):
        rcs.produce(home, tmp_path / "not-a-repo", now=1.0)


def test_builtin_on_a_non_repo_observes_unknown_not_green(tmp_path, monkeypatch):
    from mishe_tauftauf import producers, signal_store

    site = tmp_path / "not-a-checkout" / ".site"
    (site / "signals").mkdir(parents=True)
    seen = []
    monkeypatch.setattr(signal_store, "observe_unknown",
                        lambda home, name, **kw: seen.append((name, kw["reason"])))
    monkeypatch.setattr(signal_store, "observe",
                        lambda *a, **kw: pytest.fail("a failed read must never observe a value"))
    assert producers.repo_change(site, now=1.0) is None
    assert seen and seen[0][0] == "repo-change"
    assert seen[0][1]  # the reason names the failing git call
