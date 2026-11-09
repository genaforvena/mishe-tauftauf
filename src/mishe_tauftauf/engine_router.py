"""Inner engine router: per-role ordered candidates chosen from live account windows.

Site file ``engine/routes.json`` (one file, the only place candidates live)::

    {"version": 1, "cool_off_s": 1800, "env": {"PATH": "..."},
     "roles": {"health": [{"engine": "omp", "model": "opencode-go/beam-free", "account": "go-a"},
                          {"engine": "claude", "model": "claude-haiku-5-5", "account": null}]}}

A candidate is ``engine`` (``claude`` or ``omp``), ``model`` and ``account`` (a
registry alias, or null for the Claude subscription safety net, which has no
window reading in this router).

Choice is the first candidate in order that is not cooling off and is admitted
by :mod:`engine_policy` on the live :mod:`engine_usage` status. Admission needs a
FRESH account; UNKNOWN, STALE and UNATTRIBUTED accounts are never evidence of
capacity and are skipped.

Outcomes are judged from the wake's own yield receipt, not from the engine exit
code alone. A candidate that does not start falls through to the next one within
the same wake (no effect happened). A candidate whose wake times out, exits
without a yield, or leaves no receipt cools off for ``cool_off_s``; the next wake
of the role then starts on the next candidate, and the cooled candidate is probed
again once its cool-off ends. Every selection, fallback and outcome is appended
to ``engine/routing-ledger.jsonl``. The ``engine-routing`` signal is GREEN when
every role has a working candidate and all account evidence is FRESH, UNKNOWN when
evidence is stale or missing, and RED when some role has no working candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import engine_policy as policy
from . import signal_store
from . import yielding
from .wake import EngineNotStarted, EngineTimeout

ROUTES = "routes.json"
STATE = "routing-state.json"
LEDGER = "routing-ledger.jsonl"
SIGNAL = "engine-routing"
SIGNAL_MAX_AGE = 900.0
DEFAULT_COOL_OFF_S = 1800
ENGINES = ("claude", "omp")
STALE_STATES = ("STALE", "UNKNOWN", "UNATTRIBUTED")


def _dir(home) -> Path:
    return Path(home) / "engine"


def _iso(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate_key(c: dict) -> str:
    return f"{c['engine']}:{c['model']}:{c['account'] or '-'}"


def load_routes(home) -> dict | None:
    """The validated route table, or None when the site has no routes file."""
    path = _dir(home) / ROUTES
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
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
            clean.append({"engine": c["engine"], "model": c["model"], "account": account})
        roles[role] = clean
    env = value.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("invalid engine routes env")
    cool = value.get("cool_off_s", DEFAULT_COOL_OFF_S)
    if isinstance(cool, bool) or not isinstance(cool, (int, float)) or cool < 0:
        raise ValueError("invalid cool_off_s")
    return {"roles": roles, "cool_off_s": cool, "env": env}


def _load_state(home) -> dict:
    path = _dir(home) / STATE
    if not path.is_file():
        return {"cooling": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    cooling = value.get("cooling") if isinstance(value, dict) else None
    return {"cooling": cooling if isinstance(cooling, dict) else {}}


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


def _eligible(role: str, cand: dict, status, now_ms) -> tuple[bool, str]:
    if cand["engine"] == "claude" and cand["account"] is None:
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
           exclude: frozenset = frozenset()) -> dict:
    """Pure choice: the first candidate that is not excluded, not cooling and admitted."""
    trail = []
    for c in candidates:
        key = candidate_key(c)
        if key in exclude:
            trail.append({"key": key, "verdict": "tried-this-wake"})
            continue
        until = cooling.get(key)
        if isinstance(until, (int, float)) and until > now_ms:
            trail.append({"key": key, "verdict": "cooling", "until": _iso(until)})
            continue
        ok, reason = _eligible(role, c, status, now_ms)
        trail.append({"key": key, "verdict": "eligible" if ok else "skipped", "reason": reason})
        if ok:
            return {"decision": "select", "candidate": c, "key": key, "trail": trail}
    return {"decision": "none", "candidate": None, "key": None, "trail": trail}


def _invalidate_stale(home, status: dict) -> bool:
    """Ask OMP to refetch provider usage for each configured account whose reading is stale.

    ``omp usage`` serves its cached provider report, so a reading older than the TTL
    stays STALE until the cache is invalidated; a refetch is one usage call, not inference.
    Returns True when at least one provider was invalidated.
    """
    import subprocess
    from . import engine_usage
    touched = False
    for row in (status.get("accounts") or {}).values():
        if row.get("state") != "STALE" or row.get("registry_state") != "configured":
            continue
        env = dict(os.environ, PI_CODING_AGENT_DIR=str(Path(home) / row["store"]))
        try:
            subprocess.run(["omp", "usage", "invalidate", "--provider", row["provider"]], env=env,
                           capture_output=True, timeout=60, check=False)
            touched = True
        except (OSError, subprocess.SubprocessError):
            continue
    return touched


def live_status(home):
    """Live account windows from engine_usage, refetched once when stale, or None when unreadable."""
    from . import engine_usage
    try:
        status = engine_usage.status(Path(home))
        if _invalidate_stale(home, status):
            status = engine_usage.status(Path(home))
        return status
    except Exception:
        return None


def assess(routes: dict | None, status, cooling: dict, now_ms: float) -> dict:
    """The engine-routing signal value and its reasons, from pure inputs."""
    if routes is None:
        return {"state": "UNKNOWN", "reason": "no engine/routes.json in this site", "roles": {}}
    if status is None:
        return {"state": "UNKNOWN", "reason": "live account status unavailable", "roles": {}}
    roles, stale, dead = {}, [], []
    for role, cands in routes["roles"].items():
        choice = choose(role, cands, status, cooling, now_ms)
        roles[role] = {"selected": choice["key"], "decision": choice["decision"], "trail": choice["trail"]}
        if choice["decision"] == "none":
            dead.append(role)
        for c in cands:
            row = (status.get("accounts") or {}).get(c["account"]) if c["account"] else None
            if isinstance(row, dict) and row.get("state") in STALE_STATES:
                stale.append(f"{role}:{c['account']} {row.get('state')}")
    if dead:
        return {"state": "RED", "reason": "no working candidate for " + ", ".join(sorted(dead)), "roles": roles}
    if stale:
        return {"state": "UNKNOWN", "reason": "stale or missing windows: " + "; ".join(stale), "roles": roles}
    return {"state": "GREEN", "reason": "every role has an admitted candidate on fresh windows", "roles": roles}


def observe(home, result: dict, *, now: float) -> dict:
    """Observe the ``engine-routing`` signal with an assessment already computed."""
    home = Path(home)
    if SIGNAL not in signal_store.registry(home):
        signal_store.register(home, SIGNAL, max_age=SIGNAL_MAX_AGE, subscribers=[])
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
        result = assess(routes, live_status(home), _load_state(home)["cooling"], now * 1000)
    return observe(home, result, now=now)


class RoutedEngine:
    """An engine whose candidate is chosen at each run from live windows and cool-offs."""
    name = "routed"

    def __init__(self, home, role: str, routes: dict):
        self.home, self.role = Path(home), role
        self.candidates = routes["roles"][role]
        self.routes = routes
        self.cool_off_ms = routes["cool_off_s"] * 1000
        self.extra_env = routes.get("env", {})

    def _build(self, cand: dict):
        from .engine_claude import ClaudeEngine
        from .engine_omp import OmpEngine
        if cand["engine"] == "claude":
            return ClaudeEngine(cand["model"])
        return OmpEngine(cand["model"])

    def _cool(self, key: str, now_ms: float) -> None:
        state = _load_state(self.home)
        state["cooling"][key] = now_ms + self.cool_off_ms
        _save_state(self.home, state)

    def _clear(self, key: str) -> None:
        state = _load_state(self.home)
        if state["cooling"].pop(key, None) is not None:
            _save_state(self.home, state)

    def _observe(self, status, now_ms: float) -> None:
        """Re-assess the signal from the status read at selection, with the cool-offs as they now stand."""
        result = assess(self.routes, status, _load_state(self.home)["cooling"], now_ms)
        observe(self.home, result, now=now_ms / 1000)

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None) -> int:
        key_wake = env.get("MISHE_WAKE_KEY")
        tried: set = set()
        while True:
            now_ms = time.time() * 1000
            status = live_status(self.home)
            cooling = _load_state(self.home)["cooling"]
            choice = choose(self.role, self.candidates, status, cooling, now_ms, exclude=frozenset(tried))
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
                code = engine.run(prompt_file, env=dict(env, **self.extra_env), cwd=cwd, timeout=timeout,
                                  on_start=on_start)
            except EngineNotStarted as exc:
                # The command never ran, so the wake has no effect: fall through to the next candidate now.
                record(self.home, {"role": self.role, "event": "fallback", "wake": key_wake, "key": key,
                                   "reason": f"did not start: {exc}"}, now_ms=time.time() * 1000)
                self._cool(key, time.time() * 1000)
                tried.add(key)
                continue
            except EngineTimeout as exc:
                record(self.home, {"role": self.role, "event": "outcome", "wake": key_wake, "key": key,
                                   "outcome": "timeout", "reason": str(exc)}, now_ms=time.time() * 1000)
                self._cool(key, time.time() * 1000)
                self._observe(status, time.time() * 1000)
                raise
            yielded = bool(key_wake) and yielding.receipt_path(self.home, key_wake).exists()
            outcome = "yielded" if yielded else f"no yield (exit {code})"
            record(self.home, {"role": self.role, "event": "outcome", "wake": key_wake, "key": key,
                               "outcome": outcome}, now_ms=time.time() * 1000)
            if yielded:
                self._clear(key)
            else:
                self._cool(key, time.time() * 1000)
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
    choice = choose(args.role, routes["roles"][args.role], live_status(args.home),
                    _load_state(args.home)["cooling"], now_ms)
    print(json.dumps(choice, indent=2, sort_keys=True))
    return 0 if choice["decision"] == "select" else 3


def cmd_ledger(args) -> int:
    path = _dir(args.home) / LEDGER
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
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
    p = router.add_parser("ledger", help="Last routing events")
    p.add_argument("--lines", type=int, default=40); p.set_defaults(func=cmd_ledger)
