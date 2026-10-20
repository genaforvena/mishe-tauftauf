import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from mishe_tauftauf import substrate_watch as watch
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.wall import addressed


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, timeout=5).stdout.strip()


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    root = tmp_path / "plant"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "observer@example.invalid")
    git(root, "config", "user.name", "Observer test")
    home = root / ".site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text("[]")
    (home / "top-pains").mkdir()
    (home / "minds").mkdir()
    (home / "chat.log").touch()
    (root / ".gitignore").write_text(".site/\n")
    (root / "src/mishe_tauftauf").mkdir(parents=True)
    (root / "src/mishe_tauftauf/__init__.py").write_text("# initial\n")
    (root / "docs").mkdir()
    (root / "docs/guide.md").write_text("Initial guide\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root, home


def messages(home, role):
    return [entry for entry in Feed(home).entries() if addressed(entry, role)]


def test_baseline_unchanged_heartbeat_and_head_change(site):
    root, home = site
    first = watch.check(home, now=NOW)
    assert first["status"] == "KNOWN"
    assert first["kind"] == "baseline"
    assert first["checkout"]["head"] == git(root, "rev-parse", "HEAD")
    assert Feed(home).entries() == []
    unchanged = watch.check(home, now=NOW + timedelta(seconds=30))
    assert unchanged["event_id"] == first["event_id"]
    assert unchanged["checked_at"] != first["checked_at"]
    git(root, "commit", "--allow-empty", "-m", "new head")
    changed = watch.check(home, now=NOW + timedelta(seconds=60))
    assert changed["changed"] == ["checkout"]
    assert changed["runtime"] == first["runtime"]
    assert changed["checkout"]["head"] != first["checkout"]["head"]
    for role in ("genome", "witness"):
        assert [entry.sequence for entry in messages(home, role)] == [changed["alerts"][role]]
        assert changed["checkout"]["head"] in messages(home, role)[0].body
    later = watch.check(home, now=NOW + timedelta(seconds=90))
    assert later["event_id"] == changed["event_id"]
    assert len(Feed(home).entries()) == 2
    events = list((home / "checks/substrate-watch-events").glob("*.json"))
    assert {json.loads(path.read_text())["event_id"] for path in events} == {first["event_id"], changed["event_id"]}


def test_same_dirty_filename_second_edit_changes_identity_without_publishing_contents(site):
    root, home = site
    watch.check(home, now=NOW)
    source = root / "src/mishe_tauftauf/__init__.py"
    source.write_text("SECRET-one\n")
    first = watch.check(home, now=NOW)
    source.write_text("SECRET-two\n")  # same length, same porcelain status, different actual bytes
    second = watch.check(home, now=NOW)
    assert first["checkout"]["status"] == second["checkout"]["status"]
    assert first["checkout"]["sha256"] != second["checkout"]["sha256"]
    assert first["event_id"] != second["event_id"]
    row = next(row for row in second["checkout"]["files"] if row["path"] == "src/mishe_tauftauf/__init__.py")
    assert row["sha256"] == hashlib.sha256(b"SECRET-two\n").hexdigest()
    assert len(messages(home, "genome")) == 2
    for path in (home / "checks").rglob("*.json"):
        assert "SECRET-" not in path.read_text()
    assert "SECRET-" not in Feed(home).read_bytes().decode()
    assert source.read_text() == "SECRET-two\n"
    assert git(root, "rev-parse", "HEAD") == first["checkout"]["head"]


def test_tracked_and_untracked_documentation_and_untracked_source(site):
    root, home = site
    baseline = watch.check(home, now=NOW)
    (root / "docs/guide.md").write_text("Changed documentation\n")
    tracked = watch.check(home, now=NOW)
    assert tracked["checkout"]["sha256"] != baseline["checkout"]["sha256"]
    (root / "docs/new page.md").write_text("New documentation\n")
    (root / "src/new.py").write_text("new_source = 1\n")
    (root / "analysis").mkdir()
    (root / "analysis/new.unknown-suffix").write_text("Analysis text\n")
    (root / "research").mkdir()
    (root / "research/new.notes").write_text("Research text\n")
    (root / ".local-text").write_text("Nonignored hidden text\n")
    (root / "private.txt").write_text("private content\n")
    with (root / ".gitignore").open("a") as stream:
        stream.write("private.txt\n")
    untracked = watch.check(home, now=NOW)
    rows = untracked["checkout"]["status"]
    assert {"path": "docs/new page.md", "status": "??"} in rows
    assert {"path": "src/new.py", "status": "??"} in rows
    assert all(row["path"] != "private.txt" for row in untracked["checkout"]["files"])
    for path in ("analysis/new.unknown-suffix", "research/new.notes", ".local-text"):
        assert {"path": path, "status": "??"} in rows
    (root / "docs/new page.md").write_text("Second documentation edit\n")
    second = watch.check(home, now=NOW)
    assert second["checkout"]["sha256"] != untracked["checkout"]["sha256"]
    (root / "docs/new page.md").unlink()
    removed = watch.check(home, now=NOW)
    assert removed["event_id"] != second["event_id"]


@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_checkout_failure_unknown_health_once_then_recovery(site, failure):
    root, home = site
    baseline = watch.check(home, now=NOW)
    dotgit = root / ".git"
    backup = root / ".git.saved"
    dotgit.rename(backup)
    if failure == "broken":
        dotgit.write_text("not a git directory")
    unknown = watch.check(home, now=NOW)
    assert unknown["status"] == "UNKNOWN"
    assert unknown["checkout"] is None
    assert "health" in unknown["alerts"]
    assert "checkout:" in messages(home, "health")[0].body
    assert "Next bounded action:" in messages(home, "health")[0].body
    again = watch.check(home, now=NOW + timedelta(seconds=30))
    assert again["event_id"] == unknown["event_id"]
    assert len(messages(home, "health")) == 1
    if failure == "broken":
        dotgit.unlink()
    backup.rename(dotgit)
    recovered = watch.check(home, now=NOW + timedelta(seconds=60))
    assert recovered["status"] == "KNOWN"
    assert recovered["kind"] == "recovery"
    assert recovered["checkout"] == baseline["checkout"]
    assert recovered["event_id"] != baseline["event_id"]
    assert "genome" in recovered["alerts"]


@pytest.mark.parametrize("contents", ["{broken", "[]", '{"version": 1}'])
def test_corrupt_state_is_explicit_sticky_unknown_not_new_baseline(site, contents):
    _, home = site
    baseline = watch.check(home, now=NOW)
    state = home / "checks/substrate-watch.json"
    state.write_text(contents)
    unknown = watch.check(home, now=NOW)
    assert unknown["status"] == "UNKNOWN"
    assert "prior snapshot corrupt" in unknown["state_damage"]
    assert hashlib.sha256(contents.encode()).hexdigest() in unknown["state_damage"]
    retained = home / "checks/substrate-watch-corrupt" / (hashlib.sha256(contents.encode()).hexdigest() + ".bin")
    assert retained.read_bytes() == contents.encode()
    assert str(retained) in unknown["state_damage"]
    assert messages(home, "health")
    again = watch.check(home, now=NOW + timedelta(seconds=30))
    assert again["status"] == "UNKNOWN"
    assert again["event_id"] == unknown["event_id"]
    assert len(messages(home, "health")) == 1
    assert Path(baseline["event_path"]).exists()


def test_missing_established_snapshot_is_not_silent_baseline(site):
    _, home = site
    watch.check(home, now=NOW)
    (home / "checks/substrate-watch.json").unlink()
    row = watch.check(home, now=NOW)
    assert row["status"] == "UNKNOWN"
    assert row["state_damage"] == "established snapshot missing"
    assert messages(home, "health")


def test_runtime_pin_changes_independently_of_checkout_and_live_roots(site, tmp_path, monkeypatch):
    root, home = site
    releases = []
    for name in ("release-a", "release-b"):
        release = tmp_path / name
        git(root, "clone", "--no-hardlinks", str(root), str(release))
        releases.append(release)
    pin = home / "health/runtime-release.json"

    def select(release):
        pin.write_text(json.dumps({"version": 1, "source": str(release), "sha": git(release, "rev-parse", "HEAD")}))

    select(releases[0])
    first = watch.check(home, now=NOW)
    select(releases[1])
    second = watch.check(home, now=NOW)
    assert second["checkout"] == first["checkout"]
    assert second["runtime"]["source"] == str(releases[1])
    assert second["changed"] == ["runtime"]
    assert "runtime" in messages(home, "genome")[0].body
    (home / "health/services.json").write_text('["example.service"]')
    monkeypatch.setattr(watch.wall_view, "_service_block", lambda home: ([], {"example.service": [str(releases[1])]}, "GREEN"))
    monkeypatch.setattr(watch, "_service_pids", lambda units: {"example.service": os.getpid()})
    monkeypatch.setattr(watch.wall_view, "_live_import_roots", lambda pid: [str(releases[0])])
    roots = watch.check(home, now=NOW)
    assert roots["runtime"]["service_roots"] == {"example.service": [str(releases[0])]}
    monkeypatch.setattr(watch.wall_view, "_live_import_roots", lambda pid: [str(releases[1])])
    changed_roots = watch.check(home, now=NOW)
    assert changed_roots["checkout"] == roots["checkout"]
    assert changed_roots["changed"] == ["runtime"]
    assert changed_roots["runtime"]["selection_sha256"] == roots["runtime"]["selection_sha256"]


def test_runtime_failure_is_unknown_not_checkout_change_and_recovers(site):
    _, home = site
    first = watch.check(home, now=NOW)
    (home / "health/services.json").write_text("not json")
    unknown = watch.check(home, now=NOW)
    assert unknown["status"] == "UNKNOWN"
    assert unknown["changed"] == ["runtime"]
    assert unknown["checkout"] == first["checkout"]
    assert unknown["runtime"]["source"] == first["runtime"]["source"]
    assert unknown["runtime"]["selection_state"] == "KNOWN"
    assert unknown["runtime"]["services_state"] == "UNKNOWN"
    assert messages(home, "health")
    (home / "health/services.json").write_text("[]")
    assert watch.check(home, now=NOW)["kind"] == "recovery"


def test_staged_bytes_and_rename_paths_are_evidence(site):
    root, home = site
    watch.check(home, now=NOW)
    git(root, "mv", "docs/guide.md", "docs/renamed guide.md")
    renamed = watch.check(home, now=NOW)
    assert {"status": "R ", "path": "docs/renamed guide.md", "from": "docs/guide.md"} in renamed["checkout"]["status"]
    (root / "docs/renamed guide.md").write_text("staged value\n")
    git(root, "add", "docs/renamed guide.md")
    staged = watch.check(home, now=NOW)
    assert staged["checkout"]["index_sha256"] != renamed["checkout"]["index_sha256"]


def test_cache_missing_corrupt_stale_future_and_current_without_observing(site, monkeypatch):
    _, home = site
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW)[0]
    watch.check(home, now=NOW)
    monkeypatch.setattr(watch, "_checkout", lambda home: pytest.fail("cached dashboard must not sample checkout"))
    assert "KNOWN" in watch.snapshot_lines(home, now=NOW + timedelta(seconds=20))[0]
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW + timedelta(seconds=121))[0]
    assert "future-dated" in watch.snapshot_lines(home, now=NOW - timedelta(seconds=1))[0]
    (home / "checks/substrate-watch.json").write_text("[]")
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW)[0]


