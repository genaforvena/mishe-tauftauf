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


def test_witness_marks_chat_rate_unknown_when_canonical_feed_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    (tmp_path / "chat.log").mkdir()

    view = render(tmp_path)

    assert "CHAT RATE: UNKNOWN — canonical chat input unavailable" in view, view
    assert "STATE: UNKNOWN" in view, view
    assert (tmp_path / "observations" / "witness").read_text(encoding="utf-8").strip() == (
        "UNKNOWN witness coordination evidence invalid")

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


def _repeated_body_view(tmp_path, monkeypatch, *, age_seconds, count=3, source="operator",
                        body="[dm] to=health Settlement-integrity FLAG: research-methods wake=61976"):
    """Render with `count` identical non-seed bodies `age_seconds` old."""
    from datetime import datetime, timedelta, timezone
    import mishe_tauftauf.feed as feed_module
    import mishe_tauftauf.seed_witness_view as witness_view

    fixed_now = datetime.now(timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    timestamp = (fixed_now - timedelta(seconds=age_seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    monkeypatch.setattr(feed_module, "utc_now", lambda: timestamp)
    monkeypatch.setattr(witness_view, "datetime", FixedDateTime)
    feed = Feed(tmp_path)
    for _ in range(count):
        feed.append(source, body)
    return render(tmp_path)


def test_witness_reports_a_repeated_body_the_rate_line_cannot_see(tmp_path, monkeypatch):
    """The settlement-integrity router ran at 3.4/min under a green rate line."""
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)

    view = _repeated_body_view(tmp_path, monkeypatch, age_seconds=30)

    assert "CHAT RATE: GREEN" in view, view
    assert "REPEAT: RED — operator sent one body 3 times, newest 30s ago:" in view, view
    assert "wake=61976" in view, view
    assert "Stop the repeated sender and fix its cause." in view, view
    assert (tmp_path / "observations" / "witness").read_text(encoding="utf-8").strip() == (
        "FAIL witness repeated event loop")


def test_witness_repeat_clears_once_the_repetition_stops(tmp_path, monkeypatch):
    """A stopped loop is history, not a standing fault: the pane must go quiet."""
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)

    view = _repeated_body_view(tmp_path, monkeypatch, age_seconds=400)

    assert "REPEAT: GREEN — no identical non-seed body 3 or more times in the last 30 minutes" in view, view
    assert "repeated event loop" not in (tmp_path / "observations" / "witness").read_text(encoding="utf-8")


def test_witness_repeat_ignores_seed_bookkeeping(tmp_path, monkeypatch):
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
    for _ in range(12):
        feed.append("seed", "seed observation witness-1 sha256=abc")

    view = render(tmp_path)

    assert "REPEAT: GREEN" in view, view
