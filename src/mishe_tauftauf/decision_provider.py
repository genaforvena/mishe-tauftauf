"""One decision-provider path for every System One question.

A provider is configuration, not code: a name, a protocol, a base URL, a key
file, a model, a daily call cap and a timeout, read from a JSON file. Adding a
model or a provider is a config line. This module owns the one transport (https
only, loopback http allowed, redirects refused, the key read from a private file
and never placed in a message, argument or reason), the one daily ledger and the
one error model: every failure is an unknown answer with a reason, never a
silent default.

Two question kinds sit on top:
  - yes/no probability: the questions and thresholds of judges.py, unchanged;
  - choice: one label from a criteria record, an always-present escape option,
    per-option probabilities, a confidence, and an escalation threshold.

Protocols: only "systemone" (POST {base_url}/api/v1/systemone). Any other
protocol is rejected when the config loads, so a config line cannot select a
path that is not built.

Config file shape:
    {"providers": {"routerai": {"protocol": "systemone",
        "base_url": "https://routerai.ru", "key_file": "/abs/path/routerai.key",
        "model": "typesafe/jev-1.14", "daily_cap": 100, "timeout": 30}}}
An optional "budget_file" (absolute) overrides the default ledger path.

Secrets: the bearer key is read from key_file and only ever placed in the HTTP
Authorization header. Reasons, logs and exceptions never carry it.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import math
import os
import pathlib
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

from .judges import QUESTIONS, Judgment, classify, document

PROTOCOLS = frozenset({"systemone"})
UNCLEAR = "unclear"
UNCLEAR_CRITERION = "none of the above fits, or the state does not decide it"
MAX_STATE_CHARS = 20_000
MAX_RESPONSE_BYTES = 64_000
MAX_DAILY_CAP = 1000
MAX_TIMEOUT_SECONDS = 30.0
YES_NO = {"yes": "the state satisfies the question", "no": "the state does not satisfy the question"}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 mishe-tauftauf-decision"
)
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_FIELDS = frozenset({"protocol", "base_url", "key_file", "model", "daily_cap", "timeout", "budget_file"})
_REQUIRED = _FIELDS - {"budget_file"}

Post = Callable[[str, bytes, str, float], bytes]


class ProviderConfigError(ValueError):
    """The provider config is unreadable or names an unusable provider."""


class KeyFileError(ValueError):
    """The key file exists but is not safe to read. The message never contains the key."""


@dataclass(frozen=True)
class Provider:
    name: str
    protocol: str
    base_url: str
    key_file: pathlib.Path
    model: str
    daily_cap: int
    timeout: float
    budget_file: pathlib.Path


@dataclass(frozen=True)
class Decision:
    """One choice answer. choice is None when the rung cannot decide."""

    question: str
    choice: str | None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    reason: str = ""

    @property
    def decided(self) -> bool:
        return self.choice is not None and self.choice != UNCLEAR


# ---- configuration -------------------------------------------------------

def _checked_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ProviderConfigError(f"base_url is not a valid URL: {exc}") from exc
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed = {"https", "http"} if local else {"https"}
    if (not parsed.hostname or parsed.scheme not in allowed or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ProviderConfigError("base_url requires https (http only on loopback), without credentials, query or fragment")
    return url


def _provider(name: str, entry: object) -> Provider:
    if not _NAME_RE.fullmatch(name):
        raise ProviderConfigError(f"provider name {name!r} must be a lowercase slug")
    if not isinstance(entry, dict) or not _REQUIRED <= set(entry) or set(entry) - _FIELDS:
        raise ProviderConfigError(f"provider {name!r} needs exactly {sorted(_REQUIRED)} plus optional budget_file")
    if entry["protocol"] not in PROTOCOLS:
        raise ProviderConfigError(f"provider {name!r} protocol must be one of {sorted(PROTOCOLS)}")
    base_url = _checked_base_url(str(entry["base_url"]))
    key_file = pathlib.Path(str(entry["key_file"])).expanduser()
    if not key_file.is_absolute():
        raise ProviderConfigError(f"provider {name!r} key_file must be an absolute path")
    model = entry["model"]
    if not isinstance(model, str) or not model.strip():
        raise ProviderConfigError(f"provider {name!r} model must be a non-empty string")
    cap = entry["daily_cap"]
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= MAX_DAILY_CAP:
        raise ProviderConfigError(f"provider {name!r} daily_cap must be an integer in [0, {MAX_DAILY_CAP}]")
    timeout = entry["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
            or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ProviderConfigError(f"provider {name!r} timeout must be in (0, {MAX_TIMEOUT_SECONDS:g}] seconds")
    if "budget_file" in entry:
        budget = pathlib.Path(str(entry["budget_file"])).expanduser()
    else:
        state_home = pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local" / "state")
        budget = state_home / "mishe-tauftauf" / f"decision-{name}-budget.json"
    if not budget.is_absolute():
        raise ProviderConfigError(f"provider {name!r} budget_file must be an absolute path")
    return Provider(name, entry["protocol"], base_url, key_file, model.strip(), cap, float(timeout), budget)


def load_providers(path: pathlib.Path) -> dict[str, Provider]:
    """Read the provider config. Any problem raises ProviderConfigError; nothing is defaulted."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderConfigError(f"provider config is unreadable: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict) or set(raw) != {"providers"}:
        raise ProviderConfigError("provider config needs exactly one 'providers' object")
    return {name: _provider(name, entry) for name, entry in raw["providers"].items()}


