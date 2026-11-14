from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf import routerai_systemone as rs

SECRET = "test-key-123-do-not-print"
CRITERIA = {"alpha": "the state names a lookup", "beta": "the state names a diff review"}
ANSWER = json.dumps(
    {"answers": {"q": {"choice": "alpha", "probabilities": {"alpha": 0.9, "beta": 0.05, "unclear": 0.05}, "confidence": 0.9}}}
).encode()


class _Recorder(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server naming
        self.server.seen.append(self.headers.get("Authorization"))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(ANSWER)))
        self.end_headers()
        self.wfile.write(ANSWER)

    def log_message(self, *_args):
        pass


class RouterAISystemOneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.key = self.dir / "routerai.key"
        self.key.write_text(f"ROUTERAI_API_KEY={SECRET}\n", encoding="utf-8")
        os.chmod(self.key, 0o600)
        self.env = patch.dict(
            os.environ,
            {"ROUTERAI_BUDGET_FILE": str(self.dir / "budget.json"), "ROUTERAI_DAILY_CALL_LIMIT": "50"},
        )
        self.env.start()
        os.environ.pop("ROUTERAI_BASE_URL", None)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def _decide(self, **kwargs):
        return rs.decide("q", "pick", "state", CRITERIA, threshold=0.5, key_file=str(self.key), **kwargs)

    def test_private_key_and_https_default_reach_the_transport(self):
        calls = []

        def fake_post(url, body, key):
            calls.append((url, key))
            return ANSWER

        decision = self._decide(http_post=fake_post)
        self.assertTrue(decision.decided, decision.reason)
        self.assertEqual(calls[0][0], "https://routerai.ru/api/v1/systemone")
        self.assertEqual(calls[0][1], SECRET)

    def test_plain_http_non_loopback_base_url_is_refused_before_any_post(self):
        calls = []
        with patch.dict(os.environ, {"ROUTERAI_BASE_URL": "http://routerai.example"}):
            decision = self._decide(http_post=lambda *a: calls.append(a) or ANSWER)
        self.assertFalse(decision.decided)
        self.assertIn("https", decision.reason)
        self.assertEqual(calls, [])
        self.assertNotIn(SECRET, decision.reason)

    def test_group_readable_key_file_is_refused_without_reading_it(self):
        os.chmod(self.key, 0o644)
        calls = []
        self.assertRaises(rs.KeyFileError, rs.resolve_key, str(self.key))
        decision = self._decide(http_post=lambda *a: calls.append(a) or ANSWER)
        self.assertFalse(decision.decided)
        self.assertIn("group or others", decision.reason)
        self.assertEqual(calls, [])
        self.assertNotIn(SECRET, decision.reason)

    def test_redirect_is_not_followed_and_bearer_is_not_resent(self):
        target = HTTPServer(("127.0.0.1", 0), _Recorder)
        target.seen, target.redirect_to = [], None
        origin = HTTPServer(("127.0.0.1", 0), _Recorder)
        origin.seen = []
        origin.redirect_to = f"http://127.0.0.1:{target.server_port}/api/v1/systemone"
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (origin, target)]
        for t in threads:
            t.start()
        try:
            with patch.dict(os.environ, {"ROUTERAI_BASE_URL": f"http://127.0.0.1:{origin.server_port}"}):
                decision = self._decide()
            self.assertFalse(decision.decided)
            self.assertIn("HTTP 302", decision.reason)
            self.assertEqual(origin.seen, [f"Bearer {SECRET}"])
            self.assertEqual(target.seen, [], "redirect target must never be contacted")
            self.assertNotIn(SECRET, decision.reason)
        finally:
            for s in (origin, target):
                s.shutdown()
                s.server_close()


if __name__ == "__main__":
    unittest.main()
