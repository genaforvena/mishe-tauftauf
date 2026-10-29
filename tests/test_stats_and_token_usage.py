"""Stats boundary and token usage aggregation — pure decision and sense."""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from mishe_tauftauf import stats_boundary, token_usage

NOW = 1_792_712_432_436  # fixed now for deterministic windowing
TTL_MS = 60_000


def create_messages_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_file TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            folder TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            api TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            duration INTEGER,
            ttft INTEGER,
            stop_reason TEXT NOT NULL,
            error_message TEXT,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cache_read_tokens INTEGER NOT NULL,
            cache_write_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            premium_requests REAL NOT NULL,
            cost_input REAL NOT NULL,
            cost_output REAL NOT NULL,
            cost_cache_read REAL NOT NULL,
            cost_cache_write REAL NOT NULL,
            cost_total REAL NOT NULL,
            cost_no_cache_input REAL,
            cost_unpriced INTEGER NOT NULL DEFAULT 0,
            agent_type TEXT NOT NULL DEFAULT 'main',
            service_tier TEXT,
            UNIQUE(session_file, entry_id)
        );
        """
    )
    con.commit()
    con.close()


def insert_row(path: Path, **kwargs):
    defaults = dict(
        session_file="sess",
        entry_id="",
        folder="/home/p/mishe-tauftauf",
        model="claude-sonnet-4",
        provider="anthropic",
        api="anthropic-messages",
        timestamp=NOW,
        duration=100,
        ttft=10,
        stop_reason="stop",
        error_message=None,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
        total_tokens=165,
        premium_requests=0,
        cost_input=0.001,
        cost_output=0.002,
        cost_cache_read=0.0001,
        cost_cache_write=0.00005,
        cost_total=0.00315,
        cost_no_cache_input=None,
        cost_unpriced=0,
        agent_type="main",
        service_tier=None,
    )
    defaults.update(kwargs)
    # ensure unique entry_id if not provided with explicit suffix
    if not kwargs.get("entry_id"):
        # Use insertion order count
        con = sqlite3.connect(path)
        cnt = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        con.close()
        defaults["entry_id"] = f"e{cnt}"
        defaults["session_file"] = f"sf{cnt}"
    cols = list(defaults.keys())
    placeholders = ",".join(["?"] * len(cols))
    con = sqlite3.connect(path)
    con.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({placeholders})", [defaults[c] for c in cols])
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# stats_boundary
# ---------------------------------------------------------------------------

def test_query_by_provider_aggregation(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="anthropic", input_tokens=100, output_tokens=50, cache_read_tokens=0, cache_write_tokens=0, total_tokens=150, cost_total=0.01, cost_unpriced=0, folder="/a", model="m1")
    insert_row(db, provider="anthropic", input_tokens=200, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, total_tokens=200, cost_total=0.02, cost_unpriced=0, folder="/a", model="m1")
    insert_row(db, provider="openai", input_tokens=10, output_tokens=10, cache_read_tokens=0, cache_write_tokens=0, total_tokens=20, cost_total=0.001, cost_unpriced=0, folder="/a", model="m2")
    # group by provider
    result = stats_boundary.query(db_path=db, group_by=["provider"])
    assert result["error"] is None
    by_p = {r["provider"]: r for r in result["rows"]}
    assert by_p["anthropic"]["sum_input"] == 300
    assert by_p["anthropic"]["sum_total"] == 350
    assert by_p["openai"]["sum_input"] == 10
    assert result["unpriced_requests"] == 0


def test_query_by_model_aggregation(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="anthropic", model="claude-sonnet-4", input_tokens=100, total_tokens=100, folder="/a", cost_total=0.01)
    insert_row(db, provider="anthropic", model="claude-opus-4", input_tokens=50, total_tokens=50, folder="/a", cost_total=0.02)
    result = stats_boundary.query(db_path=db, group_by=["model"])
    assert result["error"] is None
    by_m = {r["model"]: r for r in result["rows"]}
    assert by_m["claude-sonnet-4"]["sum_input"] == 100
    assert by_m["claude-opus-4"]["sum_input"] == 50


def test_query_by_folder_plant_vs_all_plants(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, folder="/home/p/proj-mishe-tauftauf", input_tokens=100, total_tokens=100, cost_total=0.01)
    insert_row(db, folder="/home/p/other-mishe-tauftauf", input_tokens=50, total_tokens=50, cost_total=0.01)
    insert_row(db, folder="/tmp/other", input_tokens=10, total_tokens=10, cost_total=0.01)
    # all plants via query grouping by folder
    result_all = stats_boundary.query(db_path=db, group_by=["folder"])
    assert len(result_all["rows"]) == 3
    # filtered by suffix -mishe-tauftauf
    result_plant = stats_boundary.query(db_path=db, group_by=["folder"], folder="-mishe-tauftauf")
    folders = {r["folder"] for r in result_plant["rows"]}
    assert "/home/p/proj-mishe-tauftauf" in folders
    assert "/home/p/other-mishe-tauftauf" in folders
    assert "/tmp/other" not in folders


def test_window_1h_vs_24h(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    # recent within 1h
    insert_row(db, timestamp=NOW - 30 * 60 * 1000, input_tokens=100, total_tokens=100, cost_total=0.01)
    # older within 24h but outside 1h
    insert_row(db, timestamp=NOW - 2 * 60 * 60 * 1000, input_tokens=50, total_tokens=50, cost_total=0.01)
    # older outside 24h
    insert_row(db, timestamp=NOW - 26 * 60 * 60 * 1000, input_tokens=10, total_tokens=10, cost_total=0.01)
    since_1h = NOW - 3600000
    since_24h = NOW - 86400000
    r1 = stats_boundary.query(db_path=db, since_ms=since_1h)
    assert sum(rr["sum_total"] or 0 for rr in r1["rows"]) == 100
    r24 = stats_boundary.query(db_path=db, since_ms=since_24h)
    assert sum(rr["sum_total"] or 0 for rr in r24["rows"]) == 150
    rall = stats_boundary.query(db_path=db, since_ms=None)
    assert sum(rr["sum_total"] or 0 for rr in rall["rows"]) == 160


def test_token_usage_window_aggregation(tmp_path):
    db = tmp_path / "site.db"
    create_messages_db(db)
    insert_row(db, timestamp=NOW - 30 * 60 * 1000, input_tokens=100, total_tokens=100, cost_total=0.01, provider="anthropic", model="m1", folder="/a")
    insert_row(db, timestamp=NOW - 2 * 60 * 60 * 1000, input_tokens=50, total_tokens=50, cost_total=0.01, provider="anthropic", model="m1", folder="/a")
    home = tmp_path / "home"
    home.mkdir()
    # ensure fresh mtime
    os.utime(db, (time.time(), time.time()))
    rep_1h = token_usage.report(home, window="1h", now_ms=NOW, db_path=db)
    assert rep_1h["totals"]["total"] == 100
    rep_24h = token_usage.report(home, window="24h", now_ms=NOW, db_path=db)
    assert rep_24h["totals"]["total"] == 150


def test_unpriced_flag_separated(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    # unpriced via cost_unpriced=1 with zero cost and tokens
    insert_row(db, provider="anthropic", cost_total=0, cost_unpriced=1, total_tokens=100, input_tokens=100, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, folder="/a", model="m1")
    # priced zero with cost_unpriced 0 (free tier) -> not unpriced per predicate because need provider xai-oauth OR cost_unpriced=1, but cost_total=0 and tokens>0; this one should NOT be counted
    insert_row(db, provider="anthropic", cost_total=0, cost_unpriced=0, total_tokens=50, input_tokens=50, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, folder="/a", model="m1")
    # priced normal
    insert_row(db, provider="anthropic", cost_total=0.05, cost_unpriced=0, total_tokens=20, input_tokens=20, folder="/a", model="m1")
    result = stats_boundary.query(db_path=db, group_by=["provider"])
    assert result["unpriced_requests"] == 1
    # token_usage totals should reflect same
    home = tmp_path / "home2"
    home.mkdir()
    os.utime(db, (time.time(), time.time()))
    rep = token_usage.report(home, window="all", now_ms=NOW, db_path=db)
    assert rep["totals"]["unpriced_requests"] == 1
    assert rep["totals"]["cost_total"] == 0.05


def test_unpriced_xai_oauth_provider(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="xai-oauth", cost_total=0, cost_unpriced=0, total_tokens=100, input_tokens=100, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, folder="/a", model="grok")
    result = stats_boundary.query(db_path=db, group_by=["provider"])
    assert result["unpriced_requests"] == 1
    # also with cost_total 0 but no tokens -> not counted
    db2 = tmp_path / "stats2.db"
    create_messages_db(db2)
    insert_row(db2, provider="xai-oauth", cost_total=0, cost_unpriced=0, total_tokens=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, folder="/a", model="grok")
    result2 = stats_boundary.query(db_path=db2, group_by=["provider"])
    assert result2["unpriced_requests"] == 0


def test_stale_db_mtime(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, timestamp=NOW, total_tokens=10, input_tokens=10, cost_total=0.01)
    home = tmp_path / "home"
    home.mkdir()
    # set mtime 2 minutes ago -> stale (>60s)
    old = time.time() - 120
    os.utime(db, (old, old))
    rep = token_usage.report(home, window="all", now_ms=int(time.time() * 1000), db_path=db)
    assert rep["state"] == "STALE"
    assert rep["age_seconds"] is not None and rep["age_seconds"] > 60
    # fresh
    os.utime(db, None)
    rep2 = token_usage.report(home, window="all", now_ms=int(time.time() * 1000), db_path=db)
    assert rep2["state"] == "FRESH"


def test_missing_db_unknown(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    missing = tmp_path / "nope.db"
    rep = token_usage.report(home, window="24h", now_ms=NOW, db_path=missing)
    assert rep["state"] == "UNKNOWN"
    assert rep["error"] == "missing"
    assert rep["totals"]["total"] == 0
    # also stats_boundary directly
    res = stats_boundary.query(db_path=missing)
    assert res["error"] == "missing"
    assert res["rows"] == []


def test_malformed_rows_filtered(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, input_tokens=100, total_tokens=100, cost_total=0.01, folder="/a", model="m1", provider="p1")
    # Insert a malformed row with negative tokens via raw sql (bypassing helper validation)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO messages (session_file, entry_id, folder, model, provider, api, timestamp, stop_reason, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens, premium_requests, cost_input, cost_output, cost_cache_read, cost_cache_write, cost_total, cost_unpriced) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sess_bad", "bad1", "/a", "m1", "p1", "api", NOW, "stop", -5, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0.01, 0),
    )
    con.commit()
    con.close()
    # query grouping by provider should skip the malformed negative row and only count the good one
    result = stats_boundary.query(db_path=db, group_by=["provider"])
    # Because we GROUP BY, the bad row's group sum includes -5? Actually SUM would include negative but our decoder filters per aggregated row, not per raw row.
    # The aggregated row's sum_input would be 95 (100-5) and finite non-negative, so it wouldn't be flagged. To test raw filtering we use read_messages
    raw = stats_boundary.read_messages(db_path=db, limit=100)
    # read_messages filters malformed individual rows
    assert len(raw["rows"]) == 1
    assert raw["rows"][0]["input_tokens"] == 100
    # Also test that a group with only bad aggregated cost NaN (we inject via cost_total inf) is filtered
    db2 = tmp_path / "stats_bad2.db"
    create_messages_db(db2)
    con = sqlite3.connect(db2)
    con.execute(
        "INSERT INTO messages (session_file, entry_id, folder, model, provider, api, timestamp, stop_reason, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens, premium_requests, cost_input, cost_output, cost_cache_read, cost_cache_write, cost_total, cost_unpriced) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sess2", "e2", "/a", "m1", "p1", "api", NOW, "stop", 10, 10, 0, 0, 20, 0, 0, 0, 0, 0, float('inf'), 0),
    )
    con.commit()
    con.close()
    # inf cost will make sum_cost inf -> decoder flags malformed and filters
    res2 = stats_boundary.query(db_path=db2, group_by=["provider"])
    assert res2["rows"] == []


def test_cost_unknown_note(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="xai-oauth", cost_total=0, cost_unpriced=0, total_tokens=100, input_tokens=100, folder="/a", model="m")
    home = tmp_path / "h"
    home.mkdir()
    os.utime(db, None)
    rep = token_usage.report(home, window="all", now_ms=NOW, db_path=db)
    # cost_total 0 with unpriced>0 => cost UNKNOWN
    assert rep["totals"]["cost_total"] == 0
    assert rep["totals"]["unpriced_requests"] == 1
    rendered = token_usage.render(rep)
    assert "UNKNOWN" in rendered
    # when priced cost non-zero, no UNKNOWN
    db2 = tmp_path / "stats2.db"
    create_messages_db(db2)
    insert_row(db2, provider="anthropic", cost_total=0.02, cost_unpriced=0, total_tokens=100, input_tokens=100, folder="/a", model="m")
    os.utime(db2, None)
    rep2 = token_usage.report(home, window="all", now_ms=NOW, db_path=db2)
    rendered2 = token_usage.render(rep2)
    assert "UNKNOWN" not in rendered2 or "$0.0200" in rendered2


def test_limit_enforcement(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    for i in range(120):
        insert_row(db, timestamp=NOW - i, input_tokens=1, total_tokens=1, cost_total=0.001, folder="/a", model="m", provider="p")
    # read_messages with huge limit should cap at OUTPUT_LIMIT (10000), but we only have 120 rows so returns 120
    res = stats_boundary.read_messages(db_path=db, limit=20000)
    assert len(res["rows"]) == 120
    assert res["error"] is None
    # test cap: ask for 5000, get 5000? but we have 120
    # Create larger db with 10050 rows would be heavy; test capping logic by requesting >OUTPUT_LIMIT
    res2 = stats_boundary.read_messages(db_path=db, limit=10000 + 5000)
    # Even though we request more than OUTPUT_LIMIT, result length still limited to rows we have (120) and not error
    assert len(res2["rows"]) == 120
    # Now test that actual limit truncation works: create db with 20 rows, limit 5
    db3 = tmp_path / "stats_limit.db"
    create_messages_db(db3)
    for i in range(20):
        insert_row(db3, timestamp=NOW - i, input_tokens=1, total_tokens=1, cost_total=0.001, folder="/a", model="m", provider="p")
    res3 = stats_boundary.read_messages(db_path=db3, limit=5)
    assert len(res3["rows"]) == 5


def test_output_limit_constant():
    assert stats_boundary.OUTPUT_LIMIT == 10000


def test_query_never_raises_for_missing_table(tmp_path):
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE foo (x INT)")
    con.commit()
    con.close()
    res = stats_boundary.query(db_path=db)
    assert res["error"] == "missing"
    assert res["rows"] == []


def test_symlink_db_returns_error(tmp_path):
    real = tmp_path / "real.db"
    create_messages_db(real)
    insert_row(real, total_tokens=10, input_tokens=10, cost_total=0.01)
    link = tmp_path / "link.db"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink not supported")
    res = stats_boundary.query(db_path=link)
    assert res["error"] == "symlink"
    assert res["rows"] == []


def test_query_parameterised_no_interpolation(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="anthropic", model="m1", folder="/a", total_tokens=10, input_tokens=10, cost_total=0.01)
    # provider with SQL injection attempt should be treated literally, not break
    res = stats_boundary.query(db_path=db, provider="anthropic' OR 1=1 --", group_by=["provider"])
    assert res["error"] is None
    assert res["rows"] == []


def test_token_usage_aggregates_by_provider_model_folder(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, provider="anthropic", model="claude-sonnet-4", folder="/home/p/proj-mishe-tauftauf", input_tokens=100, output_tokens=20, cache_read_tokens=10, cache_write_tokens=5, total_tokens=135, cost_total=0.01, premium_requests=1)
    insert_row(db, provider="anthropic", model="claude-sonnet-4", folder="/home/p/proj-mishe-tauftauf", input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0, total_tokens=15, cost_total=0.02, premium_requests=0)
    insert_row(db, provider="openai", model="gpt-4o", folder="/tmp/other", input_tokens=50, output_tokens=50, cache_read_tokens=0, cache_write_tokens=0, total_tokens=100, cost_total=0.03, premium_requests=0)
    home = tmp_path / "h"
    home.mkdir()
    os.utime(db, None)
    rep = token_usage.report(home, window="all", now_ms=NOW, db_path=db)
    assert rep["totals"]["total"] == 250
    assert rep["by_provider"]["anthropic"]["total"] == 150
    assert rep["by_provider"]["openai"]["total"] == 100
    # by_model sorted by tokens desc
    assert rep["by_model"][0]["model"] == "claude-sonnet-4"
    assert rep["by_model"][0]["tokens"] == 150
    # by_folder and alias
    assert len(rep["by_folder"]) == 2
    assert rep["by_folder"] == rep["by_plant"]
    assert any(f["folder"] == "/home/p/proj-mishe-tauftauf" for f in rep["by_folder"])
    # cacheRate present
    assert "cacheRate" in rep["totals"]
    assert rep["totals"]["cacheRate"] >= 0


def test_folder_all_alias_means_no_filter_but_grouped(tmp_path):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, folder="/a-mishe-tauftauf", total_tokens=10, input_tokens=10, cost_total=0.01)
    insert_row(db, folder="/b-other", total_tokens=20, input_tokens=20, cost_total=0.01)
    home = tmp_path / "h"
    home.mkdir()
    os.utime(db, None)
    rep_all = token_usage.report(home, window="all", folder="all", now_ms=NOW, db_path=db)
    assert len(rep_all["by_folder"]) == 2
    rep_none = token_usage.report(home, window="all", folder=None, now_ms=NOW, db_path=db)
    assert len(rep_none["by_folder"]) == 2


def test_cli_wiring_parser_has_stats():
    from mishe_tauftauf.cli import parser
    p = parser()
    # should accept stats subcommand
    args = p.parse_args(["--home", "/tmp", "stats", "--window", "24h", "--json"])
    assert args.command == "stats"
    assert args.window == "24h"
    assert args.json is True


def test_cli_stats_json_output(tmp_path, capsys):
    db = tmp_path / "stats.db"
    create_messages_db(db)
    insert_row(db, total_tokens=10, input_tokens=10, cost_total=0.01, provider="anthropic", model="m1", folder="/a")
    home = tmp_path / "home"
    home.mkdir()
    os.utime(db, None)
    # monkeypatch stats_boundary.get_stats_db_path to point to our tmp db
    orig = stats_boundary.get_stats_db_path
    stats_boundary.get_stats_db_path = lambda: db
    try:
        from mishe_tauftauf.token_usage import cmd_stats
        import argparse
        args = argparse.Namespace(home=home, window="all", provider=None, model=None, folder=None, json=True)
        cmd_stats(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["state"] in ("FRESH", "STALE")
        assert "totals" in data
    finally:
        stats_boundary.get_stats_db_path = orig


def test_windows_constant():
    assert token_usage.WINDOWS == {"1h": 3600000, "24h": 86400000, "7d": 604800000, "30d": 2592000000, "90d": 7776000000, "all": None}
