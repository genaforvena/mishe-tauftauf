"""Disposition records on the head-rewinds reading (sense.repo.head-rewinds).

Real git fixtures, no mocks. A commit that edits a file is dropped by a reset,
and HEAD then carries a different edit, so the dropped path reads needs-review.
A record keyed on (commit, path, blob) moves that path to dispositioned.

Prediction checked here: an exact record moves the path from needs-review to
dispositioned; a record whose blob, filename or fields do not match leaves it
needs-review and is counted as malformed when it is malformed.
"""

import hashlib
import json
import os
import subprocess

import pytest

from mishe_tauftauf import discovery, head_rewind_dispositions

PATH = "doc.md"


def _git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def rewound(tmp_path):
    """HEAD edits doc.md one way; the dropped commit edited it another way."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / PATH).write_text("one\n")
    _git(repo, "add", PATH)
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / PATH).write_text("two\n")
    _git(repo, "commit", "-q", "-am", "dropped edit")
    dropped = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", base)
    (repo / PATH).write_text("three\n")
    _git(repo, "commit", "-q", "-am", "replacement edit")
    site = repo / "site"
    site.mkdir()
    return {"site": site, "dropped": dropped,
            "blob": _git(repo, "rev-parse", f"{dropped}:{PATH}")}


def _write(site, commit, path, blob, **overrides):
    record = {"commit": commit, "path": path, "blob": blob,
              "verdict": "superseded-by-revision", "reason": "fixture reason",
              "author": "fixture", "wake": 1, "head_checked": "0" * 40}
    record.update(overrides)
    directory = site / "dispositions" / "head-rewinds"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{commit[:12]}-{hashlib.sha256(path.encode()).hexdigest()[:16]}.json"
    (directory / name).write_text(json.dumps(record))
    return directory / name


def test_unrecorded_rewind_stays_needs_review(rewound):
    result = discovery._head_rewinds(rewound["site"])

    assert result["state"] == "drift"
    assert result["needs_review"] == 1
    assert result["dispositioned"] == 0
    assert "dispositioned-paths" not in result["sample"]


def test_exact_record_moves_path_to_dispositioned(rewound):
    _write(rewound["site"], rewound["dropped"], PATH, rewound["blob"])

    result = discovery._head_rewinds(rewound["site"])

    assert result["needs_review"] == 0
    assert result["dispositioned"] == 1
    assert result["dispositioned_paths"] == [{"commit": rewound["dropped"][:12], "path": PATH}]
    assert "dispositioned-paths=1 disposition-records-malformed=0" in result["sample"]
    # A record explains the path; it does not clear the rewind.
    assert result["state"] == "drift"


def test_record_with_other_blob_does_not_match(rewound):
    _write(rewound["site"], rewound["dropped"], PATH, "a" * 40)

    result = discovery._head_rewinds(rewound["site"])

    assert result["needs_review"] == 1
    assert result["dispositioned"] == 0


@pytest.mark.parametrize("overrides", [
    {"verdict": "looks-fine"},
    {"reason": ""},
    {"wake": "148894"},
    {"blob": "not-a-blob"},
])
def test_malformed_record_stays_needs_review(rewound, overrides):
    fields = dict(overrides)
    blob = fields.pop("blob", rewound["blob"])
    _write(rewound["site"], rewound["dropped"], PATH, blob, **fields)

    result = discovery._head_rewinds(rewound["site"])

    assert result["needs_review"] == 1
    assert result["dispositioned"] == 0
    assert "disposition-records-malformed=1" in result["sample"]


def test_record_under_wrong_filename_is_malformed(rewound):
    directory = rewound["site"] / "dispositions" / "head-rewinds"
    directory.mkdir(parents=True)
    record = {"commit": rewound["dropped"], "path": PATH, "blob": rewound["blob"],
              "verdict": "superseded-by-revision", "reason": "r", "author": "a",
              "wake": 1, "head_checked": "0" * 40}
    (directory / "copied-by-hand.json").write_text(json.dumps(record))

    result = discovery._head_rewinds(rewound["site"])

    assert result["needs_review"] == 1
    assert result["disposition_records_malformed"] == 1


def test_non_json_record_is_malformed(rewound):
    directory = rewound["site"] / "dispositions" / "head-rewinds"
    directory.mkdir(parents=True)
    name = f"{rewound['dropped'][:12]}-{hashlib.sha256(PATH.encode()).hexdigest()[:16]}.json"
    (directory / name).write_text("{not json")

    result = discovery._head_rewinds(rewound["site"])

    assert result["needs_review"] == 1
    assert result["disposition_records_malformed"] == 1


def test_absent_record_directory_is_zero_records(tmp_path):
    assert head_rewind_dispositions.load(tmp_path / "absent") == ({}, 0)
