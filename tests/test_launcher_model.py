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
        from mishe_tauftauf import seed

        self.seed = seed
        self.directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def test_reads_selected_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")

    def test_follows_a_launcher_swap(self):
        """The point of the seam: a launcher change is what records the replacement."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")
        path.write_text("#!/bin/sh\nexec omp --model vendor/model-b --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-b")

    def test_ignores_comments_and_finds_the_real_flag(self):
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n# --model vendor/decoy is only a comment\nexec omp --cwd /repo --model vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_reports_absence_when_no_model_flag(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_missing_launcher_is_none(self):
        self.assertIsNone(self.seed._launcher_model(Path(self.directory) / "minds" / "absent"))


def test_respawn_reads_the_model_the_new_process_runs(tmp_path):
    """A respawn records the launcher's current bytes, which is what the pane runs."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", home / "minds" / "probe", "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={home / 'minds' / 'probe'} reason=start model=vendor/model-a"]


def test_a_launcher_swap_replaces_the_recorded_model(tmp_path):
    """A model change at a respawn is exactly what the record must make visible."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-b --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [
        f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-a",
        f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-b",
    ]


def test_the_same_model_does_not_add_a_record_per_idle_rotation(tmp_path):
    """A rotation that lands on the same model is not a new continuity fact."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    seed._record_mind_model(home, "probe", launcher, "clear")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-a"]


def test_a_launcher_without_a_model_is_recorded_as_none_not_silence(tmp_path):
    """Losing ``--model`` is a model change, so it must not pass unrecorded."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={launcher} reason=start model=none"]


def test_a_missing_launcher_records_none_without_failing(tmp_path):
    """A site that lacks the launcher still records the respawn, without raising."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    home.mkdir()
    seed._record_mind_model(home, "probe", home / "minds" / "probe", "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={home / 'minds' / 'probe'} reason=start model=none"]


if __name__ == "__main__":
    unittest.main()
