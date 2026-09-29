from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mishe_tauftauf.cli import initialize
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


class RuntimeRecoveryTests(unittest.TestCase):
    def test_completed_retry_supersedes_crashed_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            initialize(home)
            mind = home / 'minds' / 'sensor'
            mind.write_text(
                f'#!{sys.executable}\n'
                'import sys\n'
                'from pathlib import Path\n'
                'sys.stdin.read()\n'
                f"with Path({str(home / 'actions')!r}).open('a') as stream:\n"
                "    stream.write('performed\\n')\n"
            )
            mind.chmod(0o755)
            feed = Feed(home)
            stimulus = feed.append('human', 'perform bounded action')
            feed.append_runtime('mishe-tauftauf', 'entry 1 for top-pain sensor: wake')
            feed.append_runtime('mishe-tauftauf', 'mind starting top-pain sensor for entry 1 attempt=1')
            coordinator = Coordinator(RuntimeConfig(home, launcher='headless'))
            coordinator.invoke('sensor', stimulus)
            self.assertEqual((home / 'actions').read_text(), 'performed\n')
            Coordinator(RuntimeConfig(home, launcher='headless')).retry_unfinished_wakes()
            self.assertEqual((home / 'actions').read_text(), 'performed\n')

    @unittest.skipUnless(hasattr(os, 'pidfd_open'), 'requires Linux process handles')
    def test_orphan_mind_retains_lease_until_it_exits(self):
        """A coordinator restart must not overlap a surviving headless Mind."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            initialize(home)
            ready = home / 'ready'
            release = home / 'release'
            os.mkfifo(ready)
            os.mkfifo(release)
            reader = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
            # Keep the readiness FIFO open so select means data, not writer EOF.
            keepalive = os.open(ready, os.O_WRONLY | os.O_NONBLOCK)
            mind = home / 'minds' / 'sensor'
            mind.write_text(
                f'#!{sys.executable}\n'
                'import os, sys\n'
                'from pathlib import Path\n'
                'sys.stdin.read()\n'
                f'home = Path({str(home)!r})\n'
                "with (home / 'ready').open('w') as stream:\n"
                "    stream.write(str(os.getpid()) + '\\n')\n"
                "with (home / 'release').open('rb') as stream:\n"
                '    stream.read(1)\n'
                "(home / 'artifact').write_text('observed child completion\\n')\n"
            )
            mind.chmod(0o755)
            Feed(home).append('human', 'perform one bounded action')
            script = (
                'from pathlib import Path; '
                'from mishe_tauftauf.feed import Feed; '
                'from mishe_tauftauf.runtime import Coordinator, RuntimeConfig; '
                f'home = Path({str(home)!r}); '
                'Coordinator(RuntimeConfig(home, launcher="headless")).invoke('
                '"sensor", Feed(home).entries()[0])'
            )
            processes = []
            child_handles = []

            def spawn():
                process = subprocess.Popen(
                    [sys.executable, '-c', script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                processes.append(process)
                return process

            def child_ready():
                self.assertTrue(select.select([reader], [], [], 10)[0], 'Mind never started')
                pid = int(os.read(reader, 100).strip())
                handle = os.pidfd_open(pid)
                child_handles.append(handle)
                return handle

            def finish_child(handle):
                deadline = time.monotonic() + 10
                while True:
                    try:
                        writer = os.open(release, os.O_WRONLY | os.O_NONBLOCK)
                        break
                    except OSError as exc:
                        if exc.errno != errno.ENXIO or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.01)
                try:
                    os.write(writer, b'x')
                finally:
                    os.close(writer)
                self.assertTrue(select.select([handle], [], [], 10)[0], 'Mind failed to exit')

            try:
                first = spawn()
                first_child = child_ready()
                first.kill()
                first.wait(timeout=5)
                # A tmux pane closing signals its former foreground process group.
                # The headless Mind must not remain in that group.
                try:
                    os.killpg(first.pid, signal.SIGHUP)
                except ProcessLookupError:
                    pass  # No foreground group remains when the Mind detached.
                self.assertFalse(select.select([first_child], [], [], 0.2)[0],
                                 'Mind died when the coordinator pane closed')
                self.assertFalse(select.select([first_child], [], [], 0)[0], 'Mind did not survive parent')
                restarted = spawn()
                try:
                    restarted.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Capture the duplicate child's identity for scoped cleanup.
                    if select.select([reader], [], [], 0)[0]:
                        child_ready()
                    self.fail('restart launched another Mind while the first was still alive')
                self.assertEqual(restarted.returncode, 0)
                starts = [e for e in Feed(home).entries() if e.body.startswith('mind starting ')]
                self.assertEqual(len(starts), 1)
                finish_child(first_child)
                self.assertEqual((home / 'artifact').read_text(), 'observed child completion\n')
                # A dead orphan must not leave an immortal lease.
                resumed = spawn()
                second_child = child_ready()
                finish_child(second_child)
                self.assertEqual(resumed.wait(timeout=5), 0)
                starts = [e for e in Feed(home).entries() if e.body.startswith('mind starting ')]
                self.assertEqual(len(starts), 2)
            finally:
                for handle in child_handles:
                    try:
                        signal.pidfd_send_signal(handle, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    os.close(handle)
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                os.close(reader)
                os.close(keepalive)


if __name__ == '__main__':
    unittest.main()