def select(providers: dict[str, Provider], name: str) -> Provider:
    if name not in providers:
        raise ProviderConfigError(f"no provider named {name!r} in the config")
    return providers[name]


# ---- key, transport, ledger ---------------------------------------------

def read_key(path: pathlib.Path) -> str | None:
    """Read the bearer key from a private key file. Refuses group/other readable or foreign files."""
    try:
        status = path.stat()
    except OSError:
        return None
    if status.st_mode & 0o077:
        raise KeyFileError("key file is readable by group or others; make it mode 600")
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise KeyFileError("key file is not owned by this user")
    try:
        text = path.read_text(encoding="utf-8")
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
        return line.strip("\"'") or None
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the bearer header is never re-sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url if req else "", code, "redirect refused", headers, fp)


def _post(url: str, body: bytes, key: str, timeout: float) -> bytes:
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
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds the size limit")
    return raw


def _private_regular(fd: int) -> bool:
    info = os.fstat(fd)
    return stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()


def _ledger_count(path: pathlib.Path, current_day: str) -> int:
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
    if not isinstance(stored_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stored_day) \
            or type(count) is not int or count < 0:
        raise ValueError("budget state has invalid values")
    try:
        date.fromisoformat(stored_day)
    except ValueError as exc:
        raise ValueError("budget state has invalid date") from exc
    if stored_day > current_day:
        raise ValueError("budget state is dated in the future")
    return count if stored_day == current_day else 0


def reserve_call(provider: Provider, current_day: str) -> str:
    """Reserve one call before HTTP. Returns '' when reserved, else the reason. A failed request still spends one."""
    path = provider.budget_file
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not _private_regular(lock_fd):
                raise ValueError("budget lock is not a private regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            count = _ledger_count(path, current_day)
            if count >= provider.daily_cap:
                return f"{provider.name} daily budget exhausted"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".decision-budget-",
                                                 encoding="utf-8", delete=False) as stream:
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
            return ""
        finally:
            os.close(lock_fd)
    except (OSError, ValueError) as exc:
        return f"{provider.name} daily budget unavailable: {exc}"


# ---- one request builder and one parser for the systemone protocol -------

