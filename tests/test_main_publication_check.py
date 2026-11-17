"""checks/main-publication.py: publishes local main without a hook, and reports what it did."""
import importlib.util
import os
import subprocess
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
