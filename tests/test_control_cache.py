from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf.cli import initialize, main
from mishe_tauftauf.judges import controls
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


def adapter(path: Path, count: Path, suffix: str = "") -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        f"from pathlib import Path\np = Path({str(count)!r})\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "print('probability 0.95' if n % 2 else 'probability 0.05')\n"
        f"# {suffix}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class ControlCacheTests(unittest.TestCase):
    def test_refresh_then_reuse_without_new_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge, count = home / "judge", home / "calls"
            adapter(judge, count)
            config = RuntimeConfig(home, judge=judge, control_cache_ttl=3600, refresh_controls=True)
            self.assertEqual(Coordinator(config).control_failures, {})
            self.assertEqual(int(count.read_text()), len(controls()) * 2)
            config.refresh_controls = False
            self.assertEqual(Coordinator(config).control_failures, {})
            self.assertEqual(int(count.read_text()), len(controls()) * 2)
            self.assertEqual(json.loads((home / "control-cache.json").read_text())["version"], 1)

    def test_cache_absent_stale_corrupt_or_changed_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge, count = home / "judge", home / "calls"
            adapter(judge, count)
            config = RuntimeConfig(home, judge=judge, control_cache_ttl=3600)
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            self.assertFalse(count.exists())
            (home / "top-pains" / "sensor").write_text("#!/bin/sh\nprintf 'RED'\n", encoding="utf-8")
            (home / "top-pains" / "sensor").chmod(0o755)
            Coordinator(config).observe()
            self.assertFalse(count.exists(), "a missing cache must not call the judge for ordinary work")
            config.refresh_controls = True
            self.assertEqual(Coordinator(config).control_failures, {})
            config.refresh_controls = False
            cache = home / "control-cache.json"
            original = cache.read_text()
            payload = json.loads(original)
            payload["checked_at"] = 946684800
            cache.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            cache.write_text("{bad", encoding="utf-8")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            payload = json.loads(original)
            payload["checked_at"] = 4102444800
            cache.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            cache.write_text(original, encoding="utf-8")
            adapter(judge, count, "changed executable bytes")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            self.assertEqual(int(count.read_text()), len(controls()) * 2)

    def test_policy_revision_and_question_text_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge, count = home / "judge", home / "calls"
            adapter(judge, count)
            policy = home / "policy.json"
            policy.write_text('{"version":"one"}', encoding="utf-8")
            config = RuntimeConfig(home, judge=judge, policy=policy, control_cache_ttl=3600, refresh_controls=True)
            self.assertEqual(Coordinator(config).control_failures, {})
            config.refresh_controls = False
            policy.write_text('{"version":"two"}', encoding="utf-8")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            policy.write_text('{"version":"one","question_versions":{"publish":"two"},"questions":{"publish":"Changed question"}}', encoding="utf-8")
            self.assertEqual(set(Coordinator(config).control_failures), set(controls()))
            self.assertEqual(int(count.read_text()), len(controls()) * 2)

    def test_batch_adapter_cache_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge, count = home / "batch-judge", home / "calls"
            judge.write_text(
                "#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\n"
                f"p = Path({str(count)!r})\n"
                "r = json.load(sys.stdin)\nn = int(p.read_text()) + 1 if p.exists() else 1\n"
                "p.write_text(str(n))\n"
                "print(json.dumps({'results': {q: {'probability': 0.95 if n % 2 else 0.05} for q in r['questions']}}))\n",
                encoding="utf-8",
            )
            judge.chmod(0o755)
            config = RuntimeConfig(home, batch_judge=judge, control_cache_ttl=3600, refresh_controls=True)
            self.assertEqual(Coordinator(config).control_failures, {})
            config.refresh_controls = False
            self.assertEqual(Coordinator(config).control_failures, {})
            self.assertEqual(int(count.read_text()), len(controls()) * 2)

    def test_default_still_calls_controls_and_invalid_options_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge, count = home / "judge", home / "calls"
            adapter(judge, count)
            self.assertEqual(Coordinator(RuntimeConfig(home, judge=judge)).control_failures, {})
            self.assertEqual(int(count.read_text()), len(controls()) * 2)
            self.assertFalse((home / "control-cache.json").exists())
            self.assertEqual(main(["--home", str(home), "run", "--once", "--refresh-controls"]), 2)
            self.assertEqual(main(["--home", str(home), "run", "--once", "--control-cache-ttl", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
