from mishe_tauftauf import commit_risk_outcomes as cro
from mishe_tauftauf import commit_risk_text as crt

H = 3600
NOW = 1_000_000 * H


def _commit(sha, ct, subject, paths):
    return {"sha": sha, "ct": ct, "subject": subject, "paths": set(paths)}


def test_parse_git_log_reads_subject_and_paths():
    text = (
        "COMMIT\x00aaa\x00100\x00fix: thing\n"
        "src/a.py\n"
        "\n"
        "COMMIT\x00bbb\x00200\x00docs\n"
        "README.md\n"
    )
    commits = crt.parse_git_log(text)
    assert [c["sha"] for c in commits] == ["aaa", "bbb"]
    assert commits[0]["paths"] == {"src/a.py"}
    assert commits[1]["subject"] == "docs"


def test_row_without_commit_is_missing_not_clean():
    results = cro.follow_ups([{"sha": "zzz", "at": NOW}], [], NOW)
    assert results[0]["status"] == "missing"
    state, _ = cro.summary(results)
    assert state == "UNKNOWN"


def test_young_commit_is_pending_and_keeps_state_unknown():
    commits = [_commit("a", NOW - 2 * H, "feat", ["src/x.py"])]
    results = cro.follow_ups([{"sha": "a"}], commits, NOW)
    assert results[0]["status"] == "pending"
    assert cro.summary(results)[0] == "UNKNOWN"


def test_aged_commit_with_no_fix_is_clean():
    commits = [_commit("a", NOW - 100 * H, "feat", ["src/x.py"])]
    results = cro.follow_ups([{"sha": "a"}], commits, NOW)
    assert results[0]["status"] == "clean"
    state, line = cro.summary(results)
    assert state == "MEASURED"
    assert "0 of 1" in line


def test_fix_on_same_path_within_window_marks_followed():
    commits = [
        _commit("a", NOW - 100 * H, "feat", ["src/x.py"]),
        _commit("f", NOW - 100 * H + 10 * H, "fix: x", ["src/x.py"]),
    ]
    results = cro.follow_ups([{"sha": "a"}], commits, NOW)
    assert results[0]["status"] == "followed"
    assert results[0]["fix"] == "f"


def test_fix_on_other_path_or_outside_window_does_not_count():
    commits = [
        _commit("a", NOW - 200 * H, "feat", ["src/x.py"]),
        _commit("late", NOW - 200 * H + 100 * H, "fix: late", ["src/x.py"]),
        _commit("other", NOW - 200 * H + 10 * H, "fix: other", ["src/y.py"]),
    ]
    results = cro.follow_ups([{"sha": "a"}], commits, NOW)
    assert results[0]["status"] == "clean"


def test_revert_counts_as_follow_up_and_mixed_rows_report_both():
    commits = [
        _commit("a", NOW - 200 * H, "feat", ["src/x.py"]),
        _commit("b", NOW - 200 * H, "feat2", ["src/z.py"]),
        _commit("r", NOW - 200 * H + 5 * H, 'Revert "feat"', ["src/x.py"]),
    ]
    results = cro.follow_ups([{"sha": "a"}, {"sha": "b"}], commits, NOW)
    assert [r["status"] for r in results] == ["followed", "clean"]
    state, line = cro.summary(results)
    assert state == "MEASURED"
    assert "1 of 2" in line


def test_missing_row_beside_measured_rows_stays_unknown():
    commits = [_commit("a", NOW - 200 * H, "feat", ["src/x.py"])]
    results = cro.follow_ups([{"sha": "a"}, {"sha": "gone"}], commits, NOW)
    assert cro.summary(results)[0] == "UNKNOWN"


def test_merge_without_paths_is_unscored_not_clean():
    commits = [_commit("m", NOW - 200 * H, "merge: x", [])]
    results = cro.follow_ups([{"sha": "m"}], commits, NOW)
    assert results[0]["status"] == "unscored"
    assert cro.summary(results)[0] == "UNKNOWN"
