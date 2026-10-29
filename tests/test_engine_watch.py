"""Engine watch: collector, staleness guard, proposal and alert semantics."""
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mishe_tauftauf.engine_watch as ew
import mishe_tauftauf.engine_policy as policy

NOW = 1_792_712_432_436


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(remaining=1.0, reset_ms=NOW + 3_600_000, limit="rolling-5h", window_id="5h"):
    return {"id": limit, "window_id": window_id, "label": limit, "remaining_fraction": remaining,
            "reset_at": iso(reset_ms), "reset_ms": reset_ms, "unit": "percent", "status": "ok", "reason": None}


def account(alias, provider, state="FRESH", windows=None):
    if windows is None:
        windows = [window()]
    return {"alias": alias, "provider": provider, "state": state, "reason": None,
            "windows": windows, "fetched_at": iso(NOW - 10_000), "age_seconds": 10,
            "auth": {"state": "ok", "reason": None, "blocked_until": None},
            "last_good": None}


def status_doc(accounts):
    return {"state": "FRESH", "observed_at": iso(NOW), "ttl_seconds": 180,
            "omp_generated_at": {}, "accounts": accounts, "coverage": {}}


def route(alias, provider, model="model-a"):
    return {"provider": provider, "alias": alias, "model": model, "effort": "high", "runner": "omp"}


def _registry(home, entries):
    # minimal engine/accounts.json for _load_allowlist fallback
    home = Path(home)
    (home / "engine").mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "store": "omp-recovery/agent", "accounts": {}}
    for alias, (provider, state, cid) in entries.items():
        data["accounts"][alias] = {"provider": provider, "state": state, "credential_id": cid}
        if alias != "go-a" and state == "configured":
            # ensure separate store not needed for test; monkeypatch avoids reading it
            pass
    (home / "engine/accounts.json").write_text(json.dumps(data))