def test_dm_retry_reuses_event_identity_and_does_not_duplicate_successful_recipient(site, monkeypatch):
    root, home = site
    watch.check(home, now=NOW)
    (root / "docs/guide.md").write_text("change\n")
    real = watch._send

    def fail_witness(home, source, recipient, text):
        if recipient == "witness":
            raise OSError("unavailable")
        return real(home, source, recipient, text)

    monkeypatch.setattr(watch, "_send", fail_witness)
    failed = watch.check(home, now=NOW)
    assert failed["status"] == "UNKNOWN"
    assert len(messages(home, "genome")) == 1
    monkeypatch.setattr(watch, "_send", real)
    repaired = watch.check(home, now=NOW)
    assert repaired["event_id"] == failed["event_id"]
    assert repaired["status"] == "KNOWN"
    assert len(messages(home, "genome")) == 1
    assert len(messages(home, "witness")) == 1


def test_append_before_checkpoint_crash_deduplicates_canonical_dm(site, monkeypatch):
    root, home = site
    watch.check(home, now=NOW)
    (root / "docs/guide.md").write_text("changed\n")
    real = watch._send

    def crash_after_append(home, source, recipient, text):
        real(home, source, recipient, text)
        raise RuntimeError("simulated process death after durable append")

    monkeypatch.setattr(watch, "_send", crash_after_append)
    with pytest.raises(RuntimeError):
        watch.check(home, now=NOW)
    assert len(messages(home, "genome")) == 1
    monkeypatch.setattr(watch, "_send", real)
    recovered = watch.check(home, now=NOW + timedelta(seconds=30))
    assert recovered["status"] == "KNOWN"
    assert len(messages(home, "genome")) == 1
    assert len(messages(home, "witness")) == 1


