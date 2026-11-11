"""Inner engine router: per-role ordered candidates chosen from live account windows.

Site file ``engine/routes.json`` (one file, the only place candidates live)::

    {"version": 1, "cool_off_s": 1800, "env": {"PATH": "..."},
     "roles": {"health": [{"engine": "omp", "model": "opencode-go/beam-free", "account": "go-a"},
                          {"engine": "claude", "model": "claude-haiku-5-5", "account": null}]}}

A candidate is ``engine`` (``claude`` or ``omp``), ``model`` and ``account`` (a
registry alias, or null for the Claude subscription safety net, which has no
window reading in this router). A candidate may also carry ``max_parallel``: the
number of wakes that may use it at once. The router counts the candidate's
running wakes from the routing ledger and skips a candidate that is at its cap,
so the extra wake takes the next candidate instead of hitting the provider's
rate limit (operator DM 299311).

Choice is the first candidate in order that is not cooling off and is admitted
by :mod:`engine_policy` on the account windows in ``engine/budget.json`` (the
budget producer's one-source file; :func:`live_status` reads it). Admission needs
a FRESH account; UNKNOWN, STALE and UNREADABLE accounts are never evidence of
capacity and are skipped. A candidate with no account (a free model or the Claude
safety net) reads no window, so a missing file does not stop it.

Outcomes are judged from the wake's own yield receipt, not from the engine exit
code alone. A candidate that does not start falls through to the next one within
the same wake (no effect happened). A candidate whose wake times out, exits
without a yield, or leaves no receipt cools off for ``cool_off_s``; the next wake
of the role then starts on the next candidate, and the cooled candidate is probed
again once its cool-off ends. A provider rate limit (HTTP 429) before any tool
call is refusal-class: the wake falls through at once and the candidate cools off
for that role only, for ``rate_limit_cool_off_s`` (doubling on repeats), because
a rate limit is a transient provider condition and not a quota window. Every
selection, fallback and outcome is appended to ``engine/routing-ledger.jsonl``.
The ``engine-routing`` signal is GREEN when every role has a working candidate
and all account evidence is FRESH, UNKNOWN when evidence is stale or missing, and
RED when some role has no working candidate.
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import engine_policy as policy
from .router_text import file_lines, iso_ms, ledger_events, load_json, retry_after_ms, split_cooling_key
from . import signal_store
from . import yielding
from .wake import (EngineExhausted, EngineNotStarted, EngineRateLimited, EngineRejected, EngineTimeout)

ROUTES = "routes.json"
STATE = "routing-state.json"
LEDGER = "routing-ledger.jsonl"
SIGNAL = "engine-routing"
SIGNAL_MAX_AGE = 900.0
DEFAULT_COOL_OFF_S = 1800
DEFAULT_ACCOUNT_COOL_OFF_S = 5400  # a quota-exhausted account is out for a window, not a role retry
DEFAULT_HARD_TIMEOUT_S = 3600     # total runtime a wake may reach when it is still producing output
DEFAULT_RATE_LIMIT_COOL_OFF_S = 300   # a rate limit waits minutes, not the site-wide window
MAX_RETRY_AFTER_COOL_OFF_S = 28800   # cap for a provider-stated retry-after, 8 h
MAX_RATE_LIMIT_COOL_OFF_S = 1800    # ceiling of the self-computed doubling backoff, the site-wide window
ENGINES = ("claude", "omp")
STALE_STATES = ("STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE")


def _dir(home) -> Path:
    return Path(home) / "engine"


def _iso(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate_key(c: dict) -> str:
    return f"{c['engine']}:{c['model']}:{c['account'] or '-'}"


def role_key(role: str, key: str) -> str:
    """Cool-off key for a (role, candidate) pair: a timeout belongs to the role's task, not the model."""
    return f"{role}|{key}"


def cooling_table(cooling: dict, now_ms: float, cool_off_s: float = DEFAULT_COOL_OFF_S) -> list:
    """Active cool-offs: scope 'all' (provider evidence) or the role (timeouts), with age and time left."""
    rows = []
    for k, until in sorted(cooling.items()):
        if not isinstance(until, (int, float)) or until <= now_ms:
            continue
        role, cand = split_cooling_key(k)
        rows.append({"role": role or "all", "candidate": cand, "until": _iso(until),
                     "age_s": max(0, round(now_ms / 1000 - (until / 1000 - cool_off_s))),
                     "left_s": round((until - now_ms) / 1000)})
    return rows


