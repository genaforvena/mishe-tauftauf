"""The gap-remedy detector must decide each broken row's remedy from published fields.

``checks/coverage-gap-remedy.py`` answers whether every broken coverage row
the plant publishes has a remedy the plant's own naming rule recognises, and
whether the published reading can apply that rule. A broken row is one whose
bound evidence file no longer holds the bytes the outcome published.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a broken row with no later same-role remedy successor reads UNREMEDIED and
  names the path to publish, and says whether the bytes are still on disk;
* a broken row whose file is gone says so, because a copy of nothing is not
  evidence;
* a later same-role row that names the lost artifact's stem plus a remedy
  suffix, in the same directory, with intact bytes, re-evidences the row;
* a successor published by another role, published earlier, published in
  another directory, or published with broken bytes does **not** re-evidence
  the row;
* a re-evidenced row that the published reading still reports broken reads
  BEHIND, because the running reader cannot apply the plant's own rule;
* a re-evidenced row the published reading resolves reads clean;
* rows whose bytes are intact are not rows of interest;
* a missing, unreadable or stale coverage report reads UNKNOWN, never green,
  and a malformed one never raises.

The detector reads the published coverage report and the bound evidence files
and imports nothing from the plant package, so a change to an internal
helper's signature cannot make it fail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "coverage-gap-remedy.py"


def run_check(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home), *extra],
                          capture_output=True, text=True)


def stamp(offset_seconds: float = 0.0) -> str:
    """An ISO stamp, ``offset_seconds`` in the past."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_artifact(home: Path, name: str, body: str = "artifact\n") -> str:
    """Write an artifact under the site's artifacts directory; return its path."""
    directory = home / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def item(home: Path, sequence: int, role: str, path: str, digest: str,
         *, age: float = 0.0, state: str = "OBSERVED",
         link_kind: str | None = None) -> dict:
    """One published outcome row."""
    row = {"sequence": sequence, "role": role, "timestamp": stamp(age),
           "state": state, "evidence": {"path": path, "sha256": digest}}
    if link_kind:
        row["link_kind"] = link_kind
    return row


def write_report(home: Path, items: list[dict], *, age: float = 0.0,
                 raw: str | None = None) -> None:
    """Publish a coverage report carrying the given outcome rows."""
    directory = home / "checks"
    directory.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (directory / "initiative.json").write_text(raw, encoding="utf-8")
        return
    payload = {"generated_at": stamp(age), "outcomes": {"state": "OBSERVED",
                                                       "items": items}}
    (directory / "initiative.json").write_text(json.dumps(payload), encoding="utf-8")


def digest_of(body: str) -> str:
    """The sha256 of a body, as the reader would record it."""
    import hashlib
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_intact_rows_are_not_rows_of_interest(tmp_path: Path) -> None:
    body = "intact\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", body)
    write_report(tmp_path, [item(tmp_path, 10, "role", path, digest_of(body))])
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout


def test_broken_row_without_remedy_is_unremedied(tmp_path: Path) -> None:
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    write_report(tmp_path, [item(tmp_path, 10, "role", path, digest_of("bound\n"),
                                 state="UNKNOWN")])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNREMEDIED" in result.stdout
    assert "still on disk, copyable" in result.stdout
    assert "role-wake1-report-final.md" in result.stdout


def test_broken_row_whose_artifact_is_gone_says_so(tmp_path: Path) -> None:
    path = str(tmp_path / "artifacts" / "role-wake1-report.md")
    write_report(tmp_path, [item(tmp_path, 10, "role", path, digest_of("bound\n"),
                                 state="UNKNOWN")])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "gone" in result.stdout


def test_later_same_role_successor_re_evidences_the_row(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor_body = "edited\n"
    successor = write_artifact(tmp_path, "role-wake1-report-final.md", successor_body)
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="UNKNOWN"),
        item(tmp_path, 20, "role", successor, digest_of(successor_body), age=300),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BEHIND" in result.stdout
    assert "re-evidences" not in result.stdout.split("BEHIND")[0]


def test_successor_published_by_another_role_is_no_remedy(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor_body = "edited\n"
    successor = write_artifact(tmp_path, "role-wake1-report-final.md", successor_body)
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="UNKNOWN"),
        item(tmp_path, 20, "other", successor, digest_of(successor_body), age=300),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNREMEDIED" in result.stdout
    assert "BEHIND" not in result.stdout


def test_earlier_successor_is_no_remedy(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor_body = "edited\n"
    successor = write_artifact(tmp_path, "role-wake1-report-final.md", successor_body)
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=300, state="UNKNOWN"),
        item(tmp_path, 20, "role", successor, digest_of(successor_body), age=600),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNREMEDIED" in result.stdout


def test_successor_in_another_directory_is_no_remedy(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor_body = "edited\n"
    other = tmp_path / "dms"
    other.mkdir(parents=True, exist_ok=True)
    successor = other / "role-wake1-report-final.md"
    successor.write_text(successor_body, encoding="utf-8")
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="UNKNOWN"),
        item(tmp_path, 20, "role", str(successor), digest_of(successor_body), age=300),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNREMEDIED" in result.stdout


def test_successor_with_broken_bytes_is_no_remedy(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor = write_artifact(tmp_path, "role-wake1-report-final.md", "edited again\n")
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="UNKNOWN"),
        item(tmp_path, 20, "role", successor, digest_of("edited\n"), age=300,
             state="UNKNOWN"),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNREMEDIED" in result.stdout


def test_resolved_re_evidenced_row_reads_clean(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    successor_body = "edited\n"
    successor = write_artifact(tmp_path, "role-wake1-report-final.md", successor_body)
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="OBSERVED",
             link_kind="remedy-name"),
        item(tmp_path, 20, "role", successor, digest_of(successor_body), age=300),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout


def test_proposed_remedy_path_avoids_a_taken_name(tmp_path: Path) -> None:
    bound = "bound\n"
    path = write_artifact(tmp_path, "role-wake1-report.md", "edited\n")
    # The name is taken on disk but no published row binds it, so it is not a
    # remedy successor and the proposal must not overwrite it either.
    write_artifact(tmp_path, "role-wake1-report-final.md", "taken\n")
    write_report(tmp_path, [
        item(tmp_path, 10, "role", path, digest_of(bound), age=600, state="UNKNOWN"),
    ])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "role-wake1-report-v2.md" in result.stdout


def test_missing_report_reads_unknown(tmp_path: Path) -> None:
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_stale_report_reads_unknown(tmp_path: Path) -> None:
    write_report(tmp_path, [], age=100000)
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "stale" in result.stdout


def test_malformed_report_reads_unknown_without_a_traceback(tmp_path: Path) -> None:
    write_report(tmp_path, [], raw="{not json")
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
    assert "Traceback" not in result.stderr


def test_report_without_outcome_rows_reads_unknown(tmp_path: Path) -> None:
    write_report(tmp_path, [], raw=json.dumps({"generated_at": stamp()}))
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
