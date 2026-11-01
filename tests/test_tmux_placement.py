"""Workload tmux sessions must not be created from a life-support (lifeline-slice) caller.

Mock-only: no tmux server is started. The caller's slice is faked by patching
proc_table.current_cgroup_slice, the single reader tmux.start() consults.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mishe_tauftauf import proc_table, tmux

LIFELINE_SLICE = "mishe-lifeline.slice"


class WorkloadPlacementTests(unittest.TestCase):
    def _run_start(self, session):
        calls = []

        def fake_tmux(*args, check=True, socket=None):
            calls.append(args)
            if args and args[0] == "has-session":
                return subprocess.CompletedProcess(args, 1, b"", b"")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(tmux, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(tmux, "discover", return_value=[]), \
             mock.patch.object(tmux, "_install_observability", lambda *a: None), \
             mock.patch.object(proc_table, "current_cgroup_slice", return_value=LIFELINE_SLICE):
            try:
                tmux.start(Path(directory), session, interval=5.0)
                refused = None
            except tmux.TmuxError as error:
                refused = error
        return refused, [c for c in calls if c and c[0] == "new-session"]

    def test_workload_session_from_lifeline_caller_is_refused(self):
        refused, new_sessions = self._run_start("mishe-self-development-current")
        self.assertIsNotNone(refused, "start() created a workload session from the protected slice")
        self.assertEqual(new_sessions, [], "no new-session may be issued from the protected slice")


if __name__ == "__main__":
    unittest.main()
