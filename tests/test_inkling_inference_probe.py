"""Drill for checks/inkling-inference-probe.py: a silent or failing local route goes RED, a live answer GREEN.

Uses loopback servers the test owns, so no live Inkling route is touched.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "checks" / "inkling-inference-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("inkling_inference_probe", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Answer(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib naming
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_live_answer_is_green():
    check = _load()
    server = _serve(_Answer)
    try:
        verdict, detail = check.probe(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m", 5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert verdict == "GREEN", detail


def test_silent_route_is_red_within_the_deadline():
    # A listening socket that never reads or answers stands in for a route that accepts and then stalls.
    check = _load()
    silent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    try:
        verdict, detail = check.probe(f"http://127.0.0.1:{silent.getsockname()[1]}/v1/chat/completions", "m", 1.0)
    finally:
        silent.close()
    assert verdict == "RED", detail
    assert "silent" in detail


def test_refused_connection_is_red():
    check = _load()
    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_sock.bind(("127.0.0.1", 0))
    port = probe_sock.getsockname()[1]
    probe_sock.close()  # nothing listens on this port now
    verdict, detail = check.probe(f"http://127.0.0.1:{port}/v1/chat/completions", "m", 2.0)
    assert verdict == "RED", detail


def test_http_error_is_red():
    class Broken(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(500)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    check = _load()
    server = _serve(Broken)
    try:
        verdict, detail = check.probe(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m", 5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert verdict == "RED" and "500" in detail, detail


def test_non_completion_body_is_unknown():
    class Odd(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = b"<html>proxy login</html>"
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    check = _load()
    server = _serve(Odd)
    try:
        verdict, detail = check.probe(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m", 5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert verdict == "UNKNOWN", detail


def test_exit_codes_match_duty_registration():
    check = _load()
    assert check.main(["--url", "http://127.0.0.1:1/v1/chat/completions", "--deadline", "1"]) == 2