def _is_safety_net(c: dict) -> bool:
    return c["engine"] == "claude" and c["account"] is None


def load_routes(home) -> dict | None:
    """The validated route table, or None when the site has no routes file."""
    path = _dir(home) / ROUTES
    if not path.is_file():
        return None
    value = load_json(path)
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("roles"), dict):
        raise ValueError("invalid engine routes file")
    roles = {}
    for role, cands in value["roles"].items():
        if not isinstance(role, str) or not isinstance(cands, list) or not cands:
            raise ValueError(f"invalid engine routes for {role!r}")
        clean = []
        for c in cands:
            if not isinstance(c, dict) or c.get("engine") not in ENGINES or not isinstance(c.get("model"), str):
                raise ValueError(f"invalid engine candidate for {role!r}: {c!r}")
            account = c.get("account")
            if account is not None and not isinstance(account, str):
                raise ValueError(f"invalid account for {role!r}: {c!r}")
            free, thinking = c.get("free", False), c.get("thinking")
            if not isinstance(free, bool) or (free and (c["engine"] != "omp" or account is not None)):
                raise ValueError(f"'free' needs an omp candidate with no account for {role!r}: {c!r}")
            if thinking is not None and not isinstance(thinking, str):
                raise ValueError(f"invalid thinking for {role!r}: {c!r}")
            tmo = c.get("timeout_s")
            if tmo is not None and (isinstance(tmo, bool) or not isinstance(tmo, (int, float)) or tmo <= 0):
                raise ValueError(f"invalid timeout_s for {role!r}: {c!r}")
            hard = c.get("hard_timeout_s")
            if hard is not None and (isinstance(hard, bool) or not isinstance(hard, (int, float)) or hard <= 0):
                raise ValueError(f"invalid hard_timeout_s for {role!r}: {c!r}")
            cap = c.get("max_parallel")
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
                raise ValueError(f"invalid max_parallel for {role!r}: {c!r}")
            entry = {"engine": c["engine"], "model": c["model"], "account": account}
            if tmo is not None:
                entry["timeout_s"] = tmo
            if hard is not None:
                entry["hard_timeout_s"] = hard
            if cap is not None:
                entry["max_parallel"] = cap
            if free:
                entry["free"] = True
            if thinking is not None:
                entry["thinking"] = thinking
            clean.append(entry)
        roles[role] = clean
    env = value.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("invalid engine routes env")
    cool = value.get("cool_off_s", DEFAULT_COOL_OFF_S)
    if isinstance(cool, bool) or not isinstance(cool, (int, float)) or cool < 0:
        raise ValueError("invalid cool_off_s")
    account_cool = value.get("account_cool_off_s", DEFAULT_ACCOUNT_COOL_OFF_S)
    if isinstance(account_cool, bool) or not isinstance(account_cool, (int, float)) or account_cool < 0:
        raise ValueError("invalid account_cool_off_s")
    hard = value.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S)
    if isinstance(hard, bool) or not isinstance(hard, (int, float)) or hard <= 0:
        raise ValueError("invalid hard_timeout_s")
    rate_cool = value.get("rate_limit_cool_off_s", DEFAULT_RATE_LIMIT_COOL_OFF_S)
    if isinstance(rate_cool, bool) or not isinstance(rate_cool, (int, float)) or rate_cool < 0:
        raise ValueError("invalid rate_limit_cool_off_s")
    max_rate_cool = value.get("max_rate_limit_cool_off_s", MAX_RATE_LIMIT_COOL_OFF_S)
    if isinstance(max_rate_cool, bool) or not isinstance(max_rate_cool, (int, float)) or max_rate_cool < rate_cool:
        raise ValueError("invalid max_rate_limit_cool_off_s")
    return {"roles": roles, "cool_off_s": cool, "account_cool_off_s": account_cool, "env": env,
            "hard_timeout_s": hard, "rate_limit_cool_off_s": rate_cool,
            "max_rate_limit_cool_off_s": max_rate_cool}


