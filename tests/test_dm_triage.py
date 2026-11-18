from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from mishe_tauftauf import dm_triage as dt

_NOTICE = ("mishe-wake@genome.service failed. Signal failed-mishe-wake-genome-service is RED. "
           "Find the cause (journalctl --user -u mishe-wake@genome.service), fix or disposition it, "
           "then `mishe signal observe failed-mishe-wake-genome-service GREEN`.")


class DmTriageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_tag_rule_decides_without_a_model(self):
        self.assertEqual(dt.system0_tag("[ci] state=ok sha=abc\nbody"), "fyi")
        self.assertEqual(dt.system0_tag("[permission] id=x decision=granted owner=genome"), "action")
        self.assertIsNone(dt.system0_tag("[dm] to=system1\nhello there"))
        self.assertIsNone(dt.system0_tag("plain prose message"))

    def test_failure_notice_is_a_system0_action(self):
        notice = _NOTICE
        self.assertEqual(dt.system0_class(notice), "action")
        self.assertEqual(dt.system0_class("[dm] to=health\n" + notice), "action")
        self.assertIsNone(dt.system0_class("[dm] to=system1\nhello there"))

    def test_record_unread_logs_only_machine_forms(self):
        notice = _NOTICE
        mail = [(101, "[dm] to=system1\nplease review the pin"),
                (102, "[ci] state=ok\n"),
                (103, "[dm] to=health\n" + notice)]
        rows = dt.record_unread(self.home, "system1", mail, now=1000.0)
        # Prose (101) is left to the mind: no row, and no provider call to make one.
        self.assertEqual([r["seq"] for r in rows], [102, 103])
        self.assertEqual([r["label"] for r in rows], ["fyi", "action"])
        self.assertEqual(dt.recorded_seqs(self.home), {102, 103})

    def test_record_and_dedup(self):
        mail = [(101, "[dm] to=system1\nfirst"), (102, "[ci] state=ok\n")]
        rows = dt.record_unread(self.home, "system1", mail, now=1000.0)
        self.assertEqual(len(rows), 1)
        # Second pass over the same mail adds nothing.
        rows2 = dt.record_unread(self.home, "system1", mail, now=2000.0)
        self.assertEqual(rows2, [])
        lines = dt.log_path(self.home).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["seq"], 102)
        self.assertEqual(row["source"], "system0")
        self.assertIsNone(row["confidence"])
        self.assertIsNone(row["outcome"])

    def test_record_unread_never_raises(self):
        blocked = self.root / "not-a-directory"
        blocked.write_text("x", encoding="utf-8")
        rows = dt.record_unread(blocked / "home", "system1",
                                [(1, "[ci] state=ok\n")], now=1.0)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
