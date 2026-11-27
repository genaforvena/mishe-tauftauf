#!/usr/bin/env python3
"""Detector: the local Inkling route answers a real completion within a short deadline.

Why: on 2026-11-27 an inkling-local pub wake went silent from about 18:51:27 (last stream entry) and was only cut
at 18:54:32 by the 1800 s engine cap, after "no output for 180s". The Inkling proxy logged no TimeoutError for
that hang, so inkling-upstream-timeout could not see it, and /v1/models and /health stayed 200 the whole time.
Only a real inference request exposes a route that accepts connections and then never produces tokens.

It asks the proxy what it is doing first (GET /activity), because a completion of its own costs a running wake:
the route serves one request at a time, and a second conversation evicts the wake's cached context. Busy and
producing, or an agent answered within the last 15 minutes, is GREEN; busy and silent past the limit is RED.
Only an otherwise idle route (or a proxy without /activity) gets one tiny completion (max_tokens 4) with a
deadline well under the 180 s silence watchdog.

Exit status:
  0  completion returned in time with a choice (GREEN)
  2  no answer in time, a refused connection, or a non-200 reply (RED)
  3  undecidable: the reply body is not a chat completion (UNKNOWN)
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8081/v1/chat/completions"
DEFAULT_MODEL = "inkling-2-mini"
DEFAULT_DEADLINE = 20.0
SILENT_LIMIT_S = 170.0   # a full re-read of a large prompt is silent for about 90 s
RECENT_REPLY_S = 900.0


def from_activity(url: str, deadline: float) -> tuple[str, str] | None:
    """A verdict from what the proxy is already doing, or None when only a real completion can tell."""
    target = url.split("/v1/", 1)[0] + "/activity"
    try:
        with urllib.request.urlopen(target, timeout=min(deadline, 5.0)) as resp:
            doc = json.loads(resp.read().decode())
        busy, quiet, since = doc["busy"], doc["quiet_s"], doc["since_agent_reply_s"]
        if busy:
            if float(quiet) >= SILENT_LIMIT_S:
                return "RED", f"busy and silent for {float(quiet):.0f}s"
            return "GREEN", f"busy, last output {float(quiet):.0f}s ago"
        if since is not None and float(since) < RECENT_REPLY_S:
            return "GREEN", f"answered an agent {float(since):.0f}s ago; not pinging, to keep its cached context"
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def probe(url: str, model: str, deadline: float) -> tuple[str, str]:
    """Return (verdict, detail) with verdict in {"GREEN", "RED", "UNKNOWN"}."""
    seen = from_activity(url, deadline)
    if seen:
        return seen
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
        "stream": False,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=deadline) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return "RED", f"HTTP {exc.code} from the local route after {time.monotonic() - started:.1f}s"
    except (socket.timeout, TimeoutError):
        return "RED", f"no completion within {deadline:.0f}s (route silent)"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            return "RED", f"no completion within {deadline:.0f}s (route silent)"
        return "RED", f"connection failed: {exc.reason}"
    elapsed = time.monotonic() - started
    if status != 200:
        return "RED", f"HTTP {status} after {elapsed:.1f}s"
    try:
        doc = json.loads(raw.decode())
        choices = doc["choices"]
        if not isinstance(choices, list) or not choices:
            raise KeyError("choices")
    except (ValueError, KeyError, TypeError, UnicodeError):
        return "UNKNOWN", f"200 after {elapsed:.1f}s but the body is not a chat completion"
    return "GREEN", f"completion in {elapsed:.1f}s"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    args = parser.parse_args(argv)
    verdict, detail = probe(args.url, args.model, args.deadline)
    print(f"inkling-inference-probe: {verdict} {detail}")
    return {"GREEN": 0, "RED": 2}.get(verdict, 3)


if __name__ == "__main__":
    sys.exit(main())