def _load_state(home) -> dict:
    path = _dir(home) / STATE
    if not path.is_file():
        return {"cooling": {}}
    value = load_json(path)
    cooling = value.get("cooling") if isinstance(value, dict) else None
    rate_limits = value.get("rate_limits") if isinstance(value, dict) else None
    return {"cooling": cooling if isinstance(cooling, dict) else {},
            "rate_limits": rate_limits if isinstance(rate_limits, dict) else {}}


def _save_state(home, state: dict) -> None:
    directory = _dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (STATE + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, directory / STATE)


def record(home, event: dict, *, now_ms: float) -> None:
    """Append one routing event to the ledger (append-only, one JSON object per line)."""
    directory = _dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    line = dict(event, at=_iso(now_ms))
    with open(directory / LEDGER, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def _at_ms(value) -> float | None:
    """A ledger timestamp as epoch milliseconds, or None when it cannot be read."""
    return iso_ms(value)


def running_candidates(home, *, now_ms: float, horizon_s: float) -> dict:
    """Candidates with a wake still running, counted from the ledger's open selections.

    A ``select`` opens an attempt on a candidate; the matching ``outcome`` or ``fallback``
    closes it. An attempt whose wake never reported an outcome (an interrupted wake) stops
    counting once it is older than the horizon, so a dead wake cannot hold a candidate at its
    parallelism cap for ever.
    """
    counts: dict = {}
    open_at: dict = {}
    for event in ledger_events(_dir(home) / LEDGER):
        key, wake = event.get("key"), event.get("wake")
        if not key:
            continue
        at = _at_ms(event.get("at"))
        if at is None:
            continue
        if event.get("event") == "select":
            open_at[(wake, key)] = at
        elif event.get("event") in ("outcome", "fallback"):
            open_at.pop((wake, key), None)
    horizon_ms = horizon_s * 1000
    for (_wake, key), at in open_at.items():
        if now_ms - at > horizon_ms:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _eligible(role: str, cand: dict, status, now_ms) -> tuple[bool, str]:
    if cand.get("free") is True:
        return True, "free model (no account window to read; failures cool it off)"
    if _is_safety_net(cand):
        return True, "Claude subscription safety net (no account window read in the router)"
    alias = cand["account"]
    if status is None:
        return False, "live account status unavailable"
    row = (status.get("accounts") or {}).get(alias) if alias else None
    if not isinstance(row, dict):
        return False, f"{alias} is not in the account registry"
    if row.get("state") != "FRESH":
        return False, f"{alias} {row.get('state')}: {row.get('reason') or 'no fresh reading'}"
    route = {"provider": row.get("provider"), "alias": alias, "model": cand["model"],
             "effort": "high", "runner": cand["engine"]}
    result = policy.decide(role=role, status=status, allowlist={role: [route]},
                           current_route=None, now_ms=now_ms, reservations=None)
    if result.get("decision") == "select":
        return True, result.get("reason") or f"{alias} healthy"
    return False, f"{alias} {result.get('decision')}: {result.get('reason')}"


def choose(role: str, candidates: list, status, cooling: dict, now_ms: float,
           exclude: frozenset = frozenset(), running: dict | None = None) -> dict:
    """Pure choice: the first candidate that is not excluded, not cooling and admitted."""
    trail = []
    for c in candidates:
        key = candidate_key(c)
        if key in exclude:
            trail.append({"key": key, "verdict": "tried-this-wake"})
            continue
        until = max((v for v in (cooling.get(key), cooling.get(role_key(role, key)))
                     if isinstance(v, (int, float))), default=None)
        if until is not None and until > now_ms:
            trail.append({"key": key, "verdict": "cooling", "until": _iso(until)})
            continue
        cap = c.get("max_parallel")
        if cap is not None and running:
            live = running.get(key, 0)
            if live >= cap:
                trail.append({"key": key, "verdict": "at-capacity",
                             "reason": f"{live} running wake(s) reach the cap of {cap}"})
                continue
        ok, reason = _eligible(role, c, status, now_ms)
        trail.append({"key": key, "verdict": "eligible" if ok else "skipped", "reason": reason})
        if ok:
            return {"decision": "select", "candidate": c, "key": key, "trail": trail}
    return {"decision": "none", "candidate": None, "key": None, "trail": trail}


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _budget_windows(windows) -> list:
    """The budget.json windows of one account in the window shape :mod:`engine_policy` reads.

    The file keys its windows by label (``5h``, ``7d``) and stores ``remaining`` and an
    epoch ``reset_at``; the policy iterates window rows with ``remaining_fraction`` and an
    ISO ``reset_at``. This is that format mapping, and nothing else: no value is computed
    and a window missing either number is left out rather than guessed at.
    """
    rows = []
    for name, w in (windows if isinstance(windows, dict) else {}).items():
        if not isinstance(w, dict):
            continue
        remaining, reset_at = w.get("remaining"), w.get("reset_at")
        if _is_num(remaining) and _is_num(reset_at):
            rows.append({"window": name, "limit": name, "remaining_fraction": float(remaining),
                         "reset_at": _iso(reset_at * 1000), "provider_status": None, "state": w.get("state")})
    return rows


def live_status(home):
    """Account windows from ``engine/budget.json``, or None when it is unreadable or too old.

    One source: the budget producer writes the file and its duty owns refetching stale
    provider readings, so the router reads and never computes. A missing, malformed,
    wrong-schema or older-than-``MAX_AGE_S`` file is None (UNKNOWN downstream), never an
    empty reading.
    """
    from . import budget_producer
    try:
        data = load_json(_dir(home) / "budget.json")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != budget_producer.SCHEMA:
        return None
    produced_at = data.get("produced_at")
    if not _is_num(produced_at) or time.time() - produced_at > budget_producer.MAX_AGE_S:
        return None
    return status_from_payload(data)


def status_from_payload(data: dict) -> dict | None:
    """The router's status shape from a schema-1 budget payload (shared by live_status and the producer)."""
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return None
    return {"state": "FRESH", "unknowns": data.get("unknowns") or [],
            "accounts": {alias: {"provider": row.get("provider"), "state": row.get("state"),
                                 "reason": row.get("reason"), "windows": _budget_windows(row.get("windows"))}
                         for alias, row in accounts.items() if isinstance(row, dict)}}


def _windowed(routes: dict) -> bool:
    """True when some candidate reads an account window, so account status is required."""
    return any(c.get("account") for cands in (routes.get("roles") or {}).values() for c in cands)


def assess(routes: dict | None, status, cooling: dict, now_ms: float, running: dict | None = None) -> dict:
    """The engine-routing signal value and its reasons, from pure inputs."""
    if routes is None:
        return {"state": "UNKNOWN", "reason": "no engine/routes.json in this site", "roles": {}}
    if status is None and _windowed(routes):
        return {"state": "UNKNOWN", "reason": "live account status unavailable", "roles": {}}
    accounts = (status or {}).get("accounts") or {}
    roles, stale, dead = {}, [], []
    cooled = cooling_table(cooling, now_ms, routes.get("cool_off_s", DEFAULT_COOL_OFF_S))
    for role, cands in routes["roles"].items():
        choice = choose(role, cands, status, cooling, now_ms, running=running)
        roles[role] = {"selected": choice["key"], "decision": choice["decision"], "trail": choice["trail"]}
        if choice["decision"] == "none":
            dead.append(role)
        for c in cands:
            row = accounts.get(c["account"]) if c["account"] else None
            if isinstance(row, dict) and row.get("state") in STALE_STATES:
                stale.append(f"{role}:{c['account']} {row.get('state')}")
    if dead:
        return {"state": "RED", "reason": "no working candidate for " + ", ".join(sorted(dead)), "roles": roles, "cooled": cooled}
    if stale:
        return {"state": "UNKNOWN", "reason": "stale or missing windows: " + "; ".join(stale), "roles": roles, "cooled": cooled}
    if status is None:
        return {"state": "GREEN", "reason": "no candidate reads an account window", "roles": roles, "cooled": cooled}
    return {"state": "GREEN", "reason": "every role has an admitted candidate on fresh windows", "roles": roles, "cooled": cooled}


def observe(home, result: dict, *, now: float) -> dict:
    """Observe the ``engine-routing`` signal with an assessment already computed."""
    home = Path(home)
    if SIGNAL not in signal_store.registry(home):
        signal_store.register(home, SIGNAL, max_age=SIGNAL_MAX_AGE, subscribers=[])
    if result["state"] == "UNKNOWN":
        signal_store.observe_unknown(home, SIGNAL, now=now, reason=result.get("reason", "unknown"), source="engine-router")
    else:
        signal_store.observe(home, SIGNAL, result["state"], now=now, source="engine-router")
    return result


def publish(home, *, now: float | None = None) -> dict:
    """Compute the routing assessment now and observe the ``engine-routing`` signal."""
    now = time.time() if now is None else now
    try:
        routes = load_routes(home)
    except ValueError as exc:
        result = {"state": "RED", "reason": f"routes file invalid: {exc}", "roles": {}}
    else:
        running = running_candidates(home, now_ms=now * 1000,
                                     horizon_s=routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S))
        result = assess(routes, live_status(home), _load_state(home)["cooling"], now * 1000, running=running)
    return observe(home, result, now=now)