def _write_store_dummy(home):
    d = Path(home) / "omp-recovery/agent"
    d.mkdir(parents=True, exist_ok=True)
    # create empty agent.db so credential_store does not explode if called
    import sqlite3
    con = sqlite3.connect(str(d / "agent.db"))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS credentials (id INTEGER PRIMARY KEY, provider TEXT, type TEXT, disabled TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS auth_blocks (credential_id INTEGER, blocked_until TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','8')")
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
def test_collector_writes_fresh_cache(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _write_store_dummy(home)
    _registry(home, {"go-a": ("opencode-go", "configured", 8)})
    fake_status = status_doc({"go-a": account("go-a", "opencode-go", "FRESH", [window(0.5)])})
    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", lambda h, now_ms=None, omp="omp": fake_status)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, window="24h", **kw: {"state": "FRESH", "observed_at": iso(NOW), "window": "24h"})

    cache = ew.once(home, now_ms=NOW)
    p = home / "engine/watch-cache.json"
    assert p.is_file()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert cache["engine_state"] == "FRESH"
    assert cache["stats_state"] == "FRESH"
    assert "decisions" in cache
    # next_reconsider present
    assert cache["next_reconsider"] is not None

    # check returns FRESH within tolerance
    r = ew.check(home, now_ms=NOW + 1_000)
    assert r["state"] == "FRESH"
    assert r["status"] == "GREEN"


def test_stale_cache_is_unknown(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _write_store_dummy(home)
    _registry(home, {"go-a": ("opencode-go", "configured", 8)})
    fake_status = status_doc({"go-a": account("go-a", "opencode-go")})
    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", lambda h, now_ms=None, omp="omp": fake_status)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, window="24h", **kw: {"state": "FRESH", "observed_at": iso(NOW)})

    ew.once(home, now_ms=NOW)
    p = home / "engine/watch-cache.json"
    # force mtime to be stale (>180s)
    old = time.time() - 200
    os.utime(p, (old, old))
    r = ew.check(home, now_ms=int(time.time() * 1000))
    assert r["state"] == "UNKNOWN"
    assert r["status"] == "RED"
    assert "stale" in r["reason"].lower()
    # alert file appended exactly once extra
    alert = home / "health/engine-watch-alert.jsonl"
    assert alert.is_file()
    assert stat.S_IMODE(alert.stat().st_mode) == 0o600
    lines_before = alert.read_text().strip().splitlines()
    r2 = ew.check(home, now_ms=int(time.time() * 1000))
    lines_after = alert.read_text().strip().splitlines()
    assert len(lines_after) == len(lines_before) + 1
    # never erases original: first alert still has stale reason
    assert "stale" in lines_after[0]


def test_missing_cache_is_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    r = ew.check(home, now_ms=NOW)
    assert r["state"] == "UNKNOWN"
    assert r["status"] == "RED"
    assert "missing" in r["reason"].lower()
    alert = home / "health/engine-watch-alert.jsonl"
    assert alert.is_file()
    assert stat.S_IMODE(alert.stat().st_mode) == 0o600


def test_malformed_cache_is_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    p = home / "engine/watch-cache.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    os.chmod(p, 0o600)
    r = ew.check(home, now_ms=NOW)
    assert r["state"] == "UNKNOWN"
    assert r["status"] == "RED"
    assert "malformed" in r["reason"].lower()
    alert = home / "health/engine-watch-alert.jsonl"
    assert alert.is_file()


def test_broken_alert_delivery_still_leaves_unknown(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    # ensure alert parent exists as file to force append failure first, but check should not raise
    p = home / "engine/watch-cache.json"
    # no cache -> missing
    # monkeypatch append to fail for alert path only
    orig = ew._append_jsonl

    def failing(path, obj):
        if "engine-watch-alert" in str(path):
            raise OSError("disk full")
        return orig(path, obj)

    monkeypatch.setattr(ew, "_append_jsonl", failing)
    r = ew.check(home, now_ms=NOW)
    assert r["state"] == "UNKNOWN"
    assert r["status"] == "RED"


def test_all_provider_exhausted_is_queued_with_binding_reset(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _write_store_dummy(home)
    # two go accounts depleted
    reset = NOW + 3_600_000
    fake_status = status_doc({
        "go-a": account("go-a", "opencode-go", "FRESH", [window(0.0, reset_ms=reset)]),
        "go-b": account("go-b", "opencode-go", "FRESH", [window(0.0, reset_ms=reset + 100000)]),
        "codex-a": account("codex-a", "openai-codex", "FRESH", [window(0.0, reset_ms=reset + 200000)]),
    })
    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", lambda h, now_ms=None, omp="omp": fake_status)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, window="24h", **kw: {"state": "FRESH", "observed_at": iso(NOW)})
    # policy.json allowlist to include those three
    (home / "engine").mkdir(parents=True, exist_ok=True)
    pol = {
        "default": [route("go-a", "opencode-go"), route("go-b", "opencode-go"), route("codex-a", "openai-codex")]
    }
    (home / "engine/policy.json").write_text(json.dumps(pol))

    cache = ew.once(home, now_ms=NOW)
    dec = cache["decisions"]["default"]
    assert dec["decision"] == "queue"
    assert dec["selected"] is None
    # binding reset present and earliest
    assert "binding_reset" in dec
    # no proposal emitted for queue
    proposals = list((home / "engine/proposals").glob("*.json")) if (home / "engine/proposals").exists() else []
    # queue should not have produced select proposal
    for pp in proposals:
        datum = json.loads(pp.read_text())
        assert datum["decision"]["decision"] != "select" or datum["role"] != "default"


def test_reverted_activation_rm_cache_rerun_fresh(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _write_store_dummy(home)
    _registry(home, {"go-a": ("opencode-go", "configured", 8)})
    fake_status = status_doc({"go-a": account("go-a", "opencode-go")})
    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", lambda h, now_ms=None, omp="omp": fake_status)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, window="24h", **kw: {"state": "FRESH", "observed_at": iso(NOW)})

    first = ew.once(home, now_ms=NOW)
    p = home / "engine/watch-cache.json"
    assert p.is_file()
    first_text = p.read_text()
    # remove and reapply produce same logical state (fresh)
    p.unlink()
    assert ew.check(home, now_ms=NOW + 1_000)["state"] == "UNKNOWN"
    second = ew.once(home, now_ms=NOW + 5_000)
    assert (home / "engine/watch-cache.json").is_file()
    assert ew.check(home, now_ms=NOW + 6_000)["state"] == "FRESH"
    # decisions shapes equal (except observed_at/ms)
    assert set(first["decisions"].keys()) == set(second["decisions"].keys())
    # error: ensure file mode preserved
    assert stat.S_IMODE((home / "engine/watch-cache.json").stat().st_mode) == 0o600


def test_guard_when_monitoring_broken_stays_unknown_red(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    # no initial cache -> unknown red
    r1 = ew.check(home, now_ms=NOW)
    assert r1["state"] == "UNKNOWN" and r1["status"] == "RED"
    # after a collector failure that still writes? simulate engine failure producing UNKNOWN cache -> still fresh guard
    # but if monitoring broken (stale cache not refreshed), guard stays unknown red
    _write_store_dummy(home)
    _registry(home, {"go-a": ("opencode-go", "configured", 8)})
    good = status_doc({"go-a": account("go-a", "opencode-go")})
    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", lambda h, now_ms=None, omp="omp": good)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, window="24h", **kw: {"state": "FRESH", "observed_at": iso(NOW)})
    ew.once(home, now_ms=NOW)
    assert ew.check(home, now_ms=NOW + 10_000)["state"] == "FRESH"
    # stale the cache and do NOT run once -> guard stays unknown/red
    p = home / "engine/watch-cache.json"
    old = time.time() - 190
    os.utime(p, (old, old))
    r2 = ew.check(home, now_ms=int(time.time() * 1000))
    assert r2["state"] == "UNKNOWN" and r2["status"] == "RED"
    # broken collector should have logged to watch-errors but guard still reports stale
    # now recovery: run once again -> fresh
    ew.once(home, now_ms=int(time.time() * 1000))
    r3 = ew.check(home, now_ms=int(time.time() * 1000))
    assert r3["state"] == "FRESH" and r3["status"] == "GREEN"
    # and alert file was append-only: stale alert kept
    alerts = (home / "health/engine-watch-alert.jsonl").read_text().strip().splitlines() if (home / "health/engine-watch-alert.jsonl").exists() else []
    # at least one stale entry still present
    assert any("stale" in a for a in alerts)


def test_watch_errors_on_collector_failure(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _write_store_dummy(home)
    _registry(home, {"go-a": ("opencode-go", "configured", 8)})

    def boom(*a, **kw):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("mishe_tauftauf.engine_usage.status", boom)
    monkeypatch.setattr("mishe_tauftauf.token_usage.report", lambda h, **kw: {"state": "FRESH", "observed_at": iso(NOW)})
    cache = ew.once(home, now_ms=NOW)
    # cache still written despite error
    assert (home / "engine/watch-cache.json").is_file()
    assert stat.S_IMODE((home / "engine/watch-cache.json").stat().st_mode) == 0o600
    errors = home / "engine/watch-errors.jsonl"
    assert errors.is_file()
    assert stat.S_IMODE(errors.stat().st_mode) == 0o600
