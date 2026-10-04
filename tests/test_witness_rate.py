from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed_witness_view import render


def test_witness_reports_discovery_chat_flood(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    feed = Feed(tmp_path)
    for index in range(8):
        feed.append("discover", f"[discovery] scan {index}\nThis scan repeated without a useful change.")
    view = render(tmp_path)
    assert "CHAT RATE: RED — discover: 8 entries" in view
    assert "Trace the wake or sampling feedback loop" in view

def test_witness_reports_witness_chat_flood_but_exempts_seed_bookkeeping(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    from datetime import datetime, timedelta, timezone
    import mishe_tauftauf.feed as feed_module
    import mishe_tauftauf.seed_witness_view as witness_view

    fixed_now = datetime.now(timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    timestamp = (fixed_now - timedelta(seconds=30)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    monkeypatch.setattr(feed_module, "utc_now", lambda: timestamp)
    monkeypatch.setattr(witness_view, "datetime", FixedDateTime)
    feed = Feed(tmp_path)
    for index in range(8):
        feed.append("witness", f"[audit] sample {index}\nRepeated witness work.")
    for index in range(12):
        feed.append("seed", f"seed observation witness-{index} sha256={index}")
    view = render(tmp_path)
    assert "CHAT RATE: RED — witness: 8 entries" in view, view
    assert "seed: 12 entries" not in view
    assert "seed: 12 observations" not in view
    assert "Trace the wake or sampling feedback loop" in view


def test_witness_falls_back_to_recorded_session_when_env_unset(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import mishe_tauftauf.seed_witness_view as view

    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    (tmp_path / ".seed-raised").write_text("recorded-session 42\n", encoding="utf-8")
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "windows.json").write_text('["witness"]', encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        view.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0, stdout="witness\n", stderr=""))

    output = view.render(tmp_path)

    assert "WINDOWS: GREEN" in output, output
    assert ["tmux", "list-windows", "-t", "recorded-session", "-F", "#{window_name}"] in calls, calls