class RoutedEngine:
    """An engine whose candidate is chosen at each run from live windows and cool-offs."""
    name = "routed"

    def __init__(self, home, role: str, routes: dict):
        self.home, self.role = Path(home), role
        self.candidates = routes["roles"][role]
        self.routes = routes
        self.cool_off_ms = routes["cool_off_s"] * 1000
        self.account_cool_off_ms = routes["account_cool_off_s"] * 1000
        self.rate_limit_cool_off_ms_base = routes.get("rate_limit_cool_off_s", DEFAULT_RATE_LIMIT_COOL_OFF_S) * 1000
        self.rate_limit_cool_off_ms_max = routes.get("max_rate_limit_cool_off_s", MAX_RATE_LIMIT_COOL_OFF_S) * 1000
        self.extra_env = routes.get("env", {})

    def _build(self, cand: dict):
        from .engine_claude import ClaudeEngine
        from .engine_omp import OmpEngine
        if cand["engine"] == "claude":
            return ClaudeEngine(cand["model"])
        return OmpEngine(cand["model"], thinking=cand.get("thinking"))

    def _cool(self, cand: dict, now_ms: float, *, role_only: bool = False, off_ms: float | None = None) -> None:
        # The Claude safety net is never cooled: a cooled last candidate leaves the role with no engine.
        if _is_safety_net(cand):
            return
        state = _load_state(self.home)
        key = candidate_key(cand)
        state["cooling"][role_key(self.role, key) if role_only else key] = now_ms + (off_ms or self.cool_off_ms)
        _save_state(self.home, state)

    def _clear(self, key: str) -> None:
        state = _load_state(self.home)
        hit = [state["cooling"].pop(k, None) for k in (key, role_key(self.role, key))]
        # A candidate that worked resets the role's rate-limit backoff: the next refusal starts
        # from the short cool-off again.
        count = state.get("rate_limits", {}).pop(role_key(self.role, key), None)
        if any(v is not None for v in hit) or count is not None:
            _save_state(self.home, state)

    def rate_limit_cool_off_ms(self, role: str, key: str) -> float:
        """The cool-off a rate-limited candidate gets for this role, doubling with each repeat.

        The first rate limit waits a few minutes, the next waits twice as long: a provider that
        keeps answering 429 for the same role is backing off, and a role that waits longer than
        the site-wide window would idle for half an hour over one refusal.
        """
        state = _load_state(self.home)
        past = state.get("rate_limits", {}).get(role_key(role, key), 0)
        off = min(self.rate_limit_cool_off_ms_base * (2 ** past), self.rate_limit_cool_off_ms_max)
        counts = state.setdefault("rate_limits", {})
        counts[role_key(role, key)] = past + 1
        _save_state(self.home, state)
        return off

    def _observe(self, status, now_ms: float) -> None:
        """Re-assess the signal from the status read at selection, with the cool-offs as they now stand."""
        running = running_candidates(self.home, now_ms=now_ms,
                                     horizon_s=self.routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S))
        result = assess(self.routes, status, _load_state(self.home)["cooling"], now_ms, running=running)
        observe(self.home, result, now=now_ms / 1000)

    def hard_cap(self, limit: float, cand: dict) -> float | None:
        """The total runtime this candidate may reach, or None when the deadline is fixed.

        The cap is the smaller of the site's global ``hard_timeout_s`` and the candidate's own, and
        only applies when it is above the soft deadline: a wake that is still producing output may
        run past the soft deadline, an idle one is cut on time (operator DM 298902).
        """
        caps = [value for value in (self.routes.get("hard_timeout_s"), cand.get("hard_timeout_s"))
                if isinstance(value, (int, float))]
        if not caps:
            return None
        cap = float(min(caps))
        return cap if cap > limit else None

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        key_wake = env.get("MISHE_WAKE_KEY")
        tried: set = set()
        while True:
            now_ms = time.time() * 1000
            status = live_status(self.home)
            cooling = _load_state(self.home)["cooling"]
            running = running_candidates(self.home, now_ms=now_ms, horizon_s=self.routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S))
            choice = choose(self.role, self.candidates, status, cooling, now_ms,
                             exclude=frozenset(tried), running=running)
            if choice["candidate"] is None:
                record(self.home, {"role": self.role, "event": "none", "wake": key_wake, "trail": choice["trail"]},
                       now_ms=now_ms)
                self._observe(status, now_ms)
                raise EngineNotStarted(f"no working engine candidate for {self.role}")
            cand, key = choice["candidate"], choice["key"]
            record(self.home, {"role": self.role, "event": "select", "wake": key_wake, "key": key,
                               "tried": sorted(tried), "trail": choice["trail"]}, now_ms=now_ms)
            engine = self._build(cand)
            try:
                limit = min(timeout, cand["timeout_s"]) if cand.get("timeout_s") else timeout
                code = engine.run(prompt_file, env=dict(env, **self.extra_env), cwd=cwd, timeout=limit,
                                  on_start=on_start, hard_timeout=self.hard_cap(limit, cand))
            except EngineExhausted as exc:
                # The account is out of quota: no effect, and every role on that account would fail the same
                # way, so cool the account key (shared by all roles) for a window rather than a role retry.
                record(self.home, {"role": self.role, "event": "fallback", "wake": key_wake, "key": key,
                                   "reason": f"quota: {exc}", "scope": "account"}, now_ms=time.time() * 1000)
                self._cool(cand, time.time() * 1000, off_ms=self.account_cool_off_ms)
                tried.add(key)
                continue
            except EngineRateLimited as exc:
                # A rate limit is refusal-class: no tool call ran, so the wake falls through at once.
                # The cool-off is short and role-scoped: the provider may serve another role minutes
                # later, and a quota window on the shared key would idle every role for it.
                now = time.time() * 1000
                parsed = retry_after_ms(exc)
                if parsed is not None:
                    off = min(parsed, MAX_RETRY_AFTER_COOL_OFF_S * 1000)
                else:
                    off = self.rate_limit_cool_off_ms(self.role, key)
                record(self.home, {"role": self.role, "event": "fallback", "wake": key_wake, "key": key,
                                   "reason": f"rate limited: {exc}", "scope": "role",
                                   "cool_off_s": off / 1000}, now_ms=now)
                self._cool(cand, now, role_only=True, off_ms=off)
                tried.add(key)
                continue
            except EngineRejected as exc:
                # The model refused before any tool call: no effect, so fall through now, and cool this
                # candidate so the next wake does not pick it first again.
                record(self.home, {"role": self.role, "event": "fallback", "wake": key_wake, "key": key,
                                   "reason": f"refused: {exc}"}, now_ms=time.time() * 1000)
                self._cool(cand, time.time() * 1000)
                tried.add(key)
                continue
            except EngineNotStarted as exc:
                # The command never ran, so the wake has no effect: fall through to the next candidate now.
                # No cool-off: an unavailable binary is not evidence the account is exhausted.
                record(self.home, {"role": self.role, "event": "fallback", "wake": key_wake, "key": key,
                                   "reason": f"did not start: {exc}"}, now_ms=time.time() * 1000)
                tried.add(key)
                continue
            except EngineTimeout as exc:
                record(self.home, {"role": self.role, "event": "outcome", "wake": key_wake, "key": key,
                                   "outcome": "timeout", "scope": "role", "reason": str(exc)}, now_ms=time.time() * 1000)
                self._cool(cand, time.time() * 1000, role_only=True)
                self._observe(status, time.time() * 1000)
                raise
            yielded = bool(key_wake) and yielding.receipt_path(self.home, key_wake).exists()
            # Quota spent after the run acted: the effect is unknown (no yield), so the wake stays unknown and does
            # not fall through, but the account is out for every role until its window resets.
            spent = None if yielded else (engine.spent_quota() if hasattr(engine, "spent_quota") else None)
            outcome = "yielded" if yielded else f"no yield (exit {code})"
            if spent:
                outcome += ", quota spent after acting"
            record(self.home, {"role": self.role, "event": "outcome", "wake": key_wake, "key": key,
                               "outcome": outcome, **({"scope": "account", "reason": spent} if spent else {})},
                   now_ms=time.time() * 1000)
            if yielded:
                self._clear(key)
            elif spent:
                self._cool(cand, time.time() * 1000, off_ms=self.account_cool_off_ms)
            else:
                self._cool(cand, time.time() * 1000)
            self._observe(status, time.time() * 1000)
            return code


