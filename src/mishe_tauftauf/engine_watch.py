"""Supervised engine quota collector, proposal emitter and staleness guard.

Collector cycle: reads engine_usage.status and token_usage.report, decides per
role via engine_policy.decide, writes engine/watch-cache.json (0600) with
{observed_at, engine_state, stats_state, decisions, next_reconsider}. Private
errors go to engine/watch-errors.jsonl (0600).

Decision: when a healthy select differs from current route and hold allows,
emit health proposal record engine/proposals/<generation>-<role>.json (0600)
but do NOT apply launcher; milestone 3 apply() does CAS launcher change. A
--apply flag may invoke apply with CAS.

Guard: check() reads cache mtime; if missing/stale (>180s) or malformed,
returns UNKNOWN/RED and addresses Health via durable outbox
health/engine-watch-alert.jsonl (0600), never erasing prior evidence.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_SECONDS = 180
DEFAULT_INTERVAL = 60
JITTER = 10
CACHE_REL = Path("engine/watch-cache.json")
ERRORS_REL = Path("engine/watch-errors.jsonl")
PROPOSALS_REL = Path("engine/proposals")
ALERT_REL = Path("health/engine-watch-alert.jsonl")
VERSION = 1


def _iso_from_ms(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms(now_ms=None):
    if now_ms is not None:
        return int(now_ms)
    return int(time.time() * 1000)


def _atomic_json(path: Path, obj):
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=True, indent=2).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".draft-")
    try:
        with os.fdopen(fd, "wb") as h:
            h.write(raw)
            h.flush()
            os.fsync(h.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(path))
        try:
            d = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(d)
            finally:
                os.close(d)
        except Exception:
            pass
        os.chmod(str(path), 0o600)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _append_jsonl(path: Path, obj):
    line = json.dumps(obj, sort_keys=True, ensure_ascii=True).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except Exception:
        pass
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab") as h:
            h.write(line)
            h.flush()
            os.fsync(h.fileno())
        os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    try:
        d = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    except Exception:
        pass


def _log_error(home: Path, now_ms: int, where: str, detail: str):
    try:
        _append_jsonl(home / ERRORS_REL, {"observed_at": _iso_from_ms(now_ms), "observed_ms": now_ms, "where": where, "error": detail[:500]})
    except Exception:
        pass


def _alert(home: Path, reason: str, now_ms: int, age):
    try:
        _append_jsonl(home / ALERT_REL, {"observed_at": _iso_from_ms(now_ms), "observed_ms": now_ms, "source": "engine-watch", "reason": reason, "age_seconds": age})
    except Exception as exc:
        _log_error(home, now_ms, "alert", str(exc)[:500])


def _load_allowlist(home: Path, status_obj):
    allow = {}
    pol = home / "engine/policy.json"
    if pol.is_file():
        try:
            import mishe_tauftauf.model_boundary as mb
            raw = mb.load(pol)
            if isinstance(raw, dict):
                for role, routes in raw.items():
                    if isinstance(role, str) and isinstance(routes, list):
                        clean = []
                        for r in routes:
                            if isinstance(r, dict) and isinstance(r.get("provider"), str) and isinstance(r.get("alias"), str):
                                prov = r.get("provider")
                                alias = r.get("alias")
                                model = r.get("model")
                                if not isinstance(model, str):
                                    model = prov + "/auto"
                                effort = r.get("effort")
                                if not isinstance(effort, str):
                                    effort = "high"
                                runner = r.get("runner")
                                if not isinstance(runner, str):
                                    runner = "omp"
                                clean.append({"provider": prov, "alias": alias, "model": model, "effort": effort, "runner": runner})
                        if clean:
                            allow[role] = clean
                if allow:
                    return allow
        except Exception as exc:
            _log_error(home, _now_ms(None), "load_allowlist:policy", type(exc).__name__ + " " + str(exc)[:300])
    try:
        import mishe_tauftauf.engine_boundary as eb
        _, registry = eb.load_registry(home)
        routes = []
        for alias, entry in registry.items():
            if entry.get("state") == "configured":
                routes.append({"provider": entry["provider"], "alias": alias, "model": entry["provider"] + "/auto", "effort": "high", "runner": "omp"})
        if routes:
            allow = {"default": routes}
    except Exception as exc:
        _log_error(home, _now_ms(None), "load_allowlist:registry", type(exc).__name__ + " " + str(exc)[:300])
    return allow


def _load_current(home: Path, role: str):
    for cand in [home / "engine" / "current" / (role + ".json"), home / "engine" / "current.json"]:
        if cand.is_file():
            try:
                import mishe_tauftauf.model_boundary as mb
                data = mb.load(cand)
                if isinstance(data, dict) and isinstance(data.get("alias"), str) and isinstance(data.get("provider"), str):
                    return data
                if isinstance(data, dict) and role in data and isinstance(data[role], dict):
                    return data[role]
            except Exception as exc:
                # Visible, not fatal: a malformed current selection must not read as absent.
                _log_error(home, _now_ms(None), "load_current:" + role, type(exc).__name__ + " " + str(exc)[:300])
                continue
    return None


def _maybe_propose(home: Path, role: str, decision: dict, now_ms: int):
    if decision.get("decision") != "select":
        return None
    sel = decision.get("selected")
    if not isinstance(sel, dict):
        return None
    cur = _load_current(home, role)
    if cur is not None and cur == sel:
        return None
    base = home / PROPOSALS_REL
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(base), 0o700)
    except Exception:
        pass
    gen = str(now_ms)
    path = base / (gen + "-" + role + ".json")
    if path.exists():
        return str(path)
    payload = {"version": VERSION, "role": role, "generation": gen, "observed_ms": now_ms, "observed_at": _iso_from_ms(now_ms), "decision": decision, "current": cur}
    _atomic_json(path, payload)
    return str(path)


def once(home, now_ms=None, now=None):
    if now is not None and now_ms is None:
        try:
            now_ms = int(now * 1000) if isinstance(now, (int, float)) else int(now)
        except Exception:
            now_ms = None
    t_ms = _now_ms(now_ms)
    iso = _iso_from_ms(t_ms)
    home = Path(home).resolve()
    eng = None
    try:
        import mishe_tauftauf.engine_usage as eu
        eng = eu.status(home, now_ms=t_ms)
    except Exception as exc:
        _log_error(home, t_ms, "engine_usage.status", str(exc)[:500])
        eng = {"state": "UNKNOWN", "observed_at": iso, "accounts": {}, "error": str(exc)[:200]}
    stats = None
    try:
        import mishe_tauftauf.token_usage as tu
        stats = tu.report(home, now_ms=t_ms)
    except Exception as exc:
        _log_error(home, t_ms, "token_usage.report", str(exc)[:500])
        stats = {"state": "UNKNOWN", "observed_at": iso, "error": str(exc)[:200]}
    allow = _load_allowlist(home, eng)
    if not allow:
        allow = {}
    import mishe_tauftauf.engine_policy as pol
    decisions = {}
    next_rec = None
    for role, routes in allow.items():
        cur = _load_current(home, role)
        try:
            dec = pol.decide(role=role, status=eng, allowlist={role: routes}, current_route=cur, now_ms=t_ms, reservations=None, reserve_pct=0.15, reserve_policy="auto", min_hold_ms=600000)
        except Exception as exc:
            _log_error(home, t_ms, "policy.decide:" + role, str(exc)[:500])
            dec = {"decision": "hold", "selected": None, "reason": str(exc)[:200], "evidence_ids": [], "next_reconsider_ms": t_ms + 600000}
        decisions[role] = dec
        nr = dec.get("next_reconsider_ms")
        if isinstance(nr, int):
            if next_rec is None or nr < next_rec:
                next_rec = nr
        try:
            _maybe_propose(home, role, dec, t_ms)
        except Exception as exc:
            _log_error(home, t_ms, "propose:" + role, str(exc)[:500])
    cache = {
        "version": VERSION,
        "observed_at": iso,
        "observed_ms": t_ms,
        "engine_state": eng.get("state") if isinstance(eng, dict) else "UNKNOWN",
        "stats_state": stats.get("state") if isinstance(stats, dict) else "UNKNOWN",
        "engine": eng,
        "stats": stats,
        "decisions": decisions,
        "next_reconsider": _iso_from_ms(next_rec),
        "next_reconsider_ms": next_rec,
    }
    _atomic_json(home / CACHE_REL, cache)
    try:
        os.utime(home / CACHE_REL, (t_ms / 1000, t_ms / 1000))
    except Exception:
        pass
    return cache


def check(home, now_ms=None, now=None):
    if now is not None and now_ms is None:
        try:
            now_ms = int(now * 1000) if isinstance(now, (int, float)) else int(now)
        except Exception:
            now_ms = None
    t_ms = _now_ms(now_ms)
    home = Path(home).resolve()
    p = home / CACHE_REL
    try:
        st = p.stat()
        age = (t_ms / 1000) - st.st_mtime
        try:
            import mishe_tauftauf.model_boundary as mb
            data = mb.load(p)
        except Exception as exc:
            reason = "cache malformed or unreadable: " + type(exc).__name__ + " " + str(exc)[:200]
            _alert(home, reason, t_ms, age)
            return {"state": "UNKNOWN", "status": "RED", "reason": reason, "age_seconds": age}
        if not isinstance(data, dict) or "observed_at" not in data or "decisions" not in data:
            reason = "cache malformed: missing required keys"
            _alert(home, reason, t_ms, age)
            return {"state": "UNKNOWN", "status": "RED", "reason": reason, "age_seconds": age}
        if age < -5:
            reason = "cache future-dated"
            _alert(home, reason, t_ms, age)
            return {"state": "UNKNOWN", "status": "RED", "reason": reason, "age_seconds": age}
        if age > STALE_SECONDS:
            reason = "stale cache age " + str(round(age, 1)) + "s >" + str(STALE_SECONDS) + "s"
            _alert(home, reason, t_ms, age)
            return {"state": "UNKNOWN", "status": "RED", "reason": reason, "age_seconds": age}
        return {"state": "FRESH", "status": "GREEN", "reason": "ok", "age_seconds": age, "cache": data}
    except FileNotFoundError:
        reason = "cache missing"
        _alert(home, reason, t_ms, None)
        return {"state": "UNKNOWN", "status": "RED", "reason": reason}
    except (OSError, ValueError) as exc:
        reason = "cache malformed or unreadable: " + type(exc).__name__ + " " + str(exc)[:200]
        _alert(home, reason, t_ms, None)
        return {"state": "UNKNOWN", "status": "RED", "reason": reason}
    except Exception as exc:
        reason = "cache malformed or unreadable: " + type(exc).__name__ + " " + str(exc)[:200]
        try:
            _alert(home, reason, t_ms, None)
        except Exception:
            pass
        return {"state": "UNKNOWN", "status": "RED", "reason": reason}


def serve(home, interval=DEFAULT_INTERVAL, jitter=JITTER, now_ms=None):
    home = Path(home).resolve()
    while True:
        try:
            once(home)
        except Exception as exc:
            try:
                _log_error(home, _now_ms(None), "serve", str(exc)[:500])
            except Exception:
                pass
        delta = interval + random.uniform(-jitter, jitter)
        if delta < 1:
            delta = 1
        time.sleep(delta)


def apply_proposal(home, generation, role=None):
    home = Path(home).resolve()
    base = home / PROPOSALS_REL
    if role is None:
        candidates = list(base.glob(generation + "*.json"))
        if not candidates:
            candidates = list(base.glob(generation + ".json"))
        if not candidates:
            raise FileNotFoundError("proposal not found: " + generation)
        path = candidates[0]
    else:
        path = base / (generation + "-" + role + ".json")
        if not path.is_file():
            path = base / (generation + ".json")
    import mishe_tauftauf.model_boundary as mb
    data = mb.load(path)
    return data


def main(argv=None):
    import sys
    if argv is None:
        argv = sys.argv[1:]
    home = None
    do_apply = False
    do_once = False
    do_check = False
    do_serve = False
    generation = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--home" and i + 1 < len(argv):
            home = Path(argv[i + 1])
            i = i + 2
            continue
        if a[:7] == "--home=":
            home = Path(a[7:])
            i += 1
            continue
        if a == "--apply":
            do_apply = True
            if i + 1 < len(argv):
                nxt = argv[i + 1]
                if len(nxt) < 2 or nxt[0:2] != "--":
                    generation = nxt
                    i += 1
        elif a == "--once":
            do_once = True
        elif a == "--check":
            do_check = True
        elif a == "--serve":
            do_serve = True
        i += 1
    if home is None:
        home = Path.cwd()
    home = Path(home).resolve()
    if do_check:
        r = check(home)
        print(json.dumps(r, indent=2, sort_keys=True))
        return 0 if r.get("state") == "FRESH" else 3
    if do_apply and generation is not None:
        data = apply_proposal(home, generation)
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if do_once:
        c = once(home)
        print(json.dumps(c, indent=2, sort_keys=True))
        return 0
    if do_serve:
        serve(home)
        return 0
    c = once(home)
    print(json.dumps(c, indent=2, sort_keys=True))
    return 0
