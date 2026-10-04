"""Model identity is recorded per invocation so a fresh successor can attribute a
continuity change to a model replacement without reading /proc or guessing.

The launcher bytes are the durable record of which model an invocation runs on;
these tests pin that seam so a swap stays observable.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"


def write_mind(directory: str, slug: str, text: str) -> Path:
    path = Path(directory) / "minds" / slug
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class LauncherModelTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SOURCE))
        from mishe_tauftauf import cli

        self.cli = cli
        self.directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def test_reads_selected_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.cli._launcher_model(path), "vendor/model-a")

    def test_follows_a_launcher_swap(self):
        """The point of the seam: a launcher change is what records the replacement."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.cli._launcher_model(path), "vendor/model-a")
        path.write_text("#!/bin/sh\nexec omp --model vendor/model-b --cwd /repo\n")
        self.assertEqual(self.cli._launcher_model(path), "vendor/model-b")

    def test_ignores_comments_and_finds_the_real_flag(self):
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n# --model vendor/decoy is only a comment\nexec omp --cwd /repo --model vendor/real\n",
        )
        self.assertEqual(self.cli._launcher_model(path), "vendor/real")

    def test_reports_absence_when_no_model_flag(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --cwd /repo\n")
        self.assertIsNone(self.cli._launcher_model(path))

    def test_missing_launcher_is_none(self):
        self.assertIsNone(self.cli._launcher_model(Path(self.directory) / "minds" / "absent"))


if __name__ == "__main__":
    unittest.main()
