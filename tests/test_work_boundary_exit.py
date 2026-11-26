"""unit_last_exit falls back to the unit's journal when systemd has forgotten its exit timestamps."""
import subprocess

from mishe_tauftauf import work_boundary

UNIT = "mishe-wake@field-review.service"
# A garbage-collected inactive unit has no manager exit timestamp.
BLANK_EXIT = {"Result": "success", "ExecMainStatus": "0",
              "ExecMainExitTimestamp": "", "InactiveEnterTimestamp": ""}


def _patch(monkeypatch, journal_stdout):
    def fake_run(argv, **kwargs):
        assert argv[0] == "journalctl"
        return subprocess.CompletedProcess(argv, 0, stdout=journal_stdout)
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_blank_systemd_timestamps_read_clean_finish_from_journal(monkeypatch):
    _patch(monkeypatch,
           "1794497544.575783 mesh-home systemd[1154]: mishe-wake@field-review.service: Failed with result 'exit-code'.\n"
           "1794501864.174608 mesh-home systemd[1154]: Finished mishe-wake@field-review.service - Mishe wake field-review.\n")
    assert work_boundary.unit_last_exit(UNIT, BLANK_EXIT) == (1794501864.174608, True)


def test_latest_journal_exit_being_a_failure_stays_unclean(monkeypatch):
    _patch(monkeypatch,
           "1794501864.174608 mesh-home systemd[1154]: Finished mishe-wake@field-review.service - Mishe wake field-review.\n"
           "1794505000.000000 mesh-home systemd[1154]: mishe-wake@field-review.service: Failed with result 'exit-code'.\n")
    assert work_boundary.unit_last_exit(UNIT, BLANK_EXIT) == (1794505000.0, False)


def test_no_exit_event_in_journal_stays_unknown(monkeypatch):
    _patch(monkeypatch, "")
    assert work_boundary.unit_last_exit(UNIT, BLANK_EXIT) is None
