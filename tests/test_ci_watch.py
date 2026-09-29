import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mishe_tauftauf import ci_watch
from mishe_tauftauf.feed import Feed


def test_failure_transition_opens_one_task_and_records_recovery():
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
        assert bodies[1].startswith("[task] ci-aaaaaaaaaaaa owner=genome")
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