def test_corrupt_immutable_evidence_is_unknown_and_retained(site):
    _, home = site
    baseline = watch.check(home, now=NOW)
    evidence = Path(baseline["event_path"])
    evidence.write_text("{broken")
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW)[0]
    row = watch.check(home, now=NOW)
    assert row["status"] == "UNKNOWN"
    assert row["state_damage"]
    assert messages(home, "health")
    assert evidence.read_text() == "{broken"


def test_valid_json_snapshot_damage_is_not_treated_as_observed_change(site):
    _, home = site
    watch.check(home, now=NOW)
    path = home / "checks/substrate-watch.json"
    value = json.loads(path.read_text())
    value["checkout"]["head"] = "f" * 40
    path.write_text(json.dumps(value))
    row = watch.check(home, now=NOW)
    assert row["status"] == "UNKNOWN"
    assert row["state_damage"]
    assert messages(home, "health")


def test_known_checkout_notices_survive_dead_and_uninspectable_runtime_consumers(site, monkeypatch):
    root, home = site
    units = ["live.service", "dead-docs.service", "no-environment.service"]
    (home / "health/services.json").write_text(json.dumps(units))
    monkeypatch.setattr(watch.wall_view, "_service_block",
                        lambda home: ([], {unit: ["/selected"] for unit in units}, "RED"))
    monkeypatch.setattr(watch, "_service_pids",
                        lambda units: {"live.service": 101, "dead-docs.service": None, "no-environment.service": 102})
    monkeypatch.setattr(watch.wall_view, "_live_import_roots", lambda pid: ["/actual-live"] if pid == 101 else [])
    baseline = watch.check(home, now=NOW)
    assert baseline["status"] == "UNKNOWN"
    assert baseline["surface_states"] == {"checkout": "KNOWN", "runtime": "UNKNOWN"}
    runtime = baseline["runtime"]
    assert runtime["service_roots"] == {"live.service": ["/actual-live"]}
    assert runtime["service_states"] == {"live.service": "KNOWN", "dead-docs.service": "UNKNOWN",
                                         "no-environment.service": "UNKNOWN"}
    assert any("dead-docs.service" in problem for problem in runtime["problems"])
    assert any("no-environment.service" in problem for problem in runtime["problems"])
    assert messages(home, "genome") == []
    (root / "docs/guide.md").write_text("Integration must see this edit\n")
    changed = watch.check(home, now=NOW)
    assert changed["status"] == "UNKNOWN"
    assert changed["changed"] == ["checkout"]
    assert changed["runtime"] == baseline["runtime"]
    assert set(changed["alerts"]) == {"genome", "witness", "health"}
    assert changed["checkout"]["sha256"] in messages(home, "genome")[0].body
    lines = watch.snapshot_lines(home, now=NOW)
    assert "SUBSTRATE: UNKNOWN" in lines[0]
    assert any("SOURCE: KNOWN" in line and changed["checkout"]["head"] in line for line in lines)
    assert any("RUNTIME: UNKNOWN" in line for line in lines)
    counts = {role: len(messages(home, role)) for role in ("genome", "witness", "health")}
    unchanged = watch.check(home, now=NOW + timedelta(seconds=30))
    assert unchanged["event_id"] == changed["event_id"]
    assert {role: len(messages(home, role)) for role in counts} == counts
    (root / "docs/guide.md").write_text("Second integration edit while runtime still UNKNOWN\n")
    second = watch.check(home, now=NOW + timedelta(seconds=60))
    assert second["event_id"] != changed["event_id"]
    assert len(messages(home, "genome")) == 2
    assert len(messages(home, "witness")) == 2


