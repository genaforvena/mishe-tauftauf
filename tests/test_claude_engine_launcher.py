"""The model-change observer must read the claude-engine launchers that
claude_gate writes for resident minds.

Live evidence (health wake 128742, 2026-10-31): minds/health, genome, senses,
pub, field-review and discover are ``# mishe-engine: claude`` launchers with a
``cd`` line before a single ``exec claude --model ...``. model_boundary.launcher
refuses them, so every such mind is reported UNKNOWN and
checks/model-changes.json never reaches state ok.

Two separate causes, pinned separately:
  1. a literal ``cd <path>`` line before exec is refused ("unsupported command
     or code following exec");
  2. the ``claude`` program has no model adapter ("custom engine wrapper is
     UNKNOWN").
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"

CLAUDE_LAUNCHER = """#!/bin/sh
# mishe-engine: claude
# model: claude-haiku-5-5
export MISHE_GATE_HOME=/home/mesh-home/mishe-tauftauf/.mishe-tauftauf
export MISHE_GATE_ROLE=health
cd /home/mesh-home/mishe-tauftauf
exec claude --model claude-haiku-5-5 --permission-mode dontAsk --setting-sources project,local --settings /home/mesh-home/mishe-tauftauf/.mishe-tauftauf/claude-gate/health.settings.json --append-system-prompt 'You are the health mind of the Mishe plant. Follow AGENTS.md.'
"""

OMP_WITH_CD = """#!/bin/sh
export MISHE_GATE_ROLE=probe
cd /home/mesh-home/mishe-tauftauf
exec omp --model vendor/model-a --cwd /repo
"""


class ClaudeEngineLauncherTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SOURCE))
        from mishe_tauftauf import model_boundary

        self.boundary = model_boundary

    def test_cd_before_exec_is_accepted(self):
        # Cause 1 in isolation: an omp launcher with a cd line must read its model.
        launcher = self.boundary.launcher(OMP_WITH_CD)
        self.assertEqual(launcher.model, "vendor/model-a")

    def test_cd_directory_is_kept_for_the_model_runtime(self):
        # The model runtime must run from the directory the pane runs from.
        launcher = self.boundary.launcher(OMP_WITH_CD)
        self.assertEqual(launcher.directory, "/home/mesh-home/mishe-tauftauf")

    def test_claude_launcher_is_refused_for_replacement(self):
        # Status-readable only: there is no claude model adapter yet.
        launcher = self.boundary.launcher(CLAUDE_LAUNCHER)
        with self.assertRaisesRegex(ValueError, "no provider adapter|UNKNOWN"):
            self.boundary.runtime(launcher)

    def test_reads_claude_engine_launcher_identity(self):
        # Cause 1 and cause 2 together: the live claude-engine shape.
        launcher = self.boundary.launcher(CLAUDE_LAUNCHER)
        self.assertEqual(launcher.model, "claude-haiku-5-5")


if __name__ == "__main__":
    unittest.main()
