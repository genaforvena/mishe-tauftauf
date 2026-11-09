"""unit_last_exit reads the unit's latest exit even while a queued start is pending."""
import subprocess

from mishe_tauftauf import work_boundary


def fake_systemctl(monkeypatch, properties: dict):
    text = "".join(f"{key}={value}\n" for key, value in properties.items())

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")

    monkeypatch.setattr(work_boundary.subprocess, "run", run)


def test_queued_start_keeps_clean_exit_from_inactive_enter(monkeypatch):
    # The live shape on 2026-11-09: activating, exit timestamp cleared, last exit kept by InactiveEnterTimestamp.
    fake_systemctl(monkeypatch, {
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:32:40 UTC",
    })
    exited_at, clean = work_boundary.unit_last_exit("mishe-wake@genome.service")
    assert clean is True
    assert exited_at == 1794256360  # 2026-11-09T20:32:40Z


def test_exit_timestamp_wins_when_present(monkeypatch):
    fake_systemctl(monkeypatch, {
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "Mon 2026-11-09 20:31:53 UTC",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:31:53 UTC",
    })
    assert work_boundary.unit_last_exit("u") == (1794256313, True)


def test_failed_exit_is_not_clean(monkeypatch):
    fake_systemctl(monkeypatch, {
        "Result": "exit-code",
        "ExecMainStatus": "3",
        "ExecMainExitTimestamp": "",
        "InactiveEnterTimestamp": "Mon 2026-11-09 20:29:21 UTC",
    })
    _, clean = work_boundary.unit_last_exit("u")
    assert clean is False


def test_no_exit_time_reads_as_unknown(monkeypatch):
    fake_systemctl(monkeypatch, {
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "",
        "InactiveEnterTimestamp": "",
    })
    assert work_boundary.unit_last_exit("u") is None