def test_unsupported_nested_repository_is_named_without_discarding_known_files(site):
    root, home = site
    watch.check(home, now=NOW)
    git(root, "clone", "--no-hardlinks", str(root), str(root / "nested-repository"))
    changed = watch.check(home, now=NOW)
    assert changed["status"] == "UNKNOWN"
    assert changed["checkout"]["state"] == "UNKNOWN"
    assert any("nested-repository" in problem for problem in changed["checkout"]["problems"])
    source = next(row for row in changed["checkout"]["files"] if row["path"] == "src/mishe_tauftauf/__init__.py")
    assert source["sha256"] == hashlib.sha256(b"# initial\n").hexdigest()
    assert set(changed["alerts"]) == {"genome", "witness", "health"}


def test_pending_recipient_survives_new_checkout_event(site, monkeypatch):
    root, home = site
    watch.check(home, now=NOW)
    source = root / "docs/guide.md"
    source.write_text("Edit A\n")
    real = watch._send

    def unavailable(home, source, recipient, text):
        if recipient == "witness":
            raise OSError("transport unavailable")
        return real(home, source, recipient, text)

    monkeypatch.setattr(watch, "_send", unavailable)
    first = watch.check(home, now=NOW)
    assert first["status"] == "UNKNOWN"
    source.write_text("Edit B\n")
    monkeypatch.setattr(watch, "_send", real)
    second = watch.check(home, now=NOW + timedelta(seconds=30))
    for role in ("genome", "witness"):
        notices = messages(home, role)
        assert len(notices) == 2
        assert all(sum(event_id in entry.body for entry in notices) == 1
                   for event_id in (first["event_id"], second["event_id"]))
    assert list((home / "checks/substrate-watch-pending").glob("*.json")) == []


