"""Controlled-failure tests for the post-move observation of the live consumers (pin_observation)."""
import pytest

from mishe_tauftauf import pin_observation as po


def _site(tmp_path, *, point_at=None):
    home = tmp_path / "site"
    release = home / "releases" / ("a" * 40)
    (release / "src").mkdir(parents=True)
    (home / "runtime").mkdir()
    (home / "runtime" / "current").symlink_to(point_at or release)
    return home, release


def test_pointer_must_name_the_release_the_probe_runs_from(tmp_path):
    home, release = _site(tmp_path)
    assert po.check_pointer(home, release) == home / "runtime" / "current"
    other = home / "releases" / ("b" * 40)
    other.mkdir()
    with pytest.raises(ValueError, match="does not name this release"):
        po.check_pointer(home, other)


def test_missing_pointer_is_an_error_not_a_pass(tmp_path):
    home = tmp_path / "site"
    (home / "runtime").mkdir(parents=True)
    release = home / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    with pytest.raises(ValueError):
        po.check_pointer(home, release)


def test_wrappers_must_name_the_stable_pointer_and_no_sha(tmp_path):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    (home / "bin").mkdir()
    for name in ("mishe", "mishe-tauftauf"):
        (home / "bin" / name).write_text(f"exec {stable}/bin/x\n")
    po.check_wrappers(home, stable, release.name)
    (home / "bin" / "mishe").write_text(f"exec {release}/bin/x {release.name}\n")
    with pytest.raises(ValueError, match="stable pointer alone"):
        po.check_wrappers(home, stable, release.name)


def test_drain_waits_for_old_invocations_and_fails_closed_when_they_stay(tmp_path):
    calls = []

    def pending(record, rows):
        calls.append(record["cutoff_us"])
        return [{"unit": "mishe-x.service", "invocation": "old", "start_us": 1, "pids": [1]}]

    with pytest.raises(ValueError, match="old consumer invocations remain"):
        po.drain_old(tmp_path, 5, timeout=0.0, sleep=lambda s: None, inventory=lambda h: {}, pending_old=pending)
    assert calls == [5]


def test_drain_returns_when_nothing_old_remains_and_ignores_its_own_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "self")
    rows = iter([[{"unit": "u", "invocation": "self"}], []])
    assert po.drain_old(tmp_path, 5, timeout=5, sleep=lambda s: None, inventory=lambda h: {},
                        pending_old=lambda r, i: next(rows)) == 0


def test_a_failed_evidence_read_is_not_retried_as_transient(tmp_path):
    def pending(record, rows):
        raise ValueError("unbounded pinned consumer: mishe-x.service")

    with pytest.raises(ValueError, match="unbounded pinned consumer"):
        po.drain_old(tmp_path, 5, timeout=5, sleep=lambda s: None, inventory=lambda h: {}, pending_old=pending)
