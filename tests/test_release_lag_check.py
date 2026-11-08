"""checks/release-lag.py: RED when committed main sits unreleased too long; UNKNOWN when it cannot tell."""
import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_lag', REPO / 'checks/release-lag.py')
lag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lag)
T0 = 1_800_000_000


def git(cwd, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME='m', GIT_AUTHOR_EMAIL='m@x', GIT_COMMITTER_NAME='m', GIT_COMMITTER_EMAIL='m@x',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f'{when} +0000', GIT_COMMITTER_DATE=f'{when} +0000')
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def repo_with_home(tmp_path):
    work = tmp_path / 'work'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(work)], check=True)
    home = tmp_path / 'site'
    (home / 'releases').mkdir(parents=True)
    (home / 'runtime').mkdir()
    return work, home


def commit(work, name, when):
    (work / name).write_text(name)
    git(work, 'add', name)
    git(work, 'commit', '-qm', name, when=when)
    return git(work, 'rev-parse', 'HEAD')


def pin(home, sha):
    link = home / 'runtime' / 'current'
    if link.is_symlink():
        link.unlink()
    link.symlink_to(home / 'releases' / sha)


def test_pin_at_head_is_green(tmp_path):
    work, home = repo_with_home(tmp_path)
    head = commit(work, 'a', T0)
    pin(home, head)
    assert lag.evaluate(home, work, T0 + 99999)[0] == 0


def test_young_unreleased_commit_is_green_and_old_one_is_red(tmp_path):
    work, home = repo_with_home(tmp_path)
    base = commit(work, 'a', T0)
    pin(home, base)
    commit(work, 'b', T0 + 100)
    commit(work, 'c', T0 + 200)
    code, lines = lag.evaluate(home, work, T0 + 100 + 60, max_age=1800)
    assert code == 0, lines
    code, lines = lag.evaluate(home, work, T0 + 100 + 1801, max_age=1800)
    assert code == 2, lines
    assert '2 commit(s)' in lines[0]


def test_missing_or_unnamed_pin_is_unknown(tmp_path):
    work, home = repo_with_home(tmp_path)
    commit(work, 'a', T0)
    assert lag.evaluate(home, work, T0)[0] == 3
    (home / 'runtime' / 'current').symlink_to(home / 'releases' / 'latest')
    assert lag.evaluate(home, work, T0)[0] == 3


def test_pin_not_in_clone_is_unknown(tmp_path):
    work, home = repo_with_home(tmp_path)
    commit(work, 'a', T0)
    pin(home, 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef')
    assert lag.evaluate(home, work, T0 + 99999)[0] == 3


def test_pin_that_is_not_an_ancestor_is_unknown(tmp_path):
    work, home = repo_with_home(tmp_path)
    commit(work, 'a', T0)
    git(work, 'checkout', '-q', '--orphan', 'other')
    other = commit(work, 'z', T0 + 5)
    git(work, 'checkout', '-q', 'main')
    pin(home, other)
    assert lag.evaluate(home, work, T0 + 99999)[0] == 3