def test_append_crash_then_new_source_event_keeps_both_original_notices(site, monkeypatch):
    root, home = site
    watch.check(home, now=NOW)
    source = root / "docs/guide.md"
    source.write_text("Edit A\n")
    real = watch._send

    def crash(home, source, recipient, text):
        real(home, source, recipient, text)
        raise RuntimeError("process died after append")

    monkeypatch.setattr(watch, "_send", crash)
    with pytest.raises(RuntimeError):
        watch.check(home, now=NOW)
    first_id = json.loads((home / "checks/substrate-watch.json").read_text())["event_id"]
    source.write_text("Edit B\n")
    monkeypatch.setattr(watch, "_send", real)
    second = watch.check(home, now=NOW + timedelta(seconds=30))
    for role in ("genome", "witness"):
        notices = messages(home, role)
        assert len(notices) == 2
        assert all(sum(event_id in entry.body for entry in notices) == 1
                   for event_id in (first_id, second["event_id"]))


def test_queue_before_archive_crash_recovers_with_valid_old_snapshot_and_new_source(site, monkeypatch):
    root, home = site
    baseline = watch.check(home, now=NOW)
    source = root / "docs/guide.md"
    source.write_text("Edit A\n")
    real = watch._save

    def fail_archive(path, value):
        if path.parent.name == "substrate-watch-events":
            raise RuntimeError("process died before archiving queued event")
        return real(path, value)

    monkeypatch.setattr(watch, "_save", fail_archive)
    with pytest.raises(RuntimeError):
        watch.check(home, now=NOW)
    assert json.loads((home / "checks/substrate-watch.json").read_text())["event_id"] == baseline["event_id"]
    queued = list((home / "checks/substrate-watch-pending").glob("*.json"))
    assert len(queued) == 1
    first_id = json.loads(queued[0].read_text())["event"]["event_id"]
    source.write_text("Edit B\n")
    monkeypatch.setattr(watch, "_save", real)
    second = watch.check(home, now=NOW + timedelta(seconds=30))
    assert (home / "checks/substrate-watch-events" / (first_id + ".json")).exists()
    for role in ("genome", "witness"):
        notices = messages(home, role)
        assert len(notices) == 2
        assert all(sum(event_id in entry.body for entry in notices) == 1
                   for event_id in (first_id, second["event_id"]))


