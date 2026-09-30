from argparse import Namespace
from datetime import datetime, timedelta, timezone
import pytest

from mishe_tauftauf import cli, dashboard


def test_watcher_and_mind_share_complete_frame_without_terminal_clipping(tmp_path, monkeypatch, capsys):
    from mishe_tauftauf.observations import RenderedPain
    body = "LANDING DEBT: RED outside draft\n" + "detail\n" * 80
    monkeypatch.setattr(cli, "compose_frame", lambda *a: RenderedPain("genome", body, True))
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.cmd_pain_watch(Namespace(home=tmp_path, slug="genome", timeout=1, interval=5)) == 0
    displayed = capsys.readouterr().out.removeprefix("\x1b[H\x1b[2J")
    assert dashboard.read(tmp_path, "genome")[0] == displayed
    assert "LANDING DEBT: RED" in displayed
    assert cli.cmd_pain_read(Namespace(home=tmp_path, slug="genome", launcher="dashboard")) == 0
    assert capsys.readouterr().out == displayed


def test_missing_stale_future_and_corrupt_dashboard_fail_closed(tmp_path):
    clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="missing"):
        dashboard.read(tmp_path, "genome", now=clock)
    dashboard.publish(tmp_path, "genome", "full frame", True, now=clock)
    assert dashboard.read(tmp_path, "genome", now=clock)[0] == "full frame"
    for at in (clock + timedelta(seconds=31), clock - timedelta(seconds=1)):
        with pytest.raises(ValueError, match="stale or future"):
            dashboard.read(tmp_path, "genome", now=at)
    path = tmp_path / "dashboards/genome.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        dashboard.read(tmp_path, "genome", now=clock)


def test_supervisor_observes_dashboard_instead_of_changing_terminal_history(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed, utc_now
    frame = "STATE: GREEN\nLANDING DEBT: RED outside draft\n"
    dashboard.publish(tmp_path, "genome", frame, True)
    monkeypatch.setattr(seed, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_pane_stopped_or_dead", lambda *a: False)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: False)
    monkeypatch.setattr(seed, "_tmux", lambda *a, **kw: CompletedProcess(a, 0, b"0\n"))
    pane = ["old scrolled frame\n"]
    monkeypatch.setattr(seed, "capture_raw", lambda *a: pane[0] + "-- pane live " + utc_now() +
                        " · refresh 5s · ticks every frame --\n")
    seed.tick(tmp_path, "session", "genome")
    pane[0] = "different wrapped history\n" * 100
    seed.tick(tmp_path, "session", "genome")
    observations = [e for e in Feed(tmp_path).entries() if e.body.startswith("seed observation")]
    assert len(observations) == 1
    assert "STATE: GREEN" in observations[0].body
    (tmp_path / "dashboards/genome.json").unlink()
    assert "dashboard missing" in seed.tick(tmp_path, "session", "genome")


def test_post_render_feed_failure_publishes_failed_snapshot(tmp_path, monkeypatch, capsys):
    from mishe_tauftauf.observations import RenderedPain
    monkeypatch.setattr(cli, "compose_frame", lambda *a: RenderedPain("genome", "STATE: GREEN\n", True))
    monkeypatch.setattr(cli.Feed, "entries", lambda *a: (_ for _ in ()).throw(cli.FeedError("bad feed")))
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.cmd_pain_watch(Namespace(home=tmp_path, slug="genome", timeout=1, interval=5)) == 0
    capsys.readouterr()
    frame, ok = dashboard.read(tmp_path, "genome")
    assert "STATE: RED" in frame
    assert not ok
    assert cli.cmd_pain_read(Namespace(home=tmp_path, slug="genome", launcher="dashboard")) == 1
