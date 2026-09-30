#!/usr/bin/env python3
"""Optional System One judge adapter for the hosted TypeSafe Jev model.

Protocol, not a dependency. mishe-tauftauf feeds a judgment document on stdin:

    QUESTION <name>
    INSTRUCTIONS <...>
    TOP PAIN <slug>
    <top pain text>
    EVIDENCE <...>
    [PREDICTION <...>]

and this adapter answers with the one line every external judge must emit:

    probability <P(yes)>          in [0, 1]
    unknown <reason>              when it cannot decide

The core never executes the answer, so a judge may only fail visibly: any
network problem, HTTP error, malformed response, or out-of-range value becomes
`unknown <reason>`, which mishe-tauftauf treats as needing a System Two Mind
rather than as quiet agreement.

Jev is a hosted service. It needs TYPESAFE_API_KEY in the environment (or in a
file named by TYPESAFE_KEY_FILE) and a base URL via TYPESAFE_BASE_URL. It is
reached over plain stdlib HTTPS with no third-party packages, so installing it
still costs the core nothing.

The bearer key is a secret. Never commit it, and never print it: this adapter
logs only the base URL, the model alias, and the outcome.

This is a reference adapter, not a calibration claim. Its probabilities are a
model's answer, so mishe-tauftauf keeps the same thresholds it uses for every
other judge and never takes one as proof of a real observation.
"""

from __future__ import annotations

import json
import fcntl
import os
import pathlib
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 mishe-tauftauf-jev-judge"
)
# Sized above the largest judgment document the core builds rather than fitted to a model.
MAX_STATE_CHARS = 100_000
TIMEOUT_SECONDS = 60
MAX_DAILY_CALL_LIMIT = 1000


def daily_budget_configuration() -> tuple[int, pathlib.Path] | None:
    """An unset limit preserves the adapter's original behavior."""
    raw = os.environ.get("TYPESAFE_DAILY_CALL_LIMIT")
    if raw is None:
        return None
    if not re.fullmatch(r"[0-9]{1,4}", raw) or int(raw) > MAX_DAILY_CALL_LIMIT:
        raise ValueError("TypeSafe budget limit must be an integer in [0, 1000]")
    configured = os.environ.get("TYPESAFE_BUDGET_FILE")
    if configured:
        path = pathlib.Path(configured)
    else:
        state_home = pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local" / "state")
        path = state_home / "mishe-tauftauf" / "jev-budget.json"
    if not path.is_absolute():
        raise ValueError("TypeSafe budget path must be absolute")
    return int(raw), path


def _private_regular(fd: int) -> bool:
    info = os.fstat(fd)
    return stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()


def _existing_state(path: pathlib.Path, current_day: str) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 0
    try:
        if not _private_regular(fd):
            raise ValueError("budget state is not a private regular file")
        raw = os.read(fd, 257)
        if len(raw) > 256:
            raise ValueError("budget state is oversized")
    finally:
        os.close(fd)
    try:
        state = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("budget state is corrupt") from exc
    if not isinstance(state, dict) or set(state) != {"date", "count"}:
        raise ValueError("budget state has invalid fields")
    stored_day, count = state["date"], state["count"]
    if not isinstance(stored_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stored_day) or type(count) is not int or count < 0:
        raise ValueError("budget state has invalid values")
    try:
        date.fromisoformat(stored_day)
    except ValueError as exc:
        raise ValueError("budget state has invalid date") from exc
    if stored_day > current_day:
        raise ValueError("budget state is dated in the future")
    return count if stored_day == current_day else 0


def reserve_daily_call(limit: int, path: pathlib.Path, current_day: str) -> tuple[bool, str]:
    """Reserve before HTTP; failed HTTP requests still spend one call."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not _private_regular(lock_fd):
                raise ValueError("budget lock is not a private regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            count = _existing_state(path, current_day)
            if count >= limit:
                return False, "TypeSafe daily budget exhausted"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".jev-budget-", encoding="utf-8", delete=False) as stream:
                    temporary = pathlib.Path(stream.name)
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump({"date": current_day, "count": count + 1}, stream, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return True, ""
        finally:
            os.close(lock_fd)
    except (OSError, ValueError) as exc:
        return False, f"TypeSafe daily budget unavailable: {exc}"


def _key_file_env(path: pathlib.Path) -> str | None:
    """Read KEY=value lines from a private file outside the repository."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "TYPESAFE_API_KEY":
            stripped = value.strip()
            return stripped[1:-1] if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'" else stripped
    return None


def resolve_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    path = os.environ.get("TYPESAFE_KEY_FILE")
    if path:
        return _key_file_env(pathlib.Path(path))
    return None


def base_url() -> str:
    url = os.environ.get("TYPESAFE_BASE_URL", "").strip() or DEFAULT_BASE_URL
    if not url.startswith(("http://", "https://")):
        raise ValueError("TYPESAFE_BASE_URL must be an http(s) URL")
    return url.rstrip("/")


