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


def test_registered_ack_command_advances_the_subscriber_cursor(tmp_path, repo):
    import shlex
    from mishe_tauftauf import signal_store
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    rcs.produce(home, repo, now=1_000_000.0)
    ack = signal_store.registry(home)["repo-change"]["ack"]
    command = (ack.replace("{home}", str(home)).replace("{repo}", str(repo)).replace("{role}", "docs"))
    # Run exactly the command the dossier gives the subscriber: no separate hand-written call.
    assert rcs.main(shlex.split(command)[5:]) == 0
    assert rcs.read_cursors(home) == {"docs": rcs.head_sha(repo)}

def test_resolver_ack_exits_cleanly_and_moves_no_cursor(tmp_path, repo):
    """A declared resolver (system1) receives the same ack command; it must not fail or move a cursor."""
    import shlex
    from mishe_tauftauf import signal_store
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    rcs.produce(home, repo, now=1_000_000.0)
    ack = signal_store.registry(home)["repo-change"]["ack"]
    command = ack.replace("{home}", str(home)).replace("{repo}", str(repo)).replace("{role}", "system1")
    assert rcs.main(shlex.split(command)[5:]) == 0
    assert "system1" not in rcs.read_cursors(home)


def test_ack_registration_preserves_the_duty_policy(repo):
    from mishe_tauftauf import arm, duties, owed, producers, signal_store

    home = repo / ".site"
    source = Path(__file__).resolve().parents[1]
    duty = next(item for item in duties.declared(repo, home, source=source)
                if item["name"] == rcs.SIGNAL)
    producers.load(home, python="python3", declared=[duty])
    declared = signal_store.registry(home)[rcs.SIGNAL]
    now = 1_000_000.0
    producers.repo_change(home, now=now)
    registered = signal_store.registry(home)[rcs.SIGNAL]
    assert registered["ack"]
    assert registered == {**declared, "ack": registered["ack"]}

    now += duty["every"]
    producers.repo_change(home, now=now)
    assert signal_store.registry(home)[rcs.SIGNAL] == registered
    role = "system1"
    owed.advance(home, role, owed.check(home, role, now=now))

    deadline = now + declared["max_age"]
    assert signal_store.read(home, rcs.SIGNAL, now=deadline).state == "OK"
    assert signal_store.read(home, rcs.SIGNAL, now=deadline + 1).state == "UNKNOWN"
    assert owed.check(home, role, now=deadline + 1).reasons == []
    started = []

    def start(role_name):
        started.append(role_name)
        return True

    assert arm.due(home, now=deadline + 1, scheduler=lambda _: None, roles=[role],
                   starter=start) == [rcs.SIGNAL]
    assert signal_store.recorded(home, rcs.SIGNAL)["state"] == "UNKNOWN"
    assert started == [role]
    assert owed.check(home, role, now=deadline + 1).reasons == [f"signal:{rcs.SIGNAL}"]

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


def test_named_path_covers_behaviour_paths_outside_src():
    # duties.json and tools/checks changed behaviour in c5d47a63/e5b7a219 but got no doc naming
    # when only src/*.py was checked; README-style docs and tests are not behaviour paths.
    assert rcs.named_path("src/mishe_tauftauf/budget_producer.py")
    assert rcs.named_path("duties.json")
    assert rcs.named_path("tools/delivery_view.py")
    assert rcs.named_path("checks/wake-waste.py")
    assert not rcs.named_path("docs/outer-windows.md")
    assert not rcs.named_path("tests/test_duties.py")


def test_pub_signal_wakes_only_on_reader_meaningful_commits(tmp_path, repo):
    """Controlled drill: an internal-only commit must not wake pub; a reader-meaningful one must."""
    from mishe_tauftauf import signal_store
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    rcs.produce(home, repo, now=1_000_000.0)
    rcs.produce(home, repo, wake="pub", now=1_000_001.0)        # pub reads; nothing meaningful owed
    rev_before = signal_store.rev(home, rcs.PUB_SIGNAL)
    value_before = signal_store.recorded(home, rcs.PUB_SIGNAL)["value"]
    assert signal_store.registry(home)[rcs.PUB_SIGNAL]["subscribers"] == ["pub"]

    _commit(repo, "tools/bump.py", "chore: bump the tool")       # internal-only noise
    report = rcs.produce(home, repo, now=1_000_002.0)
    assert report["subscribers"]["pub"]["count"] == 1
    assert signal_store.rev(home, rcs.PUB_SIGNAL) == rev_before
    assert signal_store.recorded(home, rcs.PUB_SIGNAL)["value"] == value_before
    assert signal_store.rev(home, rcs.SIGNAL) > 0                  # the raw signal still moved for docs

    meaningful = _commit(repo, "src/mishe_tauftauf/beta.py", "fix: beta reads the cursor")
    rcs.produce(home, repo, now=1_000_003.0)
    assert signal_store.rev(home, rcs.PUB_SIGNAL) == rev_before + 1
    assert signal_store.recorded(home, rcs.PUB_SIGNAL)["value"] == meaningful[:12]


def test_pub_signal_turns_unknown_when_git_cannot_be_read(tmp_path, repo):
    from mishe_tauftauf import signal_store
    home = tmp_path / "home"
    (home / "signals").mkdir(parents=True)
    rcs.produce(home, repo, now=1_000_000.0)
    rcs.observe_unknown_all(home, "git unreadable", now=1_000_001.0)
    assert signal_store.read(home, rcs.PUB_SIGNAL, now=1_000_001.0).state == "UNKNOWN"
    assert signal_store.read(home, rcs.SIGNAL, now=1_000_001.0).state == "UNKNOWN"
