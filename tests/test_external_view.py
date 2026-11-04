from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from mishe_tauftauf.external_view import safe_fleet_view, safe_publish_view
from mishe_tauftauf.feed import Feed


def executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)


class ExternalViewTests(unittest.TestCase):

    def test_fixed_summary_is_normalized_without_channel_label(self):
        projection = ("STATE: INTERMEDIATE\n"
                      "CLEANER: candidates=2 held=1 actionable=0 delete=0 unknowns=0 "
                      "head=abcdef123456 task=present task-epoch=133700 task-event-count=268 "
                      "task-events=133684:open,133700:complete\n")
        safe = safe_publish_view(projection)
        self.assertIsNotNone(safe)
        self.assertIn("SUMMARY: candidates=2", safe)
        self.assertNotIn("CLEANER", safe)








class FleetViewTests(unittest.TestCase):
    ADINT = ("STATE: RED\nOBSERVATION: source=top-pane/adint freshness=fresh "
             "goal=fresh value-coverage=partial semantic=obligations:stale signal=5939026726b789b9\n")
    GREEN = ("STATE: GREEN\nOBSERVATION: source=top-pane/adint freshness=fresh "
             "goal=fresh value-coverage=partial semantic=obligations:fresh signal=5939026726b789b9\n")

    def test_fleet_projection_strips_diagnostics_but_preserves_typed_context(self):
        safe = safe_fleet_view(self.ADINT, "adint")
        self.assertEqual(safe, "STATE: RED\nOBSERVATION: source=top-pane/adint freshness=fresh "
                         "goal=fresh value-coverage=partial semantic=obligations:stale\n")
        self.assertEqual(safe_fleet_view("STATE: UNKNOWN\n", "adint"), None)
        witness = ("STATE: RED\nOBSERVATION: source=top-pane/witness freshness=fresh journal=fresh "
                   "signal=0123456789abcdef tasks-total=11 tasks-unfinished=4 tasks-unowned=2 "
                   "mishe-issues=1 issue-digest=0123456789abcdef\n")
        self.assertIn("journal=fresh", safe_fleet_view(witness, "witness"))
        self.assertNotIn("tasks-total", safe_fleet_view(witness, "witness"))










if __name__ == "__main__":
    unittest.main()
