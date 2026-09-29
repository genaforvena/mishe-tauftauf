from __future__ import annotations

import hashlib
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".mishe-tauftauf"))
import plan_state


class PlanContinuityTests(unittest.TestCase):
    def test_interrupted_attempt_reports_spend_handoff_and_unknown_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "spend.jsonl").write_text(
                '{"event":"start","invocation":"self-development-92"}\n',
                encoding="utf-8")
            with patch.object(plan_state, "SITE", site):
                facts = plan_state.attempt_evidence({"invocation": "self-development-92"})
            self.assertEqual(facts, {
                "invocation": "self-development-92", "spend": "start-only",
                "handoff": "missing", "effect": "unattributed",
            })

    def test_prior_attempt_does_not_block_a_distinct_open_plan_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / ".mishe-tauftauf"
            attempts = site / "plan-attempts"
            attempts.mkdir(parents=True)
            plan = root / "plans" / "self-development.md"
            plan.parent.mkdir()
            plan.write_text(
                "- [x] [interrupted] first step\n"
                "- [ ] [next-issue] distinct checked issue\n",
                encoding="utf-8")
            prior_id = hashlib.sha256(b"self-development-v1\ninterrupted\n").hexdigest()
            (attempts / f"{prior_id}.json").write_text(json.dumps({
                "id": prior_id, "step_id": "interrupted", "state": "started",
            }), encoding="utf-8")

            class GreenSuite:
                def __init__(self, _root, _site):
                    pass

                def status(self):
                    return {"state": "GREEN", "source_sha256": "a" * 64}

            with (patch.object(plan_state, "SITE", site),
                  patch.object(plan_state, "ROOT", root),
                  patch.object(plan_state, "PLAN", plan),
                  patch.object(plan_state, "Intake", GreenSuite),
                  patch.object(plan_state, "calls_today", return_value=1)):
                current = plan_state.status()

            self.assertEqual(current["state"], "RED")
            self.assertEqual(current["step_id"], "next-issue")
            self.assertEqual(current["attempt"], "none")


if __name__ == "__main__":
    unittest.main()
