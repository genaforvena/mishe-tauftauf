from __future__ import annotations

import importlib.util
import io
import json
import multiprocessing
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ADAPTER = Path(__file__).resolve().parents[1] / "examples" / "jev-judge.py"
SPEC = importlib.util.spec_from_file_location("jev_judge_budget_test", ADAPTER)
jev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jev)
DOCUMENT = "QUESTION publish\nINSTRUCTIONS\nIs this new?\nTOP PAIN sensor\nred\nEVIDENCE\nprobe failed\n"


def reserve_worker(path: str, results) -> None:
    results.put(jev.reserve_daily_call(7, Path(path), "2026-09-23")[0])


class JevBudgetTests(unittest.TestCase):
    def test_enabled_budget_reserves_before_http_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "budget.json"
            values = {"TYPESAFE_API_KEY": "dummy", "TYPESAFE_DAILY_CALL_LIMIT": "2", "TYPESAFE_BUDGET_FILE": str(state)}
            answer = io.BytesIO(b'{"answers":{"publish":{"noul":0.91}},"model":"jev"}')
            with patch.dict(os.environ, values), patch.object(jev.urllib.request, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(answer.getvalue())) as http:
                self.assertEqual(jev.evaluate(DOCUMENT)[0], 0.91)
                self.assertEqual(jev.evaluate(DOCUMENT)[0], 0.91)
                self.assertIn("budget exhausted", jev.evaluate(DOCUMENT)[1])
                self.assertEqual(http.call_count, 2)
            self.assertEqual(set(json.loads(state.read_text())), {"date", "count"})
            self.assertEqual(json.loads(state.read_text())["count"], 2)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((Path(str(state) + ".lock")).stat().st_mode), 0o600)

    def test_failed_http_still_spends_reservation_and_zero_limit_disables_network(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "budget.json"
            values = {"TYPESAFE_API_KEY": "dummy", "TYPESAFE_DAILY_CALL_LIMIT": "1", "TYPESAFE_BUDGET_FILE": str(state)}
            with patch.dict(os.environ, values), patch.object(jev.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")) as http:
                self.assertIn("connection failed", jev.evaluate(DOCUMENT)[1])
                self.assertIn("budget exhausted", jev.evaluate(DOCUMENT)[1])
                self.assertEqual(http.call_count, 1)
            self.assertEqual(json.loads(state.read_text())["count"], 1)
            state.unlink()
            with patch.dict(os.environ, {**values, "TYPESAFE_DAILY_CALL_LIMIT": "0"}), patch.object(jev.urllib.request, "urlopen") as http:
                self.assertIn("budget exhausted", jev.evaluate(DOCUMENT)[1])
                http.assert_not_called()

    def test_corrupt_or_insecure_state_fails_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "budget.json"
            values = {"TYPESAFE_API_KEY": "dummy", "TYPESAFE_DAILY_CALL_LIMIT": "2", "TYPESAFE_BUDGET_FILE": str(state)}
            state.write_text("broken", encoding="utf-8")
            state.chmod(0o600)
            with patch.dict(os.environ, values), patch.object(jev.urllib.request, "urlopen") as http:
                self.assertIn("budget", jev.evaluate(DOCUMENT)[1])
                http.assert_not_called()
            state.write_text(json.dumps({"date": "2026-09-23", "count": 0}), encoding="utf-8")
            state.chmod(0o644)
            with patch.dict(os.environ, values), patch.object(jev.urllib.request, "urlopen") as http:
                self.assertIn("budget", jev.evaluate(DOCUMENT)[1])
                http.assert_not_called()

    def test_cross_process_reservations_are_atomic_and_utc_day_rolls(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "budget.json"
            ctx = multiprocessing.get_context("fork")
            results = ctx.Queue()
            workers = [ctx.Process(target=reserve_worker, args=(str(state), results)) for _ in range(16)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sum(results.get(timeout=2) for _ in workers), 7)
            self.assertEqual(json.loads(state.read_text()), {"date": "2026-09-23", "count": 7})
            self.assertTrue(jev.reserve_daily_call(7, state, "2026-09-24")[0])
            self.assertEqual(json.loads(state.read_text()), {"date": "2026-09-24", "count": 1})
            self.assertFalse(jev.reserve_daily_call(7, state, "2026-09-23")[0])

    def test_default_unbudgeted_behavior_and_bad_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "budget.json"
            values = {"TYPESAFE_API_KEY": "dummy", "TYPESAFE_BUDGET_FILE": str(state)}
            with patch.dict(os.environ, values), patch.object(jev.urllib.request, "urlopen", return_value=io.BytesIO(b'{"answers":{"publish":{"noul":0.5}}}')) as http:
                os.environ.pop("TYPESAFE_DAILY_CALL_LIMIT", None)
                self.assertEqual(jev.evaluate(DOCUMENT)[0], 0.5)
                http.assert_called_once()
            self.assertFalse(state.exists())
            with patch.dict(os.environ, {**values, "TYPESAFE_DAILY_CALL_LIMIT": "abc"}), patch.object(jev.urllib.request, "urlopen") as http:
                self.assertIn("budget", jev.evaluate(DOCUMENT)[1])
                http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
