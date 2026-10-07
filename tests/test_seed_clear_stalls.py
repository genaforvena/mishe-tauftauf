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


def test_witness_dispatch_observes_stall_identity_and_chat_content():
    from mishe_tauftauf.wall import observation_text
    frame = "WINDOWS: GREEN\nCLEAR STALL: RED genome wake=1 owner=health\nLATEST CHAT.LOG TEXT\nSTATE: RED\n"
    baseline = observation_text("witness", frame)
    assert observation_text("witness", frame.replace("wake=1", "wake=2")) != baseline
    assert observation_text("witness", frame.replace("STATE: RED", "STATE: GREEN")) != baseline



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

def test_stale_pend_requires_an_overdue_pending_wake():
    wake = entry(1, "seed wake genome observation=1", age=601)
    assert seed.stale_pends([wake], now=NOW) == {"genome": wake}
    assert seed.stale_pends([entry(1, "seed wake genome observation=1", age=599)], now=NOW) == {}


def test_stale_pend_clears_on_matching_yield():
    wake = entry(1, "seed wake genome observation=1", age=1000)
    yield_ = entry(2, "seed yield genome wake=1", age=500)
    assert seed.stale_pends([wake, yield_], now=NOW) == {}


def test_stale_pend_ignores_mismatched_yield():
    wake = entry(1, "seed wake genome observation=1", age=1000)
    wrong_yield = entry(2, "seed yield genome wake=99", age=500)
    assert seed.stale_pends([wake, wrong_yield], now=NOW) == {"genome": wake}


def test_stale_pend_ignores_non_seed_source():
    from dataclasses import replace
    wake = replace(entry(1, "seed wake genome observation=1", age=1000), source="operator")
    assert seed.stale_pends([wake], now=NOW) == {}
    assert seed.stale_pends([replace(entry(1, "seed wake genome observation=1", age=1000), source="seed")], now=NOW) == {"genome": entry(1, "seed wake genome observation=1", age=1000)}


def test_stale_pends_remain_role_scoped():
    first = entry(1, "seed wake genome observation=1", age=1000)
    second = entry(2, "seed wake health observation=2", age=1000)
    assert seed.stale_pends([first, second], now=NOW) == {"genome": first, "health": second}


def test_stale_pend_replaces_on_new_wake():
    first = entry(1, "seed wake genome observation=1", age=1000)
    second = entry(2, "seed wake genome observation=2", age=601)
    assert seed.stale_pends([first, second], now=NOW) == {"genome": second}


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
    from mishe_tauftauf import seed
    home, view = prepare_view(tmp_path, monkeypatch)
    receipts = [entry(1, "seed wake genome observation=1", age=10000), entry(2, "seed yield genome wake=1", age=10000)]
    monkeypatch.setattr(view, "_mind_pane_live", lambda session, role: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda session, slug: True)
    monkeypatch.setattr(view.Feed, "entries", lambda self, **kwargs: receipts)
    frame = view.render(home)
    assert "CLEAR STALL: RED genome wake=1 yield=2 owner=health" in frame
    assert "STATE: RED" in frame
    assert not (home / "chat.log").exists()
    assert not (home / "artifacts").exists()

def test_live_renderer_exposes_stale_pend(tmp_path, monkeypatch):
    from mishe_tauftauf import seed
    home, view = prepare_view(tmp_path, monkeypatch)
    receipts = [entry(1, "seed wake genome observation=1", age=10000)]
    monkeypatch.setattr(view, "_mind_pane_live", lambda session, role: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda session, slug: True)
    monkeypatch.setattr(view.Feed, "entries", lambda self, **kwargs: receipts)
    frame = view.render(home)
    assert "STALE PEND: RED genome wake=1 pending=1 owner=health" in frame
    assert "STATE: RED" in frame
    receipts.append(entry(2, "seed yield genome wake=1", age=0))
    receipts.append(entry(3, "seed clear genome after=1", age=0))
    frame = view.render(home)
    assert "STALE PEND: GREEN" in frame
    assert "STATE: GREEN" in frame


def test_live_renderer_keeps_unreadable_feed_unknown(tmp_path, monkeypatch):
    home, view = prepare_view(tmp_path, monkeypatch)
    def unavailable(self, **kwargs):
        raise ValueError("framing unavailable")
    monkeypatch.setattr(view.Feed, "entries", unavailable)
    frame = view.render(home)
    assert "CLEAR STALL: UNKNOWN" in frame
    assert "STATE: UNKNOWN" in frame


def test_health_stall_routes_to_witness(tmp_path, monkeypatch):
    from mishe_tauftauf import seed
    home, view = prepare_view(tmp_path, monkeypatch)
    receipts = [entry(1, "seed wake health observation=1", age=10000), entry(2, "seed yield health wake=1", age=10000)]
    monkeypatch.setattr(view, "_mind_pane_live", lambda session, role: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda session, slug: True)
    monkeypatch.setattr(view.Feed, "entries", lambda self, **kwargs: receipts)
    frame = view.render(home)
    assert "CLEAR STALL: RED health wake=1 yield=2 owner=witness" in frame


def test_receipt_detail_updates_witness_full_frame_observation():
    from mishe_tauftauf.wall import observation_text
    first = "WINDOWS: GREEN\nCLEAR STALL: RED genome wake=1 yield=2 owner=health\n  Evidence: clock sample A\nSTATE: RED\n"
    assert observation_text("witness", first) != observation_text("witness", first.replace("sample A", "sample B"))
