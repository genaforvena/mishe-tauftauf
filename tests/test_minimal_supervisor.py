import sys

import pytest

from mishe_tauftauf.minimal_supervisor import MinimalSupervisor, SupervisorError


def test_one_wake_is_durable_and_duplicate_is_refused(tmp_path):
    s = MinimalSupervisor(tmp_path, role="trial", timeout=2)
    done = s.dispatch([sys.executable, "-c", "print('useful-read-only-result')"])
    assert done.state == "completed"
    assert done.wake == 1
    assert s.status().wake == 1
    assert s.dispatch([sys.executable, "-c", "raise SystemExit(3)"]).state == "failed"


def test_timeout_is_unknown_and_requires_reconciliation(tmp_path):
    s = MinimalSupervisor(tmp_path, role="trial", timeout=.05)
    unknown = s.dispatch([sys.executable, "-c", "import time; time.sleep(5)"])
    assert unknown.state == "unknown"
    with pytest.raises(SupervisorError):
        s.dispatch([sys.executable, "-c", "print('no replay')"])
    assert s.reconcile(detail="isolated fixture had no external effect").state == "reconciled"


def test_invalid_role_and_empty_command_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        MinimalSupervisor(tmp_path, role="bad role")
    s = MinimalSupervisor(tmp_path, role="trial")
    with pytest.raises(ValueError):
        s.dispatch([])
