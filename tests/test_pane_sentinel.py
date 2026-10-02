from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from mishe_tauftauf import tmux as tmux_module
from mishe_tauftauf.tmux import check_pane


class PaneSentinelTests(unittest.TestCase):
    LEASE = "-- pane live 2026-10-01T00:00:{:02d}.000000Z · refresh 5s · ticks every frame --"

    def _check(self, frames):
        seen = []

        def capture(session, slug):
            seen.append(1)
            return frames[min(len(seen), len(frames)) - 1]

        with mock.patch.object(tmux_module, "_pane_stopped_or_dead", return_value=False), \
                mock.patch.object(tmux_module, "capture_raw", side_effect=capture):
            return check_pane(Path("/nonexistent"), "session", "sensor", wait=0.01)

    def test_quoted_pane_text_is_not_a_missing_pane(self):
        # A live pane's own text can quote the phrase "pane missing" (a peer chat
        # line about a pane check). capture_raw's read-failure sentinel must be
        # matched exactly, or the quoted text becomes a false HOLD.
        body = "    | peer: capture `UNKNOWN — top-pain witness pane missing`\nBODY\n"
        ok, line = self._check([body + self.LEASE.format(1) + "\n",
                                body + self.LEASE.format(2) + "\n"])
        self.assertTrue(ok, line)
        self.assertIn("pane-live", line)

    def test_capture_failure_sentinel_still_reports_a_missing_pane(self):
        with mock.patch.object(tmux_module, "_pane_stopped_or_dead", return_value=False), \
                mock.patch.object(tmux_module, "capture_raw",
                                  return_value="UNKNOWN — top-pain sensor pane missing\n"):
            ok, line = check_pane(Path("/nonexistent"), "session", "sensor", wait=0.01)
        self.assertFalse(ok)
        self.assertIn("pane-missing", line)

    def test_capture_failure_sentinel_still_reports_an_empty_pane(self):
        with mock.patch.object(tmux_module, "_pane_stopped_or_dead", return_value=False), \
                mock.patch.object(tmux_module, "capture_raw",
                                  return_value="UNKNOWN — top-pain sensor pane empty\n"):
            ok, line = check_pane(Path("/nonexistent"), "session", "sensor", wait=0.01)
        self.assertFalse(ok)
        self.assertIn("pane-empty", line)


if __name__ == "__main__":
    unittest.main()
