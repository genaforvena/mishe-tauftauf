from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf import seed
from mishe_tauftauf.feed import Feed, FeedEntry

NOW = datetime(2026, 9, 30, 6, 40, tzinfo=timezone.utc)


def entry(seq, body, age=180):
    return FeedEntry(seq, (NOW - timedelta(seconds=age)).isoformat(), "seed", body)


def test_stall_requires_an_overdue_settled_wake():
    wake = entry(1, "seed wake genome observation=1")
    settled = entry(2, "seed yield genome wake=1")
    assert seed.clear_stalls([wake, settled], now=NOW) == {"genome": settled}
    assert seed.clear_stalls([wake], now=NOW) == {}
    assert seed.clear_stalls([wake, entry(2, settled.body, age=119)], now=NOW) == {}
    assert seed.clear_stalls([wake, entry(2, settled.body, age=-10)], now=NOW) == {}
    assert seed.clear_stalls([wake, entry(2, "seed yield genome wake=99")], now=NOW) == {}
    assert seed.clear_stalls([wake, settled, entry(3, "seed clear genome after=1")], now=NOW) == {}
    assert seed.clear_stalls([wake, settled, entry(3, "seed wake genome observation=3")], now=NOW) == {}


def test_witness_observation_keeps_stall_identity_but_not_chat():
    frame = "WINDOWS: GREEN\nCLEAR STALL: RED genome wake=1 owner=health\nLATEST CHAT.LOG TEXT\nSTATE: RED\n"
    assert seed._observation_text("witness", frame) != seed._observation_text("witness", frame.replace("wake=1", "wake=2"))



def test_receipt_source_and_exact_clear_identity():
    from dataclasses import replace
    wake = entry(1, "seed wake genome observation=1")
    settled = entry(2, "seed yield genome wake=1")
    wrong = entry(3, "seed clear genome after=9")
    assert seed.clear_stalls([wake, settled, wrong], now=NOW) == {"genome": settled}
    assert seed.clear_stalls([wake, replace(settled, source="operator")], now=NOW) == {}
    assert seed.clear_stalls([wake, settled, replace(entry(4, "seed clear genome after=1"), source="operator")], now=NOW) == {"genome": settled}
    assert seed.clear_stalls([wake, entry(2, "seed yield genome wake=1 continue=1")], now=NOW)
    assert seed.clear_stalls([wake, entry(2, "seed yield genome wake=1", age=120)], now=NOW)


def test_stalls_remain_role_scoped_and_accept_full_readable_receipts():
    first = entry(1, "seed wake genome observation=1")
    second = entry(2, "seed wake health observation=2")
    settled = entry(3, "seed yield genome wake=1\nChecked handoff.")
    yield_health = entry(4, "seed yield health wake=2")
    assert seed.clear_stalls([first, second, settled, yield_health, entry(5, "seed clear genome after=1")], now=NOW) == {"health": yield_health}


def prepare_view(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import seed_witness_view as view
    home = tmp_path / "site"
    home.mkdir()
    (home / "health").mkdir()
    (home / "health/windows.json").write_text('["genome"]')
    monkeypatch.setattr(view, "ci_line", lambda home: "CI: PASS")
    monkeypatch.setattr(view, "_publication_lines", lambda home: ([], False))
    monkeypatch.setenv("MISHE_SEED_SESSION", "session")
    monkeypatch.setattr(view.subprocess, "run", lambda *a, **k: CompletedProcess(a, 0, "genome\n", ""))
    return home, view


def test_live_renderer_exposes_stall_and_recovers_without_writing_tasks(tmp_path, monkeypatch):
    home, view = prepare_view(tmp_path, monkeypatch)
    receipts = [entry(1, "seed wake genome observation=1", age=10000), entry(2, "seed yield genome wake=1", age=10000)]
    monkeypatch.setattr(view.Feed, "entries", lambda self: receipts)
    frame = view.render(home)
    assert "CLEAR STALL: RED genome wake=1 yield=2 owner=health" in frame
    assert "STATE: RED" in frame
    assert not (home / "chat.log").exists()
    assert not (home / "artifacts").exists()
    receipts.append(entry(3, "seed clear genome after=1", age=0))
    frame = view.render(home)
    assert "CLEAR STALL: GREEN" in frame
    assert "STATE: GREEN" in frame


def test_live_renderer_keeps_unreadable_feed_unknown(tmp_path, monkeypatch):
    home, view = prepare_view(tmp_path, monkeypatch)
    def unavailable(self):
        raise ValueError("framing unavailable")
    monkeypatch.setattr(view.Feed, "entries", unavailable)
    frame = view.render(home)
    assert "CLEAR STALL: UNKNOWN" in frame
    assert "STATE: UNKNOWN" in frame


def test_health_stall_routes_to_witness(tmp_path, monkeypatch):
    home, view = prepare_view(tmp_path, monkeypatch)
    receipts = [entry(1, "seed wake health observation=1", age=10000), entry(2, "seed yield health wake=1", age=10000)]
    monkeypatch.setattr(view.Feed, "entries", lambda self: receipts)
    frame = view.render(home)
    assert "CLEAR STALL: RED health wake=1 yield=2 owner=witness" in frame


def test_receipt_details_and_elapsed_time_do_not_rewake_witness():
    first = "WINDOWS: GREEN\nCLEAR STALL: RED genome wake=1 yield=2 owner=health\n  Evidence: clock sample A\nSTATE: RED\n"
    assert seed._observation_text("witness", first) == seed._observation_text("witness", first.replace("sample A", "sample B"))
