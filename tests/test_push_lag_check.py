"""checks/push-lag.py: RED when main sits unpushed too long; UNKNOWN when it cannot tell."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('push_lag', REPO / 'checks/push-lag.py')
lag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lag)
T0 = 1_800_000_000


def git(cwd, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME='m', GIT_AUTHOR_EMAIL='m@x', GIT_COMMITTER_NAME='m', GIT_COMMITTER_EMAIL='m@x',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f'{when} +0000', GIT_COMMITTER_DATE=f'{when} +0000')
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, env=env)


def clone_with_remote(tmp_path):
    remote, work = tmp_path / 'remote.git', tmp_path / 'work'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)], check=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(work)], check=True)
    git(work, 'remote', 'add', 'origin', str(remote))
    (work / 'f').write_text('1')
    git(work, 'add', 'f')
    git(work, 'commit', '-qm', 'first', when=T0)
    git(work, 'push', '-q', 'origin', 'main')
    return remote, work


def commit(work, name, when):
    (work / name).write_text(name)
    git(work, 'add', name)
    git(work, 'commit', '-qm', name, when=when)


def test_everything_pushed_is_green(tmp_path):
    _, work = clone_with_remote(tmp_path)
    assert lag.evaluate(work, T0 + 99999)[0] == 0


def test_a_young_unpushed_commit_is_green_and_an_old_one_is_red(tmp_path):
    _, work = clone_with_remote(tmp_path)
    commit(work, 'a', T0 + 100)
    commit(work, 'b', T0 + 200)
    code, lines = lag.evaluate(work, T0 + 100 + 3600)
    assert code == 0 and '2 unpushed' in lines[0]
    code, lines = lag.evaluate(work, T0 + 100 + 3 * 3600)
    assert code == 2 and 'the oldest is 3.0 h old' in lines[0]


def test_pushing_clears_it(tmp_path):
    _, work = clone_with_remote(tmp_path)
    commit(work, 'a', T0 + 100)
    assert lag.evaluate(work, T0 + 100 + 9 * 3600)[0] == 2
    git(work, 'push', '-q', 'origin', 'main')
    assert lag.evaluate(work, T0 + 100 + 9 * 3600)[0] == 0


def test_divergence_is_red_and_an_unreadable_remote_is_undecidable(tmp_path):
    remote, work = clone_with_remote(tmp_path)
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    commit(other, 'theirs', T0 + 50)
    git(other, 'push', '-q', 'origin', 'main')
    code, lines = lag.evaluate(work, T0 + 99999)
    assert code == 3 and 'does not have' in lines[0]                  # remote moved; this clone has not fetched
    commit(work, 'mine', T0 + 60)
    git(work, 'fetch', '-q', 'origin')
    code, lines = lag.evaluate(work, T0 + 99999)
    assert code == 2 and 'diverged' in lines[0]
    git(work, 'remote', 'set-url', 'origin', str(tmp_path / 'missing.git'))
    assert lag.evaluate(work, T0)[0] == 3


def test_cli_exit_code(tmp_path):
    import time
    _, work = clone_with_remote(tmp_path)
    commit(work, 'a', int(time.time()) - 7200)                         # the CLI judges against the real clock
    done = subprocess.run([sys.executable, str(REPO / 'checks/push-lag.py'), '--repo', str(work), '--max-age-hours', '0.0001'],
                          capture_output=True, text=True)
    assert done.returncode == 2 and 'unpushed' in done.stdout
