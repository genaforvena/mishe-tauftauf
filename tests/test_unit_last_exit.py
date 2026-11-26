"""unit_last_exit reads the unit's latest exit even while a queued start is pending."""

from mishe_tauftauf import work_boundary


def test_queued_start_keeps_clean_exit_from_inactive_enter():
    # The live shape on 2026-11-09: activating, exit timestamp cleared, last exit kept by InactiveEnterTimestamp.
    values = {
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:32:40 UTC",
    }
    exited_at, clean = work_boundary.unit_last_exit("mishe-wake@genome.service", values)
    assert clean is True
    assert exited_at == 1794256360  # 2026-11-09T20:32:40Z


def test_exit_timestamp_wins_when_present():
    values = {
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "Mon 2026-11-09 20:31:53 UTC",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:32:40 UTC",
    }
    assert work_boundary.unit_last_exit("u", values) == (1794256313, True)


def test_failed_exit_is_not_clean():
    values = {
        "Result": "exit-code",
        "ExecMainStatus": "3",
        "ExecMainExitTimestamp": "",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:29:21 UTC",
    }
    _, clean = work_boundary.unit_last_exit("u", values)
    assert clean is False


def test_no_exit_time_reads_as_unknown(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("missing manager evidence cannot justify a journal fallback")

    monkeypatch.setattr(work_boundary.subprocess, "run", unexpected)
    assert work_boundary.unit_last_exit("u", {}) is None
