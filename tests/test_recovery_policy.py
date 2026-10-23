from mishe_tauftauf import recovery_policy as policy


def advance(previous=None, *, now=0, result=False, mitigation=False):
    return policy.transition(previous, name="control", result=result, now=now,
                             owner="health", backup="genome", deadline=60,
                             stable_seconds=20, mitigation=mitigation)


def test_diagnosis_and_mitigation_do_not_close_functional_failure():
    failed = advance()
    assert failed["state"] == "FAILED"
    failed["diagnosis_receipt"] = "hypothesis-changed"
    mitigated = advance(failed, now=10, mitigation=True)
    assert mitigated["state"] == "MITIGATED"
    assert mitigated["first_failed"] == 0
    assert mitigated["restored_at"] is None


def test_restoration_needs_sustained_fresh_function_checks():
    row = advance()
    row = advance(row, now=5, result=True)
    assert row["state"] == "RECOVERING"
    row = advance(row, now=10, result=None)
    assert row["state"] == "UNKNOWN"
    row = advance(row, now=30, result=True)
    assert row["state"] == "RECOVERING"
    row = advance(row, now=50, result=True)
    assert row["state"] == "RESTORED"
    assert row["restored_at"] == 50


def test_recurrence_gets_a_new_episode_and_keeps_prior_resolution():
    row = advance()
    row = advance(row, now=5, result=True)
    row = advance(row, now=25, result=True)
    again = advance(row, now=30)
    assert again["episode"] == 2
    assert again["previous_restored_at"] == 25
    assert again["first_failed"] == 30


def test_overdue_failure_requests_backup_but_does_not_invent_acceptance():
    row = advance()
    row = advance(row, now=61)
    assert row["resolver"] == "health"
    assert row["backup_requested"] == "genome"
    assert row["backup_accepted_at"] is None
    row = policy.accept(row, owner="genome", now=62)
    assert row["resolver"] == "genome"
    assert row["backup_accepted_at"] == 62


def test_missing_evidence_never_initializes_as_healthy():
    assert advance(result=None)["state"] == "UNKNOWN"


def test_pressure_hysteresis_keeps_capacity_reduced_until_stable():
    row = policy.capacity(None, memory_full=40, io_full=60, available_ratio=.04,
                          now=0, recover_seconds=60)
    assert row["limited"] is True
    row = policy.capacity(row, memory_full=0, io_full=0, available_ratio=.5,
                          now=5, recover_seconds=60)
    assert row["limited"] is True
    row = policy.capacity(row, memory_full=0, io_full=0, available_ratio=.5,
                          now=65, recover_seconds=60)
    assert row["limited"] is False


def test_missing_pressure_retains_restriction_and_stays_unknown():
    row = policy.capacity(None, memory_full=None, io_full=None,
                          available_ratio=None, now=0, recover_seconds=60)
    assert row["state"] == "UNKNOWN"
    assert row["limited"] is True


def test_report_cannot_claim_help_from_pressure_only_when_function_fails():
    before = [{"memory_full": 40, "io_full": 60, "control_ok": False}]
    after = [{"memory_full": 1, "io_full": 2, "control_ok": False}]
    report = policy.compare(before, after)
    assert report["functional_improvement"] is False
    assert report["verdict"] != "IMPROVED"
    assert policy.compare([], after)["verdict"] == "UNKNOWN"


def test_report_separates_no_baseline_successes_from_missing_samples():
    before = [{"memory_full": 40, "io_full": 60, "control_ok": False}]
    after = [{"memory_full": 1, "io_full": 2, "control_ok": True}]
    report = policy.compare(before, after)
    assert report["verdict"] == "IMPROVED"
    assert report["causation"] == "uncontrolled before/after; attribution unproven"
