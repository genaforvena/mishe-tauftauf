"""RouterAI System One decision adapter (choice questions only).

System One is the middle rung of the decision ladder: System 0 (code, checks,
lookups) first, then this typed decision model that picks ONE label from a list
the core supplies, then a mind (System 2) only when this rung cannot decide.

The adapter POSTs one choice question to the RouterAI System One endpoint:

    {"model": ..., "state": ..., "questions": {name: {"type": "choice",
        "instructions": ..., "criteria": {option: one-line description}}}}

and reads answers.NAME.{choice, probabilities, confidence}. Every failure, from
a missing key, a budget limit, HTTP error, malformed body, unknown option or
out-of-range number, becomes an unknown decision with a reason. Callers treat
unknown as "use the mind path", never as an answer.

Limits that stand (operator approval 2026-11-14, see operator/standing-approvals.md):
  - decision models only; never a mind engine, never patch review;
  - the bearer key is read from a private key file (ROUTERAI_KEY_FILE, default
    ~/.mesh/routerai.key) and never printed, logged, or put in arguments;
  - states are derived; the caller passes only what the question needs.

Settings (environment):
  ROUTERAI_KEY_FILE       path to the key file (absolute); default ~/.mesh/routerai.key
  ROUTERAI_BASE_URL       default https://routerai.ru
  ROUTERAI_MODEL          default typesafe/jev-1.14 (the one the Jev adapter already speaks)
  ROUTERAI_DAILY_CALL_LIMIT  integer 0..1000; unset means DEFAULT_DAILY_LIMIT (100)
  ROUTERAI_BUDGET_FILE    absolute path of the budget state file

Run `python -m mishe_tauftauf.routerai_systemone --self-test` for the offline
contract. The live drill is `--control`, which sends two fixed synthetic
questions and checks that the answers separate.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mishe_tauftauf.jev_judge import reserve_daily_call

DEFAULT_BASE_URL = "https://routerai.ru"
DEFAULT_MODEL = "typesafe/jev-1.14"
DEFAULT_KEY_FILE = "~/.mesh/routerai.key"
TIMEOUT_SECONDS = 30
DEFAULT_DAILY_LIMIT = 100
MAX_STATE_CHARS = 20_000
UNCLEAR = "unclear"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 mishe-tauftauf-system1"
)


@dataclass(frozen=True)
class Decision:
    """One System One answer. choice is None when the rung cannot decide."""

    question: str
    choice: str | None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    reason: str = ""

    @property
    def decided(self) -> bool:
        return self.choice is not None and self.choice != UNCLEAR


class KeyFileError(ValueError):
    """The key file exists but is not safe to read. The message never contains the key."""


def resolve_key(path: str | None = None) -> str | None:
    """Read the bearer key from a private key file. Never returns it in a message.

    Refuses (KeyFileError) a file readable by group or others, or not owned by this user.
    """
    raw = path or os.environ.get("ROUTERAI_KEY_FILE") or DEFAULT_KEY_FILE
    key_path = pathlib.Path(raw).expanduser()
    try:
        status = key_path.stat()
    except OSError:
        return None
    if status.st_mode & 0o077:
        raise KeyFileError("RouterAI key file is readable by group or others; make it mode 600")
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise KeyFileError("RouterAI key file is not owned by this user")
    try:
        text = key_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            if name.strip() != "ROUTERAI_API_KEY":
                continue
            line = value.strip()
        stripped = line.strip("\"'")
        return stripped or None
    return None


def base_url() -> str:
    url = os.environ.get("ROUTERAI_BASE_URL", "").strip() or DEFAULT_BASE_URL
    parsed = urllib.parse.urlsplit(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed_schemes = {"https", "http"} if local else {"https"}
    if (not parsed.hostname or parsed.scheme not in allowed_schemes or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("ROUTERAI_BASE_URL requires https (http only on loopback), without credentials, query or fragment")
    return url.rstrip("/")


def model_alias() -> str:
    return os.environ.get("ROUTERAI_MODEL", "").strip() or DEFAULT_MODEL


def daily_budget() -> tuple[int, pathlib.Path] | None:
    raw = os.environ.get("ROUTERAI_DAILY_CALL_LIMIT")
    if raw is None:
        raw = str(DEFAULT_DAILY_LIMIT)
    if not re.fullmatch(r"[0-9]{1,4}", raw) or int(raw) > 1000:
        raise ValueError("RouterAI budget limit must be an integer in [0, 1000]")
    configured = os.environ.get("ROUTERAI_BUDGET_FILE")
    if configured:
        path = pathlib.Path(configured)
    else:
        state_home = pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local" / "state")
        path = state_home / "mishe-tauftauf" / "routerai-system1-budget.json"
    if not path.is_absolute():
        raise ValueError("RouterAI budget path must be absolute")
    return int(raw), path


def _valid_probabilities(raw: object, options: list[str]) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    clean: dict[str, float] = {}
    for option in options:
        value = raw.get(option)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            return None
        clean[option] = number
    return clean


def _unknown(question: str, reason: str) -> Decision:
    return Decision(question=question, choice=None, reason=reason)


def decide(
    question: str,
    instructions: str,
    state: str,
    criteria: dict[str, str],
    *,
    threshold: float,
    log_path: pathlib.Path | None = None,
    key_file: str | None = None,
    http_post=None,
) -> Decision:
    """Ask one choice question. Returns a Decision; decided only when it clears the threshold.

    criteria maps option name to a one-line description. The escape option
    ``unclear`` is always added, so the model can refuse rather than guess.
    Below ``threshold`` the answer is not acted on: the caller escalates to a mind.
    """
    options = list(criteria.keys())
    if not options or UNCLEAR in options:
        return _log(log_path, _unknown(question, "criteria must be non-empty and must not name the escape option"))
    if not (0.0 <= threshold <= 1.0):
        return _log(log_path, _unknown(question, "threshold must be in [0, 1]"))
    if len(state) > MAX_STATE_CHARS:
        return _log(log_path, _unknown(question, f"state has {len(state)} chars, limit is {MAX_STATE_CHARS}"))
    try:
        key = resolve_key(key_file)
    except KeyFileError as exc:
        return _log(log_path, _unknown(question, str(exc)))
    if not key:
        return _log(log_path, _unknown(question, "RouterAI key file is missing or empty"))
    try:
        url = base_url()
        budget = daily_budget()
    except ValueError as exc:
        return _log(log_path, _unknown(question, str(exc)))
    if budget is not None:
        limit, state_path = budget
        allowed, reason = reserve_daily_call(limit, state_path, datetime.now(timezone.utc).date().isoformat())
        if not allowed:
            return _log(log_path, _unknown(question, reason))

    all_options = options + [UNCLEAR]
    body = json.dumps(
        {
            "model": model_alias(),
            "state": state,
            "questions": {
                question: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": {**criteria, UNCLEAR: "none of the above fits, or the state does not decide it"},
                }
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    post = http_post or _post
    try:
        raw = post(f"{url}/api/v1/systemone", body, key)
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode("utf-8", errors="replace").replace("\n", " ")
        return _log(log_path, _unknown(question, f"RouterAI HTTP {exc.code}: {detail}"))
    except (urllib.error.URLError, OSError) as exc:
        return _log(log_path, _unknown(question, f"RouterAI request failed: {exc}"))

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return _log(log_path, _unknown(question, "RouterAI returned non-JSON"))
    answer = (payload.get("answers") or {}).get(question) if isinstance(payload, dict) else None
    if not isinstance(answer, dict):
        return _log(log_path, _unknown(question, f"RouterAI returned no answer for {question!r}"))
    choice = answer.get("choice")
    if choice not in all_options:
        return _log(log_path, _unknown(question, f"RouterAI choice {choice!r} is not an offered option"))
    probabilities = _valid_probabilities(answer.get("probabilities"), all_options)
    if probabilities is None:
        return _log(log_path, _unknown(question, "RouterAI probabilities are missing or out of range"))
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        return _log(log_path, _unknown(question, "RouterAI confidence is missing or out of range"))
    confidence = float(confidence)

    if choice == UNCLEAR:
        return _log(log_path, Decision(question, UNCLEAR, probabilities, confidence, "model chose the escape option"))
    if confidence < threshold:
        return _log(
            log_path,
            Decision(question, None, probabilities, confidence, f"confidence {confidence:.2f} below threshold {threshold:.2f}; escalate to a mind"),
        )
    return _log(log_path, Decision(question, choice, probabilities, confidence, "decided"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the bearer header is never re-sent to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url if req else "", code, "redirect refused", headers, fp)


def _post(url: str, body: bytes, key: str) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def _log(path: pathlib.Path | None, decision: Decision) -> Decision:
    """Append one answer to the decision log. The later real outcome is recorded separately."""
    if path is None:
        return decision
    record = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "question": decision.question,
        "choice": decision.choice,
        "confidence": decision.confidence,
        "probabilities": decision.probabilities,
        "reason": decision.reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return decision


CONTROL_CRITERIA = {
    "alpha": "the message names a lookup or check that can settle a routing question",
    "beta": "the message is a request for a patch review of diff text",
}
CONTROL_CASES = (
    ("alpha", "Route this: the owner-map lookup for path docs/mesh.md returned role docs."),
    ("beta", "Review this diff: + def f(x): return x * 2"),
)


def control(*, threshold: float = 0.0, key_file: str | None = None) -> list[str]:
    """Live drill: two synthetic questions with known answers. Returns a list of problems (empty = pass)."""
    problems: list[str] = []
    for expected, state in CONTROL_CASES:
        decision = decide(
            "control",
            "Pick the option that matches the state. Answer unclear if neither fits.",
            state,
            CONTROL_CRITERIA,
            threshold=threshold,
            key_file=key_file,
        )
        if decision.choice is None:
            problems.append(f"expected {expected}: unknown ({decision.reason})")
        elif decision.choice != expected:
            problems.append(f"expected {expected}: got {decision.choice} (confidence {decision.confidence})")
    return problems


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        empty = decide("q", "x", "s", {}, threshold=0.5)
        if empty.decided or empty.reason == "":
            print("FAIL empty criteria accepted")
            return 1
        bad = decide("q", "x", "s", {"a": "one"}, threshold=2.0)
        if bad.decided:
            print("FAIL bad threshold accepted")
            return 1
        print("PASS self-test: offline contract")
        return 0
    if "--control" in argv:
        problems = control()
        if problems:
            for line in problems:
                print("FAIL", line)
            return 1
        print("PASS control: both synthetic questions answered as expected")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
