from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import tempfile
import subprocess
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf import completions_judge as judge
from mishe_tauftauf.judges import controls, document
from mishe_tauftauf.feed import Feed


DOC = document("desired-state-met", "sensor", "RED", "exit 1; reading unavailable")


def response(verdict="yes", **overrides):
    payload = {"model": "fixture-v1", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"verdict": verdict})}}],
               "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
    payload.update(overrides)
    return json.dumps(payload).encode()


class CompletionsJudgeTests(unittest.TestCase):
    def setUp(self):
        self.config = judge.Config("https://example.test/v1", "fixture-v1")
        self.env = patch.dict(os.environ, {"COMPLETIONS_API_KEY": "test-secret"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_optional_adapter_exists(self):
        self.assertTrue(callable(getattr(judge, "evaluate", None)), "optional completion adapter missing")

    def test_request_separates_instructions_and_evidence_and_maps_categories(self):
        for verdict, probability in (("yes", 1.0), ("no", 0.0), ("unknown", None)):
            with self.subTest(verdict=verdict), patch.object(judge, "_transport", return_value=response(verdict)) as http:
                result = judge.evaluate(DOC, self.config)
                self.assertEqual(result.probability, probability)
                self.assertEqual(result.verdict, verdict)
                request = http.call_args.args[0]
                self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
                body = json.loads(request.data)
                self.assertEqual(body["model"], "fixture-v1")
                self.assertNotIn("exit 1", body["messages"][0]["content"])
                self.assertIn("exit 1", body["messages"][1]["content"])
                self.assertEqual(result.usage, {"prompt_tokens": 20, "completion_tokens": 5})

    def test_bad_or_incomplete_responses_are_unknown(self):
        bad = [[], {"choices": []}, {"choices": [{}]},
               {"choices": [{"finish_reason": "length", "message": {"content": '{"verdict":"yes"}'}}]},
               {"choices": [{"finish_reason": "stop", "message": {"content": '{"probability":0.99}'}}]},
               {"choices": [{"finish_reason": "stop", "message": {"content": '{"verdict":"yes","extra":1}'}}]},
               {"choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"verdict":"yes"}\n```'}}]},
               {"choices": [{"finish_reason": "stop", "message": {"content": '{"verdict":"yes"}', "refusal": "blocked"}}]}]
        for payload in bad:
            with self.subTest(payload=payload), patch.object(judge, "_transport", return_value=json.dumps(payload).encode()):
                self.assertIsNone(judge.evaluate(DOC, self.config).probability)

    def test_http_and_transport_errors_do_not_echo_credentials_or_response(self):
        failures = [urllib.error.HTTPError("https://example.test", 401, "test-secret", {}, io.BytesIO(b"test-secret")),
                    urllib.error.URLError("test-secret"), TimeoutError("test-secret")]
        for failure in failures:
            with self.subTest(failure=type(failure)), patch.object(judge, "_transport", side_effect=failure):
                result = judge.evaluate(DOC, self.config)
                self.assertIsNone(result.probability)
                self.assertNotIn("test-secret", result.reason)

    def test_broken_http_body_is_unknown(self):
        with patch.object(judge, "_transport", side_effect=http.client.IncompleteRead(b"partial")):
            self.assertIsNone(judge.evaluate(DOC, self.config).probability)

    def test_excessively_nested_json_is_unknown(self):
        with patch.object(judge, "_transport", return_value=b"[" * 31_000 + b"]" * 31_000):
            self.assertIsNone(judge.evaluate(DOC, self.config).probability)

    def test_missing_key_invalid_document_and_oversize_never_call_http(self):
        with patch.object(judge, "_transport") as http:
            for doc in ("", "QUESTION made-up\n\nINSTRUCTIONS\nx", DOC + "x" * judge.MAX_DOCUMENT_BYTES):
                self.assertIsNone(judge.evaluate(doc, self.config).probability)
            os.environ.pop("COMPLETIONS_API_KEY")
            self.assertIsNone(judge.evaluate(DOC, self.config).probability)
            http.assert_not_called()

    def test_endpoint_rejects_credentials_queries_remote_http_and_redirects(self):
        for base in ("http://remote.test/v1", "https://user:secret@example.test/v1", "https://example.test/v1?key=secret", "https://example.test/v1#fragment"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                judge.Config(base, "fixture")
        self.assertEqual(judge.Config("http://127.0.0.1:8000/v1/", "fixture").base_url, "http://127.0.0.1:8000/v1")
        with self.assertRaises(urllib.error.HTTPError):
            judge.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.test")

    def test_identity_tracks_model_endpoint_prompt_and_inference_settings(self):
        first = self.config.identity()
        self.assertNotEqual(first, judge.Config("https://example.test/v1", "other").identity())
        self.assertNotEqual(first, judge.Config("https://other.test/v1", "fixture-v1").identity())
        self.assertNotEqual(first, judge.Config("https://example.test/v1", "fixture-v1", max_tokens=32).identity())
        with patch.object(judge, "SYSTEM_PROMPT", judge.SYSTEM_PROMPT + " changed"):
            self.assertNotEqual(first, self.config.identity())
        os.environ["COMPLETIONS_API_KEY"] = "rotated-secret"
        self.assertEqual(first, self.config.identity())

    def test_adapter_requires_matching_identity_and_outputs_one_line(self):
        args = ["--base-url", self.config.base_url, "--model", self.config.model]
        for identity in (None, "wrong", self.config.identity()):
            out = io.StringIO()
            with patch("sys.stdin", io.StringIO(DOC)), contextlib.redirect_stdout(out), patch.object(judge, "_transport", return_value=response()) as http:
                code = judge.main(args + (["--expected-identity", identity] if identity else []))
                self.assertEqual(code, 0)
                self.assertEqual(len(out.getvalue().splitlines()), 1)
                if identity == self.config.identity():
                    self.assertEqual(out.getvalue(), "probability 1\n")
                else:
                    self.assertTrue(out.getvalue().startswith("unknown "))
                    http.assert_not_called()

    def test_shadow_is_bounded_private_and_records_controls_without_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.jsonl"
            answers = [response(v) for _ in controls() for v in ("yes", "no")]
            with patch.object(judge, "_transport", side_effect=answers) as http:
                code = judge.shadow(self.config, output, [], max_calls=12)
                self.assertEqual(code, 0)
                self.assertEqual(http.call_count, 12)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 13)
            self.assertTrue(rows[-1]["controls_passed"])
            self.assertTrue(all(row["matched"] for row in rows[:-1]))
            self.assertNotIn("test-secret", output.read_text())
            self.assertNotIn("document", rows[0])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with patch.object(judge, "_transport") as http, self.assertRaises(FileExistsError):
                judge.shadow(self.config, output, [], max_calls=12)
            http.assert_not_called()

    def test_shadow_failed_controls_skip_replays_and_insufficient_budget_calls_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = {"document": DOC, "expected": "no"}
            with patch.object(judge, "_transport", side_effect=lambda *_a, **_kw: response("unknown")) as http:
                self.assertEqual(judge.shadow(self.config, root / "failed", [case], max_calls=13), 1)
                self.assertEqual(http.call_count, 12)
            with patch.object(judge, "_transport") as http, self.assertRaises(ValueError):
                judge.shadow(self.config, root / "short", [case], max_calls=12)
            http.assert_not_called()

    def test_witness_reads_canonical_delta_and_previous_analysis_without_writing_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            feed = Feed(home)
            feed.append("operator", "Check this unresolved wish")
            feed.append("witness", "I checked; no change")
            before = (home / "chat.log").read_bytes()
            state = judge.witness_snapshot(home, 1, "Already audited task A")
            self.assertEqual([e["sequence"] for e in state["new_entries"]], [2])
            self.assertEqual([e["sequence"] for e in state["context"]], [1])
            self.assertEqual(state["previous_analysis"], "Already audited task A")
            answer = response()
            payload = json.loads(answer)
            payload["choices"][0]["message"]["content"] = '{"analysis":"progress-loop","evidence_sequences":[1,2]}'
            with patch.object(judge, "_transport", return_value=json.dumps(payload).encode()):
                self.assertEqual(judge.witness_decision(state, self.config)["analysis"], "progress-loop")
            self.assertEqual(state.get("question"), "Which witness analysis, if any, is warranted by the new chat entries?")
            self.assertEqual((home / "chat.log").read_bytes(), before)
            with patch.object(judge, "_transport") as http:
                self.assertEqual(judge.witness_decision(judge.witness_snapshot(home, 2), self.config)["analysis"], "none")
                http.assert_not_called()
            with self.assertRaises(ValueError):
                judge.witness_snapshot(home, 3)

    def test_witness_rejects_old_only_invented_or_malformed_analysis_references(self):
        state = {"context": [{"sequence": 1}], "new_entries": [{"sequence": 2}]}
        for analysis, refs in (("progress-loop", [1]), ("progress-loop", [999]),
                               ("arbitrary-command", [2]), ("evidence-audit", [True]),
                               ("progress-loop", []), ("progress-loop", "2")):
            with self.subTest(analysis=analysis, refs=refs):
                payload = json.loads(response())
                payload["choices"][0]["message"]["content"] = json.dumps({"analysis": analysis, "evidence_sequences": refs})
                with patch.object(judge, "_transport", return_value=json.dumps(payload).encode()):
                    self.assertEqual(judge.witness_decision(state, self.config)["analysis"], "unknown")

    def test_replay_metadata_cannot_change_control_gate_or_skip_summary(self):
        verdicts = [judge.Result(verdict=verdict)
                    for _question, _pair in controls().items()
                    for verdict in ("yes", "no")]
        verdicts.append(judge.Result(verdict="unknown"))
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "trial.jsonl"
            with patch.object(judge, "evaluate", side_effect=verdicts):
                result = judge.shadow(self.config, report,
                                      [{"document": DOC, "control": True}], max_calls=13)
            rows = [json.loads(line) for line in report.read_text().splitlines()]
        self.assertEqual(result, 0)
        self.assertFalse(rows[-2]["control"])
        self.assertIsNone(rows[-2]["matched"])
        self.assertEqual(rows[-1]["controls_passed"], True)
        self.assertEqual(rows[-1]["evaluations"], 13)
        self.assertEqual(rows[-1]["replay_cases_skipped"], 0)

    def test_real_http_and_subprocess_external_judge_protocol(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                raw = response("no")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = judge.Config(f"http://127.0.0.1:{server.server_port}/v1", "fixture-v1")
            command = [os.sys.executable, "-m", "mishe_tauftauf.completions_judge", "--base-url", config.base_url,
                       "--model", config.model, "--expected-identity", config.identity()]
            child_env = dict(os.environ, PYTHONPATH=str(Path(judge.__file__).resolve().parents[1]))
            result = subprocess.run(command, input=DOC, capture_output=True, text=True, timeout=10, env=child_env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "probability 0\n")
            self.assertEqual(captured[0][0], "/v1/chat/completions")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_worker_preserves_valid_unicode_and_quote_heavy_documents(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response())
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = judge.Config(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=2)
            for text in (DOC + '"' * 16_000, DOC + 'é' * 25_000):
                with self.subTest(character=text[-1]):
                    before = len(captured)
                    self.assertLess(len(text.encode()), judge.MAX_DOCUMENT_BYTES)
                    self.assertEqual(judge.evaluate(text, config).verdict, "yes")
                    self.assertEqual(len(captured), before + 1)
                    supplied = json.loads(captured[-1]['messages'][1]['content'])
                    self.assertEqual(text.split('\n\n', 2)[2], supplied['state'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_trickling_response_obeys_total_request_deadline(self):
        received = threading.Event()
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.set()
                self.send_response(200)
                self.end_headers()
                try:
                    for byte in response():
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = judge.Config(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=0.25)
            started = time.monotonic()
            children = []
            popen = subprocess.Popen
            def track(*args, **kwargs):
                child = popen(*args, **kwargs)
                children.append(child)
                self.assertNotIn("test-secret", repr(args))
                self.assertNotIn("COMPLETIONS_API_KEY", kwargs['env'])
                return child
            with patch.object(judge.subprocess, "Popen", side_effect=track):
                result = judge.evaluate(DOC, config)
            self.assertIsNone(result.probability)
            self.assertLess(time.monotonic() - started, 0.55)
            self.assertTrue(received.is_set(), "deadline probe must reach the real server")
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll(), "expired transport must be reaped")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_total_deadline_covers_headers_chunk_lines_and_trailers(self):
        for phase in ("headers", "chunk-line", "trailer"):
            with self.subTest(phase=phase):
                self._slow_framing_probe(phase)

    def _slow_framing_probe(self, phase):
        received = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.set()
                try:
                    if phase == "headers":
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        slow = b"x" * 100 + b"\r\n\r\n"
                    else:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                        if phase == "chunk-line":
                            slow = b"1;" + b"x" * 100 + b"\r\nx\r\n0\r\n\r\n"
                        else:
                            raw = response()
                            self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n0\r\n")
                            slow = b"X-Slow: " + b"x" * 100 + b"\r\n\r\n"
                    self.wfile.flush()
                    for byte in slow:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.01)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = judge.Config(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=0.5)
            started = time.monotonic()
            result = judge.evaluate(DOC, config)
            self.assertIsNone(result.probability)
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertTrue(received.is_set(), "deadline probe must reach the real server")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_fetch_supports_read_only_file_like_response(self):
        class ReadOnly:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def read(self, size):
                return response()[:size]
        with patch.object(judge, "_open", return_value=ReadOnly()):
            self.assertEqual(judge._fetch_bytes(None, timeout=1), response())


if __name__ == "__main__":
    unittest.main()
