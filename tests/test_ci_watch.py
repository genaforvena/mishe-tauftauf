import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from mishe_tauftauf import ci_watch
from mishe_tauftauf.feed import Feed


def test_failure_transition_addresses_genome_once_and_records_recovery():
    with TemporaryDirectory() as directory:
        home = Path(directory)
        failing = {"state": "fail", "sha": "a" * 40, "run": "42", "url": "https://example.test/42",
                   "detail": "completed/failure"}
        recovered = {"state": "pass", "sha": "b" * 40, "run": "43", "url": "https://example.test/43",
                     "detail": "completed/success"}
        with patch.object(ci_watch, "read", side_effect=[failing, failing, recovered]):
            ci_watch.tick(home)
            ci_watch.tick(home)
            ci_watch.tick(home)
        bodies = [entry.body for entry in Feed(home).entries()]
        assert len(bodies) == 3
        assert bodies[0].startswith("[ci] state=fail sha=" + "a" * 40)
        assert bodies[1].startswith("[dm] to=genome\nObserved CI failure")
        assert not any(body.startswith("[task]") for body in bodies)
        assert bodies[2].startswith("[ci] state=pass sha=" + "b" * 40)
        assert ci_watch.line(home).startswith("CI: PASS")


def test_queries_runs_for_exact_origin_commit_and_preserves_unavailable_state(tmp_path):
    sha = "a" * 40
    rows = [{"headSha": sha, "workflowName": "CI", "databaseId": 11, "status": "completed",
             "conclusion": "success", "url": "https://example.test/11"}]
    answers = [json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}}),
               sha, json.dumps(rows)]
    with patch.object(ci_watch, "_command", side_effect=answers) as command:
        result = ci_watch.read(tmp_path / "site")
    assert result["state"] == "pass"
    run_query = command.call_args_list[2].args
    assert run_query[:4] == ("gh", "run", "list", "--repo")
    assert run_query[4:6] == ("o/r", "--commit")
    assert run_query[6] == sha
    assert "--branch" not in run_query

    with patch.object(ci_watch, "_command", side_effect=[
            json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}}),
            sha, RuntimeError("GitHub unavailable")]):
        unavailable = ci_watch.read(tmp_path / "site")
    assert unavailable["state"] == "unknown"
    assert unavailable["detail"] == "GitHub unavailable"


def test_latest_run_per_workflow_supersedes_failed_attempt(tmp_path):
    sha = "a" * 40
    rows = [
        {"headSha": sha, "workflowName": "CI", "databaseId": 10, "status": "completed",
         "conclusion": "failure", "url": "https://example.test/10"},
        {"headSha": sha, "workflowName": "CI", "databaseId": 11, "status": "completed",
         "conclusion": "success", "url": "https://example.test/11"},
    ]
    answers = [json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}}),
               sha, json.dumps(rows)]
    with patch.object(ci_watch, "_command", side_effect=answers):
        assert ci_watch.read(tmp_path / "site")["state"] == "pass"
    rows.append({"headSha": sha, "workflowName": "Security", "databaseId": 12,
                 "status": "completed", "conclusion": "failure", "url": "https://example.test/12"})
    with patch.object(ci_watch, "_command", side_effect=[answers[0], sha, json.dumps(rows)]):
        result = ci_watch.read(tmp_path / "site")
        assert result["state"] == "fail"
        assert result["run"] == "12"


def test_candidate_ci_requires_named_push_workflow_and_exact_origin(tmp_path):
    sha = "a" * 40
    metadata = json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}})
    rows = [dict(headSha=sha, workflowName="CI", databaseId=10, status="completed",
                 conclusion="failure", url="https://example.test/10", event="push", headBranch="candidate"),
            dict(headSha=sha, workflowName="CI", databaseId=11, status="completed",
                 conclusion="success", url="https://example.test/11", event="pull_request", headBranch="candidate")]
    with patch.object(ci_watch, "_command", side_effect=[metadata, json.dumps(rows)]) as command:
        result = ci_watch.read(tmp_path / "site", workspace=tmp_path, sha=sha, branch="candidate",
                              repository="https://github.com/o/r.git", required_workflows=("CI",))
    assert result["state"] == "fail"
    assert command.call_args_list[0].args[3] == "https://github.com/o/r.git"
    assert "push" in command.call_args_list[1].args
    rows[0]["workflowName"] = "Other"
    rows[0]["conclusion"] = "success"
    with patch.object(ci_watch, "_command", side_effect=[metadata, json.dumps(rows)]):
        result = ci_watch.read(tmp_path / "site", workspace=tmp_path, sha=sha, branch="candidate",
                              required_workflows=("CI",))
    assert result["state"] == "unknown"


def test_tick_never_runs_obsolete_delivery_scheduler(tmp_path):
    from mishe_tauftauf import delivery
    result = dict(state="pass", sha="a" * 40, run="1", url="https://example.test/1", detail="completed/success")
    with patch.object(ci_watch, "read", return_value=result), patch.object(delivery, "check_all") as maintenance:
        ci_watch.tick(tmp_path, check_deliveries=False)
        maintenance.assert_not_called()
        assert ci_watch.latest(tmp_path)["state"] == "pass"
        assert len(Feed(tmp_path).entries()) == 1
        ci_watch.tick(tmp_path)
        maintenance.assert_not_called()
        assert len(Feed(tmp_path).entries()) == 1


def test_command_retries_one_transient_transport_failure(monkeypatch):
    attempts = []

    def flaky(argv, **kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            raise OSError("connection reset by peer")
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ci_watch.subprocess, "run", flaky)
    monkeypatch.setattr(ci_watch.time, "sleep", lambda _seconds: None)
    assert ci_watch._command("gh", "repo", "view") == "ok"
    assert len(attempts) == 2


def test_command_retries_nonzero_gh_transport_exit(monkeypatch):
    attempts = []

    def flaky(argv, **kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr='Post "https://api.github.com/graphql": EOF')
        return subprocess.CompletedProcess(argv, 0, stdout='{"nameWithOwner": "o/r"}\n', stderr="")

    monkeypatch.setattr(ci_watch.subprocess, "run", flaky)
    monkeypatch.setattr(ci_watch.time, "sleep", lambda _seconds: None)
    assert ci_watch._command("gh", "repo", "view") == '{"nameWithOwner": "o/r"}'
    assert len(attempts) == 2


def test_command_gives_up_after_bounded_attempts(monkeypatch):
    attempts = []

    def dead(argv, **kwargs):
        attempts.append(argv)
        raise OSError("network is unreachable")

    monkeypatch.setattr(ci_watch.subprocess, "run", dead)
    monkeypatch.setattr(ci_watch.time, "sleep", lambda _seconds: None)
    with pytest.raises(OSError):
        ci_watch._command("gh", "run", "list")
    assert len(attempts) == ci_watch._COMMAND_ATTEMPTS


def test_read_still_reports_unknown_when_transport_stays_down(tmp_path, monkeypatch):
    def dead(argv, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(ci_watch.subprocess, "run", dead)
    monkeypatch.setattr(ci_watch.time, "sleep", lambda _seconds: None)
    result = ci_watch.read(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["detail"] == "network is unreachable"
