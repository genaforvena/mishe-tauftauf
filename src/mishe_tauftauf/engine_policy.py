"""Pure admission policy for engine routes.

Milestone 2: deterministic, no I/O, no inference.

OMP reuse note: ``retry.usageAwareFallback`` defaults to ``false`` (``node_modules/@oh-my-pi/pi-coding-agent/src/session/settings.ts:728``,
``DEFAULT_USAGE_RESERVE_PCT=10`` from ``pi-ai/src/auth/types.ts:25``) so OMP does **not**
preempt on reserve by default. ``retry.usageReservePolicy`` defaults to ``confirm``
(``settings.ts:761``) and ``retry.usageReservePct`` defaults to 10%.  The
installed ``omp-recovery/agent/config.yml`` contains no ``retry.*`` keys, so
defaults apply.  This module adds admission-time preemption with a configurable
reserve (plan proposed 15 %, default 0.15 here) and explicit ``auto`` /
``fail-closed`` policy mirroring ``turn-recovery.ts:1936-2051`` reserve /
depleted / healthy handling: healthy keeps the selected account, reserve either
auto-fallbacks or fail-closes, depleted always falls back or blocks.

Inputs are the **role allowlist** ``{role: [Route, ...]}`` ordered by
preference, the dashboard ``status`` as returned by
``engine_usage.status`` (``accounts`` dict with ``state`` ``FRESH``/``STALE``/
``UNKNOWN``/``UNATTRIBUTED``/``NOT_CONFIGURED`` and ``windows``), per-alias
``reservations`` ``{alias: {window_id: reserved_fraction}}``, the in-flight
``current_route`` and ``now_ms``.

- ``UNKNOWN``/``STALE``/``UNATTRIBUTED``/``NOT_CONFIGURED`` are not evidence
  of exhaustion; new admission is ineligible and the decision is ``hold``.
- For ``FRESH`` accounts every window whose ``remaining_fraction`` is not
  ``None`` is checked: ``effective = remaining - reserved``.  ``0`` or
  ``provider_status == "depleted"`` is ``depleted``, ``0 < effective <
  reserve_pct`` is ``reserve``, otherwise ``healthy``.  All relevant windows
  (rolling, weekly, monthly, model-specific) gate admission.
- ``auto`` treats ``reserve`` as ineligible and prefers an alternative;
  ``fail-closed`` queues instead of spending reserve.
- Hysteresis: while the current compatible route remains eligible, ``stay``.
  A switch occurs only on hard failure (current becomes ineligible) or when
  the hold expires.  The hold interval is ``min_hold_ms`` (default 600 000
  ms); ``next_reconsider_ms`` is ``now + min_hold_ms`` on ``stay`` and
  ``min(reset, now+hold)`` on ``queue``.
- Same-provider alias is preferred before cross-provider fallback (``go-a``
  before ``go-b`` before other providers).  No headroom percentage decides
  model suitability — the allowlist decides.
- Exhausted/gated pools produce ``queue`` with ``binding_reset`` as the
  earliest ``reset_at`` among gated accounts.

Pure function :func:`decide` returns ``{decision, selected, reason,
evidence_ids, next_reconsider_ms, binding_reset?}``.  :func:`explain`
renders human reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_INELIGIBLE_STATES = {"UNKNOWN", "STALE", "UNATTRIBUTED", "NOT_CONFIGURED"}
# STALE is produced by engine_usage but normalised comparison is upper-case;
# status may also contain lower variations from auth states - normalise upper.
# NOT_CONFIGURED comes from engine_usage row state after _auth.
# We also handle "PARTIAL" etc at top level? Only per-account states matter.

def _norm_state(s: Any) -> str:
    if not isinstance(s, str):
        return "UNKNOWN"
    u = s.upper()
    # engine_usage uses NOT_CONFIGURED (underscore), alias mismatch
    if u == "NOT-CONFIGURED":
        u = "NOT_CONFIGURED"
    return u

def _parse_reset(iso: Any) -> int | None:
    if not isinstance(iso, str) or not iso:
        return None
    # status._iso produces exactly "%Y-%m-%dT%H:%M:%SZ" (len 20).
    # Parse without strptime/fromisoformat/endswith per parsing policy.
    if len(iso) != 20:
        return None
    if iso[4] != "-" or iso[7] != "-" or iso[10] != "T" or iso[13] != ":" or iso[16] != ":" or iso[19] != "Z":
        return None
    try:
        year = int(iso[0:4])
        mon = int(iso[5:7])
        day = int(iso[8:10])
        hour = int(iso[11:13])
        minute = int(iso[14:16])
        second = int(iso[17:19])
        dt = datetime(year, mon, day, hour, minute, second, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def _effective_reserved(alias: str, window: dict, reservations: dict) -> float:
    if not reservations or alias not in reservations:
        return 0.0
    per = reservations[alias]
    if isinstance(per, (int, float)):
        return float(per)
    if not isinstance(per, dict):
        return 0.0
    # keyed by window id: prefer window['window'], then limit, then label
    for k in (window.get("window"), window.get("window_id"), window.get("limit"), window.get("label"), window.get("id")):
        if isinstance(k, str) and k in per and isinstance(per[k], (int, float)):
            return float(per[k])
        if k is not None and k in per:
            try:
                return float(per[k])
            except Exception:
                return 0.0
    # if per has a single numeric for this alias (already handled) or a
    # generic string key like "*" or "default"
    for v in per.values():
        if isinstance(v, (int, float)):
            # ambiguous: if dict has multiple entries we already tried exact match.
            # Fall back to 0 to avoid mis-attribution; caller can use single float.
            break
    return 0.0

def _classify_account(account: dict, alias: str, reservations: dict, reserve_pct: float, now_ms: int) -> tuple[str, str, list[str]]:
    """Return (health, reason, evidence_ids). health in healthy/reserve/depleted/hold."""
    state = _norm_state(account.get("state"))
    if state in _INELIGIBLE_STATES:
        # UNKNOWN/STALE/UNATTRIBUTED/NOT_CONFIGURED suspend new admission; hold not queue
        reason = account.get("reason") or state.lower()
        return "hold", f"{alias} {state}: {reason}", [alias]
    if state != "FRESH":
        # Any other non-fresh (e.g. partial) treat as hold
        return "hold", f"{alias} not FRESH ({state})", [alias]
    windows = account.get("windows") or []
    if not windows:
        return "healthy", "no windows", [alias]
    worst = "healthy"
    reasons: list[str] = []
    evidence_ids: list[str] = []
    for w in windows:
        remaining = w.get("remaining_fraction")
        if remaining is None:
            # per spec every window with remaining not None must satisfy; None is ignored (unknown unit)
            continue
        reset_ms = _parse_reset(w.get("reset_at"))
        # reset crossing: if reset already passed relative to now, window is no longer gating
        if reset_ms is not None and reset_ms <= now_ms:
            continue
        provider_status = w.get("provider_status")
        if isinstance(provider_status, str) and provider_status.lower() == "depleted":
            worst = "depleted"
            reasons.append(f"{w.get('limit') or w.get('window')} provider depleted")
            evidence_ids.append(f"{alias}:{w.get('limit') or w.get('window')}")
            continue
        reserved = _effective_reserved(alias, w, reservations)
        try:
            eff = float(remaining) - float(reserved)
        except Exception:
            eff = float(remaining)
        # clamp? reserved may exceed remaining
        win_id = w.get("limit") or w.get("window") or "window"
        evidence_ids.append(f"{alias}:{win_id}")
        if eff <= 0:
            worst = "depleted"
            reasons.append(f"{win_id} depleted (remaining {remaining:.2f} - reserved {reserved:.2f} <=0)")
        elif 0 < eff < reserve_pct:
            if worst != "depleted":
                worst = "reserve"
            reasons.append(f"{win_id} reserve {eff:.2%} < {reserve_pct:.0%}")
        else:
            # healthy window; keep worst
            pass
    if worst == "healthy":
        return "healthy", "all windows healthy", evidence_ids or [alias]
    if worst == "reserve":
        return "reserve", "; ".join(reasons) or "reserve", evidence_ids or [alias]
    return "depleted", "; ".join(reasons) or "depleted", evidence_ids or [alias]


def _alias_of(route: dict | None) -> str | None:
    if not isinstance(route, dict):
        return None
    for k in ("alias", "entitlement", "id"):
        if isinstance(route.get(k), str):
            return route.get(k)
    return None

def _provider_of(route: dict | None) -> str | None:
    if not isinstance(route, dict):
        return None
    p = route.get("provider")
    return p if isinstance(p, str) else None

def _route_compatible(route: dict, account: dict) -> bool:
    # route provider must match account provider if both present
    rp = _provider_of(route)
    ap = account.get("provider") if isinstance(account, dict) else None
    if isinstance(rp, str) and isinstance(ap, str) and rp != ap:
        return False
    return True

def _is_route_in_allowlist(route: dict, candidates: list[dict]) -> bool:
    alias = _alias_of(route)
    for c in candidates:
        if _alias_of(c) == alias and _provider_of(c) == _provider_of(route):
            # also compare model/effort if present?
            return True
        # fallback: exact alias match
        if alias is not None and alias == _alias_of(c):
            return True
    return False

def _binding_reset_ms(accounts: dict, gated_aliases: list[str], now_ms: int) -> int | None:
    earliest = None
    for alias in gated_aliases:
        acc = accounts.get(alias) or {}
        for w in acc.get("windows") or []:
            ms = _parse_reset(w.get("reset_at"))
            if ms is None or ms <= now_ms:
                continue
            if earliest is None or ms < earliest:
                earliest = ms
    return earliest

def _iso_from_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _normalize_decide_args(*args, **kwargs) -> tuple[str, dict, dict, dict | None, int, dict | None, float, str, int]:
    """Support positional and keyword calling styles.

    Canonical keyword style:
      decide(role=..., status=..., allowlist=..., current_route=..., now_ms=..., reservations=..., reserve_pct=..., reserve_policy=..., min_hold_ms=...)
    Positional style:
      decide(role, status, allowlist, current_route, now_ms, ...)
      or decide(role, status, current_route, now_ms, allowlist=...)
    """
    # Extract known kwargs
    role = kwargs.pop("role", None)
    status = kwargs.pop("status", None)
    allowlist = kwargs.pop("allowlist", kwargs.pop("role_allowlist", kwargs.pop("routes", None)))
    current_route = kwargs.pop("current_route", kwargs.pop("current", None))
    now_ms = kwargs.pop("now_ms", kwargs.pop("now", None))
    reservations = kwargs.pop("reservations", None)
    reserve_pct = kwargs.pop("reserve_pct", kwargs.pop("reserve", None))
    reserve_policy = kwargs.pop("reserve_policy", kwargs.pop("policy", None))
    min_hold_ms = kwargs.pop("min_hold_ms", kwargs.pop("hold_ms", None))

    # Fill from positional args
    # args may be (role, status, allowlist, current_route, now_ms) or (role, status, current_route, now_ms)
    pos = list(args)
    # If first pos is role and role already set via kw, keep kw
    idx = 0
    if role is None and idx < len(pos):
        role = pos[idx]; idx += 1
    elif idx < len(pos) and role is not None:
        # role supplied both ways - keep kw, advance idx past positional role
        # only if caller used positional for role as first arg and also kw (duplicate)
        # we skip one positional to align
        if pos[0] == role:
            idx += 1
    if status is None and idx < len(pos):
        status = pos[idx]; idx += 1
    # Now we need to determine if next positional is allowlist or current_route
    # Heuristic: allowlist is dict mapping role->list; current_route is dict with provider/alias
    if allowlist is None and idx < len(pos):
        candidate = pos[idx]
        if isinstance(candidate, dict) and candidate and all(isinstance(v, list) for v in candidate.values()):
            allowlist = candidate; idx += 1
        elif isinstance(candidate, dict) and ("provider" in candidate or "alias" in candidate):
            # this is actually current_route, allowlist will come via kwargs
            pass
        elif isinstance(candidate, dict):
            # ambiguous: if dict looks like allowlist (role keys), treat as allowlist
            # else treat as current_route
            # allowlist keys are role names, values are lists
            # current_route has keys provider/alias/model
            if any(isinstance(v, list) for v in candidate.values()):
                allowlist = candidate; idx += 1
            else:
                # assume current_route
                pass
        else:
            # list? could be candidate list directly
            if isinstance(candidate, list):
                # wrap as single-role allowlist under role key
                if role is not None:
                    allowlist = {role: candidate}
                idx += 1

    if current_route is None and idx < len(pos):
        current_route = pos[idx]; idx += 1
    if now_ms is None and idx < len(pos):
        now_ms = pos[idx]; idx += 1
    if reservations is None and idx < len(pos):
        # allow trailing reservations positional
        reservations = pos[idx]; idx += 1

    # defaults
    if reserve_pct is None:
        reserve_pct = 0.15
    if reserve_policy is None:
        reserve_policy = "auto"
    if min_hold_ms is None:
        min_hold_ms = 600_000
    if reservations is None:
        reservations = {}
    if status is None:
        status = {}
    if allowlist is None:
        allowlist = {}
    # any remaining kwargs are ignored (e.g. extra)
    # consume reserve_pct/reserve_policy overrides from leftover kwargs? already popped
    if "reserve_pct" in kwargs:
        reserve_pct = kwargs.pop("reserve_pct")
    if "reserve_policy" in kwargs:
        reserve_policy = kwargs.pop("reserve_policy")
    # coerce types
    try:
        reserve_pct = float(reserve_pct)
    except Exception:
        reserve_pct = 0.15
    try:
        min_hold_ms = int(min_hold_ms)
    except Exception:
        min_hold_ms = 600_000
    if not isinstance(reserve_policy, str):
        reserve_policy = "auto"
    reserve_policy = reserve_policy.lower()
    if reserve_policy not in ("auto", "fail-closed"):
        # spec says only auto/fail-closed; treat confirm as auto for compat? but keep as auto
        if reserve_policy == "confirm":
            reserve_policy = "auto"
        else:
            reserve_policy = "auto"
    return role, status, allowlist, current_route, int(now_ms) if now_ms is not None else 0, reservations, reserve_pct, reserve_policy, min_hold_ms

def decide(*args, **kwargs) -> dict:
    """Pure admission decision.

    Keyword style::

        decide(role="codex", status=dashboard, allowlist={role: [Route]}, current_route=Route|None, now_ms=..., reservations=..., reserve_pct=0.15, reserve_policy="auto", min_hold_ms=600000)

    Positional style::

        decide(role, status, allowlist, current_route, now_ms, reservations, reserve_pct, reserve_policy, min_hold_ms)

    ``Route`` is ``{provider, alias, model, effort, runner?}``.
    Returns ``{decision, selected, reason, evidence_ids, next_reconsider_ms, binding_reset?}``.
    """
    role, status, allowlist, current_route, now_ms, reservations, reserve_pct, reserve_policy, min_hold_ms = _normalize_decide_args(*args, **kwargs)

    if not isinstance(role, str):
        role = str(role) if role is not None else ""
    candidates: list[dict] = []
    if isinstance(allowlist, dict):
        # role keyed
        if role in allowlist and isinstance(allowlist[role], list):
            candidates = [c for c in allowlist[role] if isinstance(c, dict)]
        elif role in allowlist:
            candidates = []
        else:
            # allowlist might itself be a list for this role
            candidates = []
    elif isinstance(allowlist, list):
        candidates = [c for c in allowlist if isinstance(c, dict)]

    # No candidates -> queue/hold? Forbid -> queue with no binding
    accounts = status.get("accounts") if isinstance(status, dict) else {}
    if not isinstance(accounts, dict):
        accounts = {}

    # Classify each candidate alias
    health_by_alias: dict[str, tuple[str, str, list[str]]] = {}
    for c in candidates:
        alias = _alias_of(c)
        if not alias:
            continue
        acc = accounts.get(alias)
        if acc is None:
            # alias not in inventory => NOT_CONFIGURED-like
            health_by_alias[alias] = ("hold", f"{alias} not in inventory", [alias])
            continue
        h, reason, eids = _classify_account(acc, alias, reservations, reserve_pct, now_ms)
        # additionally check provider compatibility? allowlist decides suitability, so mismatch is filtered elsewhere
        health_by_alias[alias] = (h, reason, eids)

    # Helper to find candidate dict by alias
    def cand_by_alias(alias: str) -> dict | None:
        for c in candidates:
            if _alias_of(c) == alias:
                return c
        return None

    healthy_aliases = [a for a, (h, _, _) in health_by_alias.items() if h == "healthy"]
    reserve_aliases = [a for a, (h, _, _) in health_by_alias.items() if h == "reserve"]
    depleted_aliases = [a for a, (h, _, _) in health_by_alias.items() if h == "depleted"]
    hold_aliases = [a for a, (h, _, _) in health_by_alias.items() if h == "hold"]

    # --- current route hysteresis: stay while eligible ---
    current_alias = _alias_of(current_route) if isinstance(current_route, dict) else None
    current_healthy = False
    current_reason = ""
    current_eids: list[str] = []
    if current_alias and current_alias in health_by_alias:
        h, r, e = health_by_alias[current_alias]
        current_reason = r
        current_eids = e
        # also check route is in allowlist (compatible)
        if not _is_route_in_allowlist(current_route, candidates):
            # forbidden -> force ineligible
            current_healthy = False
            current_reason = f"current route {current_alias} not in allowlist for role {role}"
        else:
            if h == "healthy":
                current_healthy = True
            elif h == "reserve":
                # reserve handling depends on policy: auto => ineligible (force switch), fail-closed => queue (stay? actually hold/queue)
                if reserve_policy == "auto":
                    current_healthy = False
                else:
                    current_healthy = False
            else:
                current_healthy = False
    elif current_route is not None:
        # current route not in candidates or not in accounts
        # check if it is compatible at all? If allowlist empty, ignore
        if candidates and not _is_route_in_allowlist(current_route, candidates):
            current_healthy = False
            current_reason = "current route not allowed"
        else:
            # unknown alias - treat as hold? keep in-flight?
            # If current has no inventory, we cannot reassess as healthy -> hold
            current_healthy = False
            current_reason = f"current route {current_alias} inventory unknown"

    if current_healthy:
        # stay: prefer current while eligible, irrespective of alternative headroom
        # hysteresis: no churn within min_hold
        # next reconsider is now + hold
        # evidence includes current windows
        return {
            "decision": "stay",
            "selected": current_route,
            "reason": f"current {current_alias} healthy: {current_reason}",
            "evidence_ids": current_eids,
            "next_reconsider_ms": now_ms + min_hold_ms,
        }

    # Current not healthy or no current: need to select alternative
    # Try same-provider alias first
    selected: dict | None = None
    reason = ""
    evidence_ids: list[str] = []
    # Determine provider to prefer same-provider
    cur_provider = _provider_of(current_route) if isinstance(current_route, dict) else None
    # If no current, use first candidate's provider as reference? spec: try same-provider alias first (go-a -> go-b)
    # With no current, we skip same-provider priority and just go in order.
    if cur_provider:
        for c in candidates:
            a = _alias_of(c)
            if a == current_alias:
                continue
            if _provider_of(c) != cur_provider:
                continue
            if health_by_alias.get(a, ("hold", "", []))[0] == "healthy":
                selected = c
                _, r, e = health_by_alias[a]
                reason = f"same-provider fallback {a} healthy: {r}"
                evidence_ids = e
                break
    # Then ordered fallback
    if selected is None:
        for c in candidates:
            a = _alias_of(c)
            if a == current_alias:
                continue
            h = health_by_alias.get(a, ("hold", "", []))[0]
            if h == "healthy":
                selected = c
                _, r, e = health_by_alias[a]
                reason = f"fallback {a} healthy: {r}"
                evidence_ids = e
                break

    if selected is not None:
        return {
            "decision": "select",
            "selected": selected,
            "reason": reason,
            "evidence_ids": evidence_ids,
            "next_reconsider_ms": now_ms + min_hold_ms,
        }

    # No healthy candidate: determine hold vs queue
    # If current exists and its health is hold (UNKNOWN etc) -> hold (keep in-flight, no reassignment)
    # This mirrors turn-recovery: unknown does not mean exhausted; decision is hold
    # If every candidate is hold -> hold
    # If any gated (reserve/depleted) exists -> queue with binding reset
    gated_aliases = reserve_aliases + depleted_aliases
    # reserve under fail-closed is gated for queue; under auto reserve is also gated but we already failed to find healthy alternative -> queue as well?
    # Distinguish: reserve with auto and alternative existence already tried; now with no healthy, reserve pools are gated -> queue
    # However spec: "Reserve elides to ineligible under policy 'auto' prefers alternative; 'fail-closed' queues."
    # With no alternative healthy, both become queue (binding reset). For hold case, UNKNOWN takes precedence over queue if no gated pools.
    if gated_aliases:
        # queue
        earliest = _binding_reset_ms(accounts, gated_aliases, now_ms)
        binding_iso = _iso_from_ms(earliest)
        # next reconsider is min(reset, now+hold)
        if earliest is not None:
            next_ms = min(earliest, now_ms + min_hold_ms)
        else:
            next_ms = now_ms + min_hold_ms
        # reason includes most restrictive window
        # prefer depleted over reserve in reason
        if depleted_aliases:
            q_reason = f"all pools depleted/gated; earliest reset {binding_iso or 'unknown'}"
        else:
            q_reason = f"all pools in reserve; earliest reset {binding_iso or 'unknown'} (policy {reserve_policy})"
        result: dict = {
            "decision": "queue",
            "selected": None,
            "reason": q_reason,
            "evidence_ids": sorted({eid for a in gated_aliases for eid in health_by_alias[a][2]}),
            "next_reconsider_ms": next_ms,
        }
        if binding_iso is not None:
            result["binding_reset"] = binding_iso
        elif earliest is not None:
            result["binding_reset"] = binding_iso
        # also include binding_reset_ms for caller convenience? spec says binding_reset is reset_at iso
        return result

    # No gated, but hold aliases exist (or no candidates)
    if hold_aliases or not candidates:
        # hold: keep in-flight if possible, no reassignment
        # If current exists, keep it; else no selected
        # reason aggregates hold reasons
        hold_reasons = "; ".join(health_by_alias[a][1] for a in hold_aliases) if hold_aliases else "no eligible candidates (unknown/stale/not_configured)"
        eids = sorted({eid for a in hold_aliases for eid in health_by_alias.get(a, ("hold", "", []))[2]})
        # next reconsider is report TTL (use min_hold as hold) - spec says hold with next reconsider
        return {
            "decision": "hold",
            "selected": None,
            "reason": hold_reasons or "suspended: unknown inventory",
            "evidence_ids": eids or (sorted(health_by_alias.keys()) if health_by_alias else []),
            "next_reconsider_ms": now_ms + min_hold_ms,
        }

    # fallback queue if nothing else (should not reach)
    return {
        "decision": "queue",
        "selected": None,
        "reason": "no healthy candidate",
        "evidence_ids": [],
        "next_reconsider_ms": now_ms + min_hold_ms,
    }

def explain(*args, **kwargs) -> str:
    """Render human reasoning for a policy decision.

    Accepts either ``explain(role, policy_input)`` where ``policy_input``
    is a dict containing ``status, allowlist, current_route, now_ms,
    reservations, reserve_pct, reserve_policy, min_hold_ms`` or the same
    keyword arguments as :func:`decide`.
    """
    # support explain(role, policy_input) style
    if len(args) == 2 and isinstance(args[1], dict) and "status" in args[1]:
        role = args[0]
        pi = args[1]
        status = pi.get("status")
        allowlist = pi.get("allowlist", pi.get("role_allowlist", pi.get("routes")))
        current_route = pi.get("current_route", pi.get("current"))
        now_ms = pi.get("now_ms", pi.get("now"))
        reservations = pi.get("reservations")
        reserve_pct = pi.get("reserve_pct", pi.get("reserve"))
        reserve_policy = pi.get("reserve_policy", pi.get("policy"))
        min_hold_ms = pi.get("min_hold_ms", pi.get("hold_ms"))
        result = decide(role=role, status=status, allowlist=allowlist, current_route=current_route, now_ms=now_ms, reservations=reservations, reserve_pct=reserve_pct or 0.15, reserve_policy=reserve_policy or "auto", min_hold_ms=min_hold_ms or 600_000)
        decision = result
    elif len(args) == 2 and isinstance(args[1], dict):
        # generic policy_input without status key, treat as kwargs expansion
        role = args[0]
        pi = args[1]
        result = decide(role=role, status=pi.get("status"), allowlist=pi.get("allowlist"), current_route=pi.get("current_route"), now_ms=pi.get("now_ms"), reservations=pi.get("reservations"), reserve_pct=pi.get("reserve_pct", 0.15), reserve_policy=pi.get("reserve_policy", "auto"), min_hold_ms=pi.get("min_hold_ms", 600_000))
        decision = result
    else:
        decision = decide(*args, **kwargs)
        # recover role for rendering
        role = kwargs.get("role")
        if role is None and args:
            role = args[0]
        if not isinstance(role, str):
            role = str(role) if role is not None else "unknown"

    lines = [
        f"role {role}: decision {decision.get('decision')}",
        f"reason: {decision.get('reason')}",
        f"selected: {decision.get('selected')}",
        f"evidence: {', '.join(decision.get('evidence_ids') or []) or 'n/a'}",
        f"next reconsider: {decision.get('next_reconsider_ms')}",
    ]
    if decision.get("binding_reset"):
        lines.append(f"binding reset: {decision['binding_reset']}")
    # OMP reuse note
    lines.append("OMP reuse: usageAwareFallback default false means OMP does NOT preempt on reserve; this policy adds admission-time preemption; installed omp-recovery/agent/config.yml has no retry keys so defaults apply.")
    return "\n".join(lines)

__all__ = ["decide", "explain"]
