"""checks/main-publication.py: publishes local main without a hook, and reports what it did."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('main_publication', REPO / 'checks/main-publication.py')
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='m', GIT_AUTHOR_EMAIL='m@x', GIT_COMMITTER_NAME='m', GIT_COMMITTER_EMAIL='m@x',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout


def clone_with_remote(tmp_path):
    remote, work = tmp_path / 'remote.git', tmp_path / 'work'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)], check=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(work)], check=True)
    git(work, 'remote', 'add', 'origin', str(remote))
    (work / 'f').write_text('1')
    git(work, 'add', 'f')
    git(work, 'commit', '-qm', 'first')
    git(work, 'push', '-q', 'origin', 'main')
    return remote, work


def commit(work, name):
    (work / name).write_text(name)
    git(work, 'add', name)
    git(work, 'commit', '-qm', name)


def commit_with_hooks_disabled(work, name):
    """Exactly the command that skipped publication: every hook off, as health did."""
    (work / name).write_text(name)
    git(work, 'add', name)
    env = dict(os.environ, GIT_AUTHOR_NAME='m', GIT_AUTHOR_EMAIL='m@x', GIT_COMMITTER_NAME='m', GIT_COMMITTER_EMAIL='m@x',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    subprocess.run(['git', '-C', str(work), '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', name],
                   check=True, capture_output=True, text=True, env=env)


def remote_head(remote):
    return git(remote, 'rev-parse', 'refs/heads/main').strip()


def test_nothing_to_push_is_green(tmp_path):
    _, work = clone_with_remote(tmp_path)
    code, lines = pub.publish(work)
    assert code == 0
    assert 'already holds HEAD' in lines[0]


def test_unpushed_commit_is_pushed_and_named(tmp_path):
    """The cause itself: commits made with every hook disabled still reach origin."""
    remote, work = clone_with_remote(tmp_path)
    commit_with_hooks_disabled(work, 'a')
    commit_with_hooks_disabled(work, 'b')
    head = git(work, 'rev-parse', 'HEAD').strip()
    assert remote_head(remote) != head

    code, lines = pub.publish(work)

    assert code == 0, lines
    assert 'pushed 2 unpushed commit(s)' in lines[0]
    assert remote_head(remote) == head
    assert 'already holds HEAD' in pub.publish(work)[1][0]


def test_diverged_main_is_red_and_not_pushed(tmp_path):
    remote, work = clone_with_remote(tmp_path)
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    git(other, 'checkout', '-q', '-B', 'main', 'origin/main')
    commit(other, 'theirs')
    git(other, 'push', '-q', 'origin', 'main')
    commit(work, 'mine')
    mine = git(work, 'rev-parse', 'HEAD').strip()
    theirs = remote_head(remote)

    code, lines = pub.publish(work)

    assert code == 2
    assert 'diverged' in lines[0]
    assert remote_head(remote) == theirs != mine


def test_local_behind_is_red_not_a_push(tmp_path):
    remote, work = clone_with_remote(tmp_path)
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    git(other, 'checkout', '-q', '-B', 'main', 'origin/main')
    commit(other, 'theirs')
    git(other, 'push', '-q', 'origin', 'main')
    theirs = remote_head(remote)

    code, lines = pub.publish(work)

    assert code == 2
    assert 'behind' in lines[0]
    assert remote_head(remote) == theirs


def test_not_on_main_publishes_nothing(tmp_path):
    remote, work = clone_with_remote(tmp_path)
    git(work, 'checkout', '-q', '-b', 'side')
    commit(work, 'side')
    before = remote_head(remote)

    code, lines = pub.publish(work)

    assert code == 0
    assert 'not main' in lines[0]
    assert remote_head(remote) == before


def test_unreadable_remote_is_unknown(tmp_path):
    _, work = clone_with_remote(tmp_path)
    git(work, 'remote', 'set-url', 'origin', str(tmp_path / 'gone.git'))

    code, lines = pub.publish(work)

    assert code == 3
    assert 'cannot read origin/main' in lines[0]


def test_a_spent_budget_is_unknown_and_names_the_step(tmp_path):
    """A stalled filesystem must end as a stated UNKNOWN, not a silent kill by the duty timeout."""
    _, work = clone_with_remote(tmp_path)
    commit_with_hooks_disabled(work, 'late')

    code, lines = pub.publish(work, deadline=time.monotonic() - 1)

    assert code == 3
    assert 'time budget exhausted before git symbolic-ref' in lines[0]


def test_duty_timeout_leaves_the_check_room_to_report():
    """The duty kills the check at `timeout`; the check's own bound must be shorter, or a run whose
    local git calls stall is killed before it can say what it could not read."""
    duties = json.loads((REPO / 'duties.json').read_text())
    entry = next(d for d in duties['duties'] if d['name'] == 'main-publication')

    assert entry['timeout'] > pub.BUDGET
    assert 0 < pub.LOCAL_TIMEOUT < pub.NETWORK_TIMEOUT <= pub.BUDGET


def test_an_unreadable_remote_ref_is_unknown_with_its_own_cause(tmp_path, monkeypatch):
    """A rev-parse that cannot run is not an absent origin/main: the reason must name the call."""
    _, work = clone_with_remote(tmp_path)
    real = pub.git

    def stalled_remote_ref(repo, *args, **kwargs):
        if args[:2] == ('rev-parse', '--verify'):
            return 127, "Command 'git' timed out after 15 seconds"
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(pub, 'git', stalled_remote_ref)
    code, lines = pub.publish(work)

    assert code == 3
    assert 'timed out after 15 seconds' in lines[0]
    assert 'no origin/main' not in lines[0]


def test_a_push_recovery_that_cannot_be_read_is_unknown(tmp_path, monkeypatch):
    """A failed push whose post-push comparison also fails is undecidable, not a proven failure."""
    remote, work = clone_with_remote(tmp_path)
    commit_with_hooks_disabled(work, 'late')
    head = git(work, 'rev-parse', 'HEAD').strip()
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)

    real = pub.git
    comparisons = 0

    def failing_recovery(repo, *args, **kwargs):
        nonlocal comparisons
        if args[:2] == ('merge-base', '--is-ancestor') and args[2] == head:
            comparisons += 1
            if comparisons == 2:  # the first is the "local is not behind" test, before the push
                return 127, "Command 'git' timed out after 15 seconds"
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(pub, 'git', failing_recovery)
    code, lines = pub.publish(work)

    assert code == 3
    assert 'cannot tell whether the push landed' in lines[0]
