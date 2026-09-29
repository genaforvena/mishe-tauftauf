from __future__ import annotations

from argparse import Namespace

from mishe_tauftauf import cli


def test_pain_watcher_keeps_red_pane_alive_on_malformed_feed(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "chat.log").write_text("unframed task line\n", encoding="utf-8")
    ticks = 0

    def sleep(_interval: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    args = Namespace(home=tmp_path, slug="health", timeout=1, interval=0.01)
    assert cli.cmd_pain_watch(args) == 0
    output = capsys.readouterr().out
    assert output.count("STATE: RED — chat feed is malformed") == 2
    assert output.count("FEED ERROR: malformed header at line 1") == 2
    assert output.count("-- pane live ") == 2