def _systemone_body(provider: Provider, question: str, instructions: str, state: str, options: dict[str, str]) -> bytes:
    criteria = {**options, UNCLEAR: UNCLEAR_CRITERION}
    return json.dumps(
        {
            "model": provider.model,
            "state": state,
            "questions": {question: {"type": "choice", "instructions": instructions, "criteria": criteria}},
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _probabilities(raw: object, labels: list[str]) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    clean: dict[str, float] = {}
    for label in labels:
        value = raw.get(label)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            return None
        clean[label] = number
    return clean


def _call(provider: Provider, question: str, instructions: str, state: str, options: dict[str, str],
          post: Post | None = None) -> tuple[dict | None, str]:
    """The one transport and error model. Returns (answer, '') or (None, reason). Never raises."""
    if len(state) > MAX_STATE_CHARS:
        return None, f"state has {len(state)} chars, limit is {MAX_STATE_CHARS}"
    try:
        key = read_key(provider.key_file)
    except KeyFileError as exc:
        return None, str(exc)
    if not key:
        return None, f"{provider.name} key file is missing or empty"
    spent = reserve_call(provider, datetime.now(timezone.utc).date().isoformat())
    if spent:
        return None, spent
    body = _systemone_body(provider, question, instructions, state, options)
    try:
        raw = (post or _post)(f"{provider.base_url}/api/v1/systemone", body, key, provider.timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode("utf-8", errors="replace").replace("\n", " ")
        return None, f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        return None, f"request failed: {exc}"
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None, "returned non-JSON"
    answers = payload.get("answers") if isinstance(payload, dict) else None
    answer = answers.get(question) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        return None, f"returned no answer for {question!r}"
    labels = list(options) + [UNCLEAR]
    choice = answer.get("choice")
    if choice not in labels:
        return None, f"choice {choice!r} is not an offered option"
    probabilities = _probabilities(answer.get("probabilities"), labels)
    if probabilities is None:
        return None, "probabilities are missing or out of range"
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0.0 <= float(confidence) <= 1.0:
        return None, "confidence is missing or out of range"
    return {"choice": choice, "probabilities": probabilities, "confidence": float(confidence)}, ""


# ---- the two question kinds ---------------------------------------------

def decide(provider: Provider, question: str, instructions: str, state: str, criteria: dict[str, str], *,
           threshold: float, post: Post | None = None) -> Decision:
    """Choice question. Decided only when the confidence clears threshold; otherwise the caller escalates."""
    if not criteria or UNCLEAR in criteria or not all(isinstance(v, str) and v.strip() for v in criteria.values()):
        return Decision(question, None, reason="criteria must be non-empty one-line descriptions and must not name the escape option")
    if not 0.0 <= threshold <= 1.0:
        return Decision(question, None, reason="threshold must be in [0, 1]")
    answer, reason = _call(provider, question, instructions, state, criteria, post)
    if answer is None:
        return Decision(question, None, reason=reason)
    choice, probabilities, confidence = answer["choice"], answer["probabilities"], answer["confidence"]
    if choice == UNCLEAR:
        return Decision(question, UNCLEAR, probabilities, confidence, "model chose the escape option")
    if confidence < threshold:
        return Decision(question, None, probabilities, confidence,
                        f"confidence {confidence:.2f} below threshold {threshold:.2f}; escalate to a mind")
    return Decision(question, choice, probabilities, confidence, "decided")


def judge(provider: Provider, question: str, slug: str, top_pain: str, evidence: str,
          prediction: str | None = None, *, post: Post | None = None) -> Judgment:
    """Yes/no probability question with the judges.py questions and thresholds, unchanged."""
    text = document(question, slug, top_pain, evidence, prediction)
    answer, reason = _call(provider, question, QUESTIONS[question], text, YES_NO, post)
    if answer is None:
        return Judgment(question, None, "unknown", reason, text)
    if answer["choice"] == UNCLEAR:
        return Judgment(question, None, "unknown", "model chose the escape option", text)
    probability = answer["probabilities"]["yes"]
    return Judgment(question, probability, classify(question, probability), "decision provider judgment", text)
