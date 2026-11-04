import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from mishe_tauftauf.external_view import projected_delta_publish_controls


def executable(path, body):
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)


class ExternalDeltaViewTests(unittest.TestCase):
    def test_projected_negative_control_is_nonidentical_routine_change(self):
        _, negative = projected_delta_publish_controls()
        pair = json.loads(negative)
        self.assertNotEqual(pair["previous"], pair["current"])
        self.assertEqual(pair["previous"].replace("held=1", "held=2"), pair["current"])
        self.assertIn("actionable=0 delete=0 unknowns=0", pair["current"])

    def coordinator(self, home, capture):
        judge = home / "judge"
        executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read() + '\\n---\\n')\nprint('probability 0.9')\n")
        runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_delta_view_slugs=("sensor",)))
        runner.control_failures = {}
        capture.write_text("")
        return runner














if __name__ == "__main__":
    unittest.main()