def model_alias() -> str:
    return os.environ.get("TYPESAFE_MODEL", "").strip() or DEFAULT_MODEL


def question_name(document: str) -> str | None:
    """The core's own heading names the judgment; a blank or missing one is unknown."""
    first = next((line for line in document.splitlines() if line.strip()), "")
    if not first.startswith("QUESTION "):
        return None
    name = first[len("QUESTION "):].strip()
    return name or None


def question_instructions(document: str) -> str | None:
    """The wording the core already uses to ask this question.

    Jev answers the `instructions` field, so the adapter must not paraphrase it:
    passing a generic wrapper here moves the actual question into the state text and
    collapses the model's separation between what is asked and what is evidence.
    """
    lines = document.splitlines()
    try:
        index = next(i for i, line in enumerate(lines) if line.strip() == "INSTRUCTIONS")
    except StopIteration:
        return None
    body = []
    for line in lines[index + 1:]:
        if line.strip() and line.strip() == line.strip().upper() and line.strip().endswith(("PAIN", "EVIDENCE", "PREDICTION")):
            break
        body.append(line)
    text = "\n".join(body).strip()
    return text or None


def build_state(document: str) -> tuple[str, str]:
    """Validate size. Returns (state, reason); a non-empty reason means unusable."""
    if not document:
        return "", "empty judgment document"
    if len(document) > MAX_STATE_CHARS:
        return "", f"document has {len(document)} chars, limit is {MAX_STATE_CHARS}; no truncation"
    return document, ""


def evaluate(document: str) -> tuple[float | None, str]:
    """Ask Jev the one yes/no question the core's threshold applies to.

    Returns (probability, reason). Probability is None whenever the answer is
    not a finite value in [0, 1], and reason explains why rather than letting a
    caller treat silence as zero.
    """
    key = resolve_key()
    if not key:
        return None, "TYPESAFE_API_KEY is not set (env or TYPESAFE_KEY_FILE)"
    try:
        url = base_url()
        name = question_name(document)
        if name is None:
            return None, "document has no QUESTION <name> heading"
        instructions = question_instructions(document)
        if not instructions:
            return None, "document has no INSTRUCTIONS section"
        state, state_reason = build_state(document)
    except ValueError as exc:
        return None, str(exc)
    if state_reason:
        return None, state_reason

    body = json.dumps(
        {
            "model": model_alias(),
            "state": state,
            "questions": {name: {"type": "noul", "instructions": instructions}},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        budget = daily_budget_configuration()
    except ValueError as exc:
        return None, str(exc)
    if budget is not None:
        limit, state_path = budget
        allowed, reason = reserve_daily_call(limit, state_path, datetime.now(timezone.utc).date().isoformat())
        if not allowed:
            return None, reason
    request = urllib.request.Request(
        f"{url}/v1/systemone",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # The endpoint rejects the default urllib signature with an access-denied error.
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", errors="replace").replace("\n", " ")
        return None, f"TypeSafe HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return None, f"TypeSafe connection failed: {exc.reason}"
    except OSError as exc:
        return None, f"TypeSafe request failed: {exc}"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"TypeSafe returned non-JSON: {exc}"

    answer = (payload.get("answers") or {}).get(name)
    if not isinstance(answer, dict):
        return None, f"TypeSafe returned no answer for question {name!r}"
    value = answer.get("noul")
    if isinstance(value, bool):  # JSON true/false is not a probability
        value = None
    if not isinstance(value, (int, float)):
        return None, f"TypeSafe noul answer is not numeric: {answer.get('noul')!r}"
    number = float(value)
    if number != number or number < 0.0 or number > 1.0:  # NaN or out of range
        return None, f"TypeSafe probability outside [0,1]: {number}"
    return number, payload.get("model", model_alias())


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if "--self-test" in argv:
        # Offline: proves the envelope contract, not that the network is up.
        empty = evaluate("")
        print("empty-document:", "unknown" if empty[0] is None else "unexpected probability")
        if empty[0] is not None:
            return 1
        malformed = evaluate("no question heading\njust evidence")
        print("no-question-heading:", "unknown" if malformed[0] is None else "unexpected probability")
        if malformed[0] is not None:
            return 1
        oversized = evaluate("QUESTION publish\nINSTRUCTIONS x\nEVIDENCE " + "x" * (MAX_STATE_CHARS + 1))
        if oversized[0] is None:
            print("oversized-document: unknown", oversized[1])
        else:
            print("oversized-document: unexpected accept")
            return 1
        print("PASS self-test: envelope contract")
        return 0

    document = sys.stdin.read()
    probability, reason = evaluate(document)
    if probability is None:
        print(f"unknown {reason}")
        return 0
    print(f"probability {probability:.17g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