def test_corrupt_state_retention_failure_keeps_original_bytes(site, monkeypatch):
    _, home = site
    watch.check(home, now=NOW)
    state = home / "checks/substrate-watch.json"
    damaged = b"original corrupt snapshot bytes\xff"
    state.write_bytes(damaged)
    real = watch._atomic_bytes

    def fail_retention(path, raw):
        if path.parent.name == "substrate-watch-corrupt":
            raise OSError("retention unavailable")
        return real(path, raw)

    monkeypatch.setattr(watch, "_atomic_bytes", fail_retention)
    with pytest.raises(OSError):
        watch.check(home, now=NOW)
    assert state.read_bytes() == damaged


def test_non_utf8_git_filename_round_trips_and_remains_observable(site):
    root, home = site
    watch.check(home, now=NOW)
    raw_name = b"analysis-\xff.notes"
    fd = os.open(os.fsencode(root) + b"/" + raw_name, os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(b"first text\n")
    first = watch.check(home, now=NOW)
    assert first["status"] == "KNOWN"
    stored = json.loads((home / "checks/substrate-watch.json").read_text())
    row = next(row for row in stored["checkout"]["files"] if os.fsencode(row["path"]) == raw_name)
    assert row["sha256"] == hashlib.sha256(b"first text\n").hexdigest()
    assert os.fsencode(row["path"]) == raw_name
    with open(os.fsencode(root) + b"/" + raw_name, "wb") as stream:
        stream.write(b"second text\n")
    second = watch.check(home, now=NOW)
    assert second["checkout"]["sha256"] != first["checkout"]["sha256"]
    assert len(messages(home, "genome")) == 2


def test_pin_validation_neither_refreshes_index_nor_runs_fsmonitor(site, tmp_path):
    root, home = site
    release = tmp_path / "release"
    git(root, "clone", "--no-hardlinks", str(root), str(release))
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor"
    hook.write_text("#!/bin/sh\nprintf invoked > " + str(marker) + "\n")
    hook.chmod(0o700)
    git(release, "config", "core.fsmonitor", str(hook))
    package = release / "src/mishe_tauftauf/__init__.py"
    before_stat = package.stat()
    os.utime(package, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns + 3_000_000_000))
    pin = {"version": 1, "source": str(release), "sha": git(release, "rev-parse", "HEAD")}
    (home / "health/runtime-release.json").write_text(json.dumps(pin))
    index = release / ".git/index"
    original = (index.read_bytes(), index.stat().st_mtime_ns)
    reading = watch._runtime(home)
    assert reading["selection_state"] == "KNOWN"
    assert (index.read_bytes(), index.stat().st_mtime_ns) == original
    assert not marker.exists()