def for_role(home, role: str):
    """A RoutedEngine for a role listed in the site routes file, else None (legacy engine file applies)."""
    routes = load_routes(home)
    if routes is None or role not in routes["roles"]:
        return None
    return RoutedEngine(home, role, routes)


def cmd_publish(args) -> int:
    result = publish(args.home)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else f"engine-routing {result['state']}: {result['reason']}")
    return {"GREEN": 0, "RED": 2}.get(result["state"], 3)     # the producer check contract: 2 RED, 3 cannot tell


def cmd_choose(args) -> int:
    routes = load_routes(args.home)
    if routes is None or args.role not in routes["roles"]:
        print(f"no routes for {args.role}")
        return 3
    now_ms = time.time() * 1000
    running = running_candidates(args.home, now_ms=now_ms,
                                 horizon_s=routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S))
    choice = choose(args.role, routes["roles"][args.role], live_status(args.home),
                    _load_state(args.home)["cooling"], now_ms, running=running)
    print(json.dumps(choice, indent=2, sort_keys=True))
    return 0 if choice["decision"] == "select" else 3


def cmd_cooling(args) -> int:
    routes = load_routes(args.home)
    cool = routes["cool_off_s"] if routes else DEFAULT_COOL_OFF_S
    rows = cooling_table(_load_state(args.home)["cooling"], time.time() * 1000, cool)
    for r in rows:
        print(f"{r['role']:<10} {r['candidate']}  age {r['age_s']}s  left {r['left_s']}s")
    if not rows:
        print("nothing cooling")
    return 0


