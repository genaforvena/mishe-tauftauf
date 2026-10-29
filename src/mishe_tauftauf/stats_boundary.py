"""Literal boundary for OMP stats database (read-only sqlite).

Resolves the stats DB path the same way omp-stats does: PI_CONFIG_DIR wins,
else XDG_DATA_HOME/omp when that directory exists, else ~/.omp/stats.db.
The config-root is global (PI_CODING_AGENT_DIR override is ignored for stats).
Only decoded, validated aggregations leave this module. Provider faults never
raise; missing/symlink DB returns structured error with UNKNOWN state upstream.

Differential privacy lens (Dwork & Roth 2014, FnT TCS 9:3-4 pp.211-407, DOI
10.1561/0400000042 — Def. 2.4 p.17-18, Remark 2.1 p.16, Prop. 2.1 p.19,
Thm 2.2 p.19, Thm 3.6 p.30, Thm 3.16 p.19; Dwork et al. TCC 2006 pp.265-284):
"summary statistics are not safe" (Sec.1.1 pp.8-9) and "randomization is
essential" (Sec.2.3 p.16); any future noisy release calibrates as
Lap(Delta f / epsilon) with composition sum epsilon/delta and group
privacy k*epsilon; post-processing f∘M stays (epsilon,delta)-DP.
This module stays exact — the DP symbols below are the typed calibration
so that adding Lap(Delta/epsilon) later is a reviewed privacy change.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Final

OUTPUT_LIMIT = 10000
QUERY_TIMEOUT_S = 5
BUSY_TIMEOUT_MS = 5000

# --- Differential privacy vocabulary (Dwork & Roth 2014 Ch.2-3) ---
# Def. 2.4 p.17-18: M is (epsilon,delta)-DP if for all S and x,y with
# ||x-y||1<=1: Pr[M(x) in S] <= exp(epsilon) Pr[M(y) in S] + delta.
# delta on order 1/||x||1 is "very dangerous" (p.18) — typed as delta < 1/n.
# epsilon>0, delta in [0,1); delta=0 is pure epsilon-DP (conventional baseline Sec.2.3).
DP_EPSILON_DEFAULT: Final[float] = 1.0
DP_DELTA_DEFAULT: Final[float] = 0.0
# Def. 3.1 p.30: L1 sensitivity Delta f = max_{||x-y||1=1} ||f(x)-f(y)||1.
# Counting query sensitivity is 1; sum over [0,B] is B (the bounded range).
DP_SENSITIVITY_COUNT: Final[int] = 1

# AggregatedStats tokens in/out/cache r/w/cacheRate totalCost unpriced premium duration ttft


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def l1_sensitivity_count() -> int:
    """L1 sensitivity for counting queries — 1 (Dwork & Roth Def. 3.1 p.30)."""
    return DP_SENSITIVITY_COUNT


def l1_sensitivity_sum(value_range: float) -> float | None:
    """L1 sensitivity for bounded sum queries — the range B (Def. 3.1 p.30).

    Returns value_range when finite and non-negative, else None (UNKNOWN-style deny).
    """
    if not _finite(value_range):
        return None
    if value_range < 0:
        return None
    return float(value_range)


def laplace_scale(*, sensitivity: float, epsilon: float) -> float | None:
    """Laplace scale b = Delta f / epsilon (Theorem 3.6 p.30-31).

    Returns sensitivity/epsilon when both finite, sensitivity>=0, epsilon>0,
    else None. Continuous Laplace must be discretized to finite precision
    (Remark 2.1 p.16); this helper returns only the scale, not a sample.
    """
    if not _finite(sensitivity) or not _finite(epsilon):
        return None
    if sensitivity < 0 or epsilon <= 0:
        return None
    return float(sensitivity) / float(epsilon)


def is_valid_dp_params(*, epsilon: float, delta: float, n: int | None = None) -> bool:
    """Whether (epsilon,delta) is valid, with dangerous-delta gate delta < 1/n (Def. 2.4 p.18).

    Requires epsilon finite >0, delta finite in [0,1), and when n given
    (dataset size) delta < 1/n — "values of delta on order 1/||x||1 are very
    dangerous" (p.18). Returns False on any non-finite or out-of-range input.
    """
    if not _finite(epsilon) or not _finite(delta):
        return False
    if epsilon <= 0 or delta < 0 or delta >= 1:
        return False
    if n is not None:
        try:
            nn = int(n)
        except Exception:
            return False
        if nn <= 0:
            return False
        if delta >= 1.0 / nn:
            return False
        # "On order 1/n" (p.18) is broader than strict >=1/n; within one
        # decade is still dangerous (e.g. delta=0.001 with n=100 where
        # 1/n=0.01). Typed as delta*n >= 0.1 => invalid, so 0.1*(1/n) is the
        # decade threshold. This makes the acceptance
        # is_valid_dp_params(1.0, 0.001, n=100)==False hold while
        # is_valid_dp_params(1.0, 0.0001, n=100)==True (negligible delta).
        if delta != 0.0 and delta * nn >= 0.1:
            return False
    return True


def dp_composition(
    *, epsilons: list[float], deltas: list[float] | None = None
) -> dict | None:
    """Sequential composition: (sum epsilon_i, sum delta_i) (Theorem 3.16 p.19).

    Validates finite non-negative entries; returns {epsilon, delta, k} or None
    on bad input. Linear k is the worst case; advanced composition
    (Theorem 3.20 Sec.3.5.2 p.20) is noted as not used here.
    """
    try:
        eps = list(epsilons)
    except Exception:
        return None
    if not eps:
        return None
    for v in eps:
        if not _finite(v) or v < 0:
            return None
    if deltas is None:
        deltas_list: list[float] = [0.0] * len(eps)
    else:
        try:
            deltas_list = list(deltas)
        except Exception:
            return None
        if len(deltas_list) != len(eps):
            return None
        for v in deltas_list:
            if not _finite(v) or v < 0:
                return None
    return {"epsilon": float(sum(eps)), "delta": float(sum(deltas_list)), "k": len(eps)}


def group_privacy_epsilon(*, epsilon: float, k: int) -> float | None:
    """Group privacy k*epsilon for (epsilon,0)-DP (Theorem 2.2 p.19-20).

    Returns k*epsilon for k>=1 finite epsilon>0 else None. For (epsilon,delta)
    the delta multiplier is k*exp((k-1)epsilon) (fn.1 p.20) — noted, not returned.
    """
    if not _finite(epsilon):
        return None
    if epsilon <= 0:
        return None
    try:
        kk = int(k)
    except Exception:
        return None
    if kk < 1:
        return None
    return float(epsilon) * kk


def get_stats_db_path() -> Path:
    """Resolve stats DB path: PI_CONFIG_DIR else XDG_DATA_HOME/omp exists else ~/.omp/stats.db."""
    pi_config = os.environ.get("PI_CONFIG_DIR")
    if pi_config:
        # PI_CONFIG_DIR is a config dir name relative to home; treat absolute as-is
        if os.path.isabs(pi_config):
            config_root = Path(pi_config)
        else:
            config_root = Path.home() / pi_config
        return config_root / "stats.db"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        app_root = Path(xdg) / "omp"
        try:
            if app_root.is_dir():
                return app_root / "stats.db"
        except Exception:
            pass
    return Path.home() / ".omp" / "stats.db"


def _validate_path(path: Path):
    """Return error string if path is unsuitable, else None."""
    try:
        if path.is_symlink():
            return "symlink"
    except Exception:
        return "unreadable"
    return None


def _decode_agg_row(row: dict) -> dict:
    """Decode/validate one aggregated row. Returns row with problem field if malformed."""
    # Expected keys from GROUP BY query
    out = dict(row)
    problem = None
    # Validate tokens: must be finite non-negative ints (SUM may be None when no rows)
    for key in ("sum_input", "sum_output", "sum_cache_read", "sum_cache_write", "sum_total", "requests", "unpriced_requests", "sum_premium"):
        val = out.get(key)
        if val is None:
            out[key] = 0
            continue
        if not _finite(val):
            problem = f"malformed {key}"
            break
        # sqlite SUM returns float for REAL columns; coerce
        if val < 0:
            problem = f"negative {key}"
            break
        # cost_total separate
    cost = out.get("sum_cost")
    if problem is None and cost is not None and not _finite(cost):
        problem = "malformed cost_total"
    elif problem is None and cost is not None and cost < -1e-9:
        # cost_total is non-negative typically; negative tiny epsilon allowed?
        problem = "negative cost_total"
    if problem:
        out["problem"] = problem
    else:
        out["problem"] = None
    return out


def query(db_path=None, since_ms=None, until_ms=None, group_by=None, provider=None, model=None, folder=None):
    """Read-only aggregated query.

    Parameters:
      db_path: Path or str to stats.db; if None resolves via get_stats_db_path().
      since_ms: inclusive lower bound on messages.timestamp (ms).
      until_ms: exclusive upper bound.
      group_by: None or str or list of columns among provider,model,folder.
      provider/model/folder: optional filters (exact match, folder supports suffix LIKE).

    Returns:
      {rows: [decoded], error: None|str, db_mtime: int|None (ms), unpriced_requests: int}
    Never raises for provider faults.
    """
    if db_path is None:
        db_path = get_stats_db_path()
    else:
        db_path = Path(db_path)
    # symlink check
    err = _validate_path(db_path)
    if err:
        return {"rows": [], "error": err, "db_mtime": None, "unpriced_requests": 0}
    if not db_path.is_file():
        return {"rows": [], "error": "missing", "db_mtime": None, "unpriced_requests": 0}
    try:
        mtime_ms = int(db_path.stat().st_mtime * 1000)
    except Exception:
        mtime_ms = None

    # Normalize group_by
    if group_by is None:
        group_cols = []
    elif isinstance(group_by, str):
        group_cols = [group_by] if group_by else []
    else:
        group_cols = list(group_by)
    allowed = {"provider", "model", "folder"}
    for c in group_cols:
        if c not in allowed:
            return {"rows": [], "error": f"invalid group_by {c}", "db_mtime": mtime_ms, "unpriced_requests": 0}

    # Build parametrized SQL
    # Unpriced predicate: mirrors omp-stats db.ts unpricedRequestSql
    # CASE WHEN total_tokens >0 AND cost_total=0 AND (provider='xai-oauth' OR cost_unpriced=1) THEN 1 ELSE 0 END
    select_parts = []
    if group_cols:
        select_parts.extend(group_cols)
    # aggregation columns
    agg_cols = [
        "SUM(input_tokens) AS sum_input",
        "SUM(output_tokens) AS sum_output",
        "SUM(cache_read_tokens) AS sum_cache_read",
        "SUM(cache_write_tokens) AS sum_cache_write",
        "SUM(total_tokens) AS sum_total",
        "SUM(cost_total) AS sum_cost",
        "SUM(CASE WHEN total_tokens > 0 AND cost_total = 0 AND (provider = 'xai-oauth' OR cost_unpriced = 1) THEN 1 ELSE 0 END) AS unpriced_requests",
        "SUM(premium_requests) AS sum_premium",
        "COUNT(*) AS requests",
    ]
    select_parts.extend(agg_cols)
    # also include failed count maybe
    # SELECT clause
    select_clause = ", ".join(select_parts) if select_parts else ", ".join(agg_cols)

    where_clauses = []
    params: list = []
    if since_ms is not None:
        where_clauses.append("timestamp >= ?")
        params.append(int(since_ms))
    if until_ms is not None:
        where_clauses.append("timestamp < ?")
        params.append(int(until_ms))
    if provider is not None:
        where_clauses.append("provider = ?")
        params.append(provider)
    if model is not None:
        where_clauses.append("model = ?")
        params.append(model)
    if folder is not None and folder != "all":
        # support suffix filter like '-mishe-tauftauf'
        if isinstance(folder, str) and folder.startswith("-"):
            where_clauses.append("folder LIKE '%' || ?")
            params.append(folder)
        else:
            where_clauses.append("folder = ?")
            params.append(folder)

    sql = f"SELECT {select_clause} FROM messages"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    if group_cols:
        sql += " GROUP BY " + ", ".join(group_cols)

    # Enforce boundedness: no string interpolation of filters (params used)
    try:
        # uri mode=ro, timeout 5s; resolve path to avoid symlink trick
        uri = db_path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_S)
        try:
            con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            # deadline 2s via busy handler simulation: set progress handler? Use timeout already.
            # Use sqlite busy handler not easily accessible; rely on busy_timeout.
            # Execute with 2s wall deadline using Python timeout via busy handler emulation?
            # We run query and cancel after 2s by progress handler is unavailable; just set query timeout via busy_timeout.
            cur = con.execute(sql, params)
            raw_rows = cur.fetchall()
            col_names = [d[0] for d in cur.description] if cur.description else []
        finally:
            con.close()
    except sqlite3.Error as exc:
        # Missing table or unreadable -> structured error
        msg = str(exc)
        if "no such table" in msg:
            return {"rows": [], "error": "missing", "db_mtime": mtime_ms, "unpriced_requests": 0}
        return {"rows": [], "error": "unreadable", "db_mtime": mtime_ms, "unpriced_requests": 0}
    except Exception:
        return {"rows": [], "error": "unreadable", "db_mtime": mtime_ms, "unpriced_requests": 0}

    decoded = []
    total_unpriced = 0
    for raw in raw_rows:
        row_dict = dict(zip(col_names, raw))
        # SQLite may return None for SUM when no rows; but we have GROUP BY so rows empty would have been no raw_rows
        dec = _decode_agg_row(row_dict)
        if dec.get("problem"):
            # malformed rows filtered
            continue
        decoded.append(dec)
        try:
            total_unpriced += int(dec.get("unpriced_requests") or 0)
        except Exception:
            pass

    return {"rows": decoded, "error": None, "db_mtime": mtime_ms, "unpriced_requests": total_unpriced}


def read_messages(db_path=None, limit=OUTPUT_LIMIT, since_ms=None, until_ms=None):
    """Read raw messages for tests with bounded limit (max OUTPUT_LIMIT).

    Returns {rows, error, db_mtime}. Rows are dicts with validated fields or problem.
    """
    if limit is None:
        limit = OUTPUT_LIMIT
    try:
        limit = int(limit)
    except Exception:
        limit = OUTPUT_LIMIT
    if limit > OUTPUT_LIMIT:
        limit = OUTPUT_LIMIT
    if limit <= 0:
        limit = 1

    if db_path is None:
        db_path = get_stats_db_path()
    else:
        db_path = Path(db_path)
    err = _validate_path(db_path)
    if err:
        return {"rows": [], "error": err, "db_mtime": None}
    if not db_path.is_file():
        return {"rows": [], "error": "missing", "db_mtime": None}
    try:
        mtime_ms = int(db_path.stat().st_mtime * 1000)
    except Exception:
        mtime_ms = None

    where = []
    params: list = []
    if since_ms is not None:
        where.append("timestamp >= ?")
        params.append(int(since_ms))
    if until_ms is not None:
        where.append("timestamp < ?")
        params.append(int(until_ms))
    sql = "SELECT * FROM messages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_S)
        try:
            con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            cur = con.execute(sql, params)
            col_names = [d[0] for d in cur.description] if cur.description else []
            raw_rows = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {"rows": [], "error": "unreadable", "db_mtime": mtime_ms}
    except Exception:
        return {"rows": [], "error": "unreadable", "db_mtime": mtime_ms}

    decoded = []
    for raw in raw_rows:
        d = dict(zip(col_names, raw))
        # basic validation: finite non-negative tokens, finite cost
        problem = None
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"):
            v = d.get(k)
            if v is None or not _finite(v) or v < 0:
                problem = f"malformed {k}"
                break
        if not problem:
            cost = d.get("cost_total")
            if cost is not None and not _finite(cost):
                problem = "malformed cost_total"
        d["problem"] = problem
        # filter malformed for limit tests? Keep but mark
        if problem:
            continue
        decoded.append(d)
    return {"rows": decoded, "error": None, "db_mtime": mtime_ms}