def test_ancestor_swap_to_external_symlink_is_never_followed(site, tmp_path, monkeypatch):
    root, _ = site
    external = tmp_path / "external"
    (external / "mishe_tauftauf").mkdir(parents=True)
    (external / "mishe_tauftauf/__init__.py").write_text("external private text\n")
    real = os.open
    swapped = False

    def swap_ancestor(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "src" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            (root / "src").rename(root / "original-src")
            (root / "src").symlink_to(external, target_is_directory=True)
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(watch.os, "open", swap_ancestor)
    with pytest.raises(OSError):
        watch._file(root, "src/mishe_tauftauf/__init__.py")
    assert swapped
    assert (external / "mishe_tauftauf/__init__.py").read_text() == "external private text\n"


@pytest.mark.parametrize("field", ["recipients", "timestamp"])
def test_event_metadata_corruption_cannot_suppress_or_rewrite_notices(site, field):
    root, home = site
    watch.check(home, now=NOW)
    (root / "docs/guide.md").write_text("source change\n")
    changed = watch.check(home, now=NOW)
    path = Path(changed["event_path"])
    event = json.loads(path.read_text())
    if field == "recipients":
        event["recipients"] = []
    else:
        event["report"]["checked_at"] = (NOW + timedelta(days=1)).isoformat()
    damaged = json.dumps(event).encode()
    path.write_bytes(damaged)
    reading = watch.check(home, now=NOW)
    assert reading["status"] == "UNKNOWN"
    assert reading["state_damage"]
    assert messages(home, "health")
    assert path.read_bytes() == damaged


def test_unignored_site_is_one_explicit_coverage_gap_not_recursive_source_events(site):
    root, home = site
    (root / ".gitignore").write_text("")
    first = watch.check(home, now=NOW)
    assert first["status"] == "UNKNOWN"
    assert first["checkout"]["problems"] == ["site state is not Git-ignored: " + str(home)]
    assert all(not (root / row["path"]).is_relative_to(home) for row in first["checkout"]["files"])
    second = watch.check(home, now=NOW + timedelta(seconds=30))
    assert second["event_id"] == first["event_id"]
    assert len(messages(home, "health")) == 1


def test_symlink_cycle_in_runtime_pin_preserves_known_checkout_notices(site, tmp_path):
    root, home = site
    watch.check(home, now=NOW)
    first = tmp_path / "cycle-a"
    second = tmp_path / "cycle-b"
    first.symlink_to(second)
    second.symlink_to(first)
    pin = home / "health/runtime-release.json"
    raw = json.dumps({"version": 1, "source": str(first), "sha": git(root, "rev-parse", "HEAD")}).encode()
    pin.write_bytes(raw)
    (root / "docs/guide.md").write_text("Known source edit with broken runtime pin\n")
    reading = watch.check(home, now=NOW + timedelta(seconds=30))
    assert reading["status"] == "UNKNOWN"
    assert reading["surface_states"] == {"checkout": "KNOWN", "runtime": "UNKNOWN"}
    assert reading["runtime"]["selection_state"] == "UNKNOWN"
    assert reading["runtime"]["selection_sha256"] == hashlib.sha256(raw).hexdigest()
    assert set(reading["alerts"]) == {"genome", "witness", "health"}
    assert reading["checkout"]["sha256"] in messages(home, "genome")[0].body
    assert pin.read_bytes() == raw