def cmd_running(args) -> int:
    routes = load_routes(args.home)
    now_ms = time.time() * 1000
    horizon = routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S) if routes else DEFAULT_HARD_TIMEOUT_S
    counts = running_candidates(args.home, now_ms=now_ms, horizon_s=horizon)
    caps = {}
    if routes:
        for cands in routes["roles"].values():
            for c in cands:
                if c.get("max_parallel") is not None:
                    caps[candidate_key(c)] = c["max_parallel"]
    for key in sorted(counts):
        cap = caps.get(key)
        print(f"{key}  running {counts[key]}" + (f" of {cap}" if cap else ""))
    if not counts:
        print("no candidate is running")
    return 0


def cmd_ledger(args) -> int:
    path = _dir(args.home) / LEDGER
    lines = file_lines(path) if path.is_file() else []
    for line in lines[-args.lines:]:
        print(line)
    return 0


def register(sub) -> None:
    router = sub.add_parser("router", help="Inner engine router: per-role candidates, cool-offs, ledger").add_subparsers(
        dest="router_command", required=True)
    p = router.add_parser("publish", help="Recompute and observe the engine-routing signal")
    p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_publish)
    p = router.add_parser("choose", help="Dry-run the choice for a role (no ledger, no cool-off change)")
    p.add_argument("role"); p.set_defaults(func=cmd_choose)
    p = router.add_parser("cooling", help="Cooled (role, candidate) pairs with age"); p.set_defaults(func=cmd_cooling)
    p = router.add_parser("running", help="Candidates with a wake still running, against their parallelism cap")
    p.set_defaults(func=cmd_running)
    p = router.add_parser("ledger", help="Last routing events")
    p.add_argument("--lines", type=int, default=40); p.set_defaults(func=cmd_ledger)
