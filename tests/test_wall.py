from datetime import datetime, timezone, timedelta
import json
import os
import subprocess
import pytest
from mishe_tauftauf import seed, post_check, observations
from mishe_tauftauf.feed import Feed


def setup_wall(home):
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)+timedelta(hours=10)).isoformat()}))


def test_wall_chat_does_not_call_receipt_reviewer(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    def reject(*args, **kwargs):
        raise AssertionError("receipt reviewer was invoked")
    monkeypatch.setattr(post_check, "require", reject)
    assert Feed(tmp_path).append("genome", "Investigating two approaches; no patch yet.").sequence == 1


def test_planning_turn_settles_without_task_claim(tmp_path):
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    note = tmp_path/"plan.md"
    note.write_text("I investigated two options. Next: prototype the smaller one.")
    assert "yield" in seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=True, result="verified")
    assert (tmp_path/"walls/genome.md").read_text() == note.read_text()
    assert seed._state(tmp_path, "genome")[1] is None


def test_wall_context_includes_only_monitored_role_walls(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "genome.md").write_text("Genome plan")
    (tmp_path / "walls" / "health.md").write_text("Health plan")
    (tmp_path / "walls" / "operator.md").write_text("Operator position")
    (tmp_path / "walls" / "audit.md").write_text("Scratch audit")
    (tmp_path / "charters").mkdir()
    (tmp_path / "minds").mkdir()
    (tmp_path / "charters" / "resident.md").write_text("resident")
    (tmp_path / "minds" / "resident").write_text("#!/bin/sh\n")
    (tmp_path / "walls" / "resident.md").write_text("Resident plan")
    wall.message(tmp_path, "health", "genome", "Sample is ready; see artifacts/sample.md")
    text = wall.context(tmp_path, "genome")
    sections = [line for line in text.splitlines() if line.startswith("WALL ")]
    assert sections == ["WALL health", "WALL operator", "WALL resident"]
    assert "Operator position" in text
    assert "Scratch audit" not in text
    assert "Sample is ready; see artifacts/sample.md" in text
def test_wall_write_rejects_uninitialized_reserved_home(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    # A typo'd --home must not silently raise a second site tree. A reserved
    # .mishe-* name inside a worktree needs the initialized layout first.
    stray = tmp_path / ".mishe-tauftauft"
    with pytest.raises(ValueError):
        wall.write(stray, "discover", "notes")
    assert not stray.exists()

def test_wall_write_rejects_oversized_wall(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "x" * (wall.WALL_MAX_BYTES + 1))
    assert not (tmp_path / "walls" / "genome.md").exists()


def test_wall_write_rejects_too_many_lines(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "line\n" * (wall.WALL_MAX_LINES + 1))
    assert not (tmp_path / "walls" / "genome.md").exists()


def test_wall_limit_is_configurable_and_enforced_at_the_bound(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "wall_max_bytes": 32}))
    wall.write(tmp_path, "genome", "y" * 32)
    assert (tmp_path / "walls" / "genome.md").read_text() == "y" * 32
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "y" * 33)
    assert (tmp_path / "walls" / "genome.md").read_text() == "y" * 32



def test_wall_is_visible_without_hiding_failed_sensor(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "health", "Planning an investigation; no repair yet.")
    monkeypatch.setattr(observations, "run_renderer", lambda *a, **k: observations.RenderedPain("health", "STATE: RED sensor failed\n", False))
    rendered = observations.compose_frame(tmp_path, "health")
    assert not rendered.ok
    assert "STATE: RED sensor failed" in rendered.body and "Planning an investigation" in rendered.body



def test_check_report_marks_stale_by_mtime_without_changing_report(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS pinned sha=old\n")
    now = 10_000
    monkeypatch.setattr(observations.time, "time", lambda: now)

    os.utime(path, (now - 900, now - 900))
    assert observations.check_report(tmp_path, "genome") == "SYSTEM ZERO\nPASS pinned sha=old\n"

    os.utime(path, (now - 901, now - 901))
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report exceeds 900 seconds\nPASS pinned sha=old\n"
    )


def test_check_report_missing_mtime_is_stale(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS old\n")
    from pathlib import Path
    original_stat = Path.stat

    def failing_stat(self, *args, **kwargs):
        if self == path:
            raise OSError("mtime unavailable")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report exceeds 900 seconds\nPASS old\n"
    )


def test_check_report_future_mtime_is_stale(tmp_path, monkeypatch):
    """A report mtime ahead of the wall clock is not evidence of freshness."""
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS pinned sha=old\n")
    now = 10_000
    monkeypatch.setattr(observations.time, "time", lambda: now)

    os.utime(path, (now + 60, now + 60))
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report timestamp is ahead of the wall clock\nPASS pinned sha=old\n"
    )

    os.utime(path, (now, now))
    assert observations.check_report(tmp_path, "genome") == "SYSTEM ZERO\nPASS pinned sha=old\n"


def test_watcher_future_heartbeat_is_stale(tmp_path):
    """A heartbeat timestamp ahead of the wall clock cannot read live."""
    from mishe_tauftauf import observations
    checks = tmp_path / "checks"
    checks.mkdir()
    heartbeat = checks / "silence-heartbeat.json"

    heartbeat.write_text(json.dumps({"at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
    assert observations._watcher(tmp_path) == "stale"

    heartbeat.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat()}))
    assert observations._watcher(tmp_path) == "live"

def test_pane_reads_the_shared_feed_once(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    calls = []
    original = Feed.entries

    def counting(self, **kwargs):
        calls.append(self.home)
        return original(self, **kwargs)

    monkeypatch.setattr(Feed, "entries", counting)
    wall.pane(tmp_path, "genome")
    assert len(calls) == 1, "a pane frame must not rescan the whole feed per section"


def test_feed_read_paths_take_a_shared_lock(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    Feed(tmp_path).append("genome", "Shared read lock probe with a real entry.")
    import fcntl

    modes = []
    original = fcntl.flock

    def recording(fd, mode):
        modes.append(mode)
        return original(fd, mode)

    monkeypatch.setattr(fcntl, "flock", recording)
    Feed(tmp_path).entries()
    Feed(tmp_path).tail_sequence()
    assert modes and all(mode == fcntl.LOCK_SH for mode in modes), \
        "concurrent readers must not serialize behind an exclusive lock"


def test_expired_trial_stops_new_wakes(tmp_path):
    from mishe_tauftauf import wall
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    assert "ended" in wall.tick(tmp_path, "session", "genome", 300)
    assert not Feed(tmp_path).entries()


def test_expired_trial_can_recover_existing_turn_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    assert wall.tick(tmp_path, "session", "genome", 300).startswith("wake ")
    assert "wake window ended" in sent[-1]
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1


def test_invalid_mode_refuses_silently_switching_coordination(tmp_path):
    from mishe_tauftauf import wall
    (tmp_path/"coordination-mode.json").write_text("broken")
    with pytest.raises(ValueError, match="coordination mode"):
        wall.enabled(tmp_path)


def test_addressed_message_wakes_stable_mind_without_ledger(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    sent = []
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    monkeypatch.setattr(dashboard, "read", lambda *a: ("STATE: GREEN steady\n", True))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    assert "Wall coordination is canonical" in sent[-1]
    note = tmp_path / "note"
    note.write_text("Planning done. Waiting for sample.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    assert "waiting" in wall.tick(tmp_path, "session", "genome", 3600)
    wall.message(tmp_path, "health", "genome", "New sample available.")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake ")
    assert "New sample available" in sent[-1]
    assert "unsettled" in wall.tick(tmp_path, "session", "genome", 3600)
    assert len(sent) == 2


def test_failed_command_with_same_text_wakes_after_settlement(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    sent = []
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    ok = [True]
    monkeypatch.setattr(dashboard, "read", lambda *a: ("STATE: GREEN steady\n", ok[0]))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    ok[0] = False
    assert "unsettled" in wall.tick(tmp_path, "session", "genome", 3600)
    note = tmp_path / "note"
    note.write_text("Turn completed; inspect failed command next.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake ")
    assert len(sent) == 2


def test_docs_pane_reads_edits_and_reports_missing_page(tmp_path):
    from mishe_tauftauf.wall_view import render
    home = tmp_path / "site"
    home.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "docs_document": "docs/mesh.md"}))
    page = docs / "mesh.md"
    page.write_text("Mesh helps minds observe their work.")
    assert "Mesh helps" in render(home, "docs")
    page.write_text("Mesh is a shared practice.")
    assert "shared practice" in render(home, "docs")
    assert "Mesh helps" not in render(home, "docs")
    page.unlink()
    assert "STATE: UNKNOWN" in render(home, "docs")


def test_wall_view_dispatches_witness_and_marks_unproduced_roles(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    called = []
    monkeypatch.setattr(wall_view.seed_witness_view, "render", lambda home: called.append(home) or "WITNESS\n")
    assert wall_view.render(tmp_path, "witness") == "WITNESS\n"
    assert called == [tmp_path]
    for role in ("genome", "research-methods"):
        assert "REPORT: NOT PRODUCED" in wall_view.render(tmp_path, role)

def test_compose_frame_omits_legacy_reports_for_reportless_roles(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import observations, wall
    from mishe_tauftauf.observations import RenderedPain
    reports = tmp_path / "observations"
    reports.mkdir()
    for role in ("genome", "research-methods"):
        (reports / role).write_text("STALE LEGACY VERDICT\n")
        monkeypatch.setattr(
            observations, "run_renderer",
            lambda _home, slug, _timeout, **k: RenderedPain(slug, "REPORT: NOT PRODUCED\n", True),
        )
        monkeypatch.setattr(wall, "pane", lambda *_args: "")
        frame = observations.compose_frame(tmp_path, role).body
        assert "REPORT: NOT PRODUCED" in frame
        assert "STALE LEGACY VERDICT" not in frame

def test_health_dashboard_uses_user_runtime_and_preserves_bus_unknown(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(["health.service"]))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    expected_runtime = f"/run/user/{os.getuid()}"

    def systemctl_with_runtime(command, **kwargs):
        if kwargs.get("env", {}).get("XDG_RUNTIME_DIR") == expected_runtime:
            return subprocess.CompletedProcess(
                command, 0,
                "Id=health.service\nActiveState=active\nSubState=running\nNRestarts=0\n",
                "")
        return subprocess.CompletedProcess(
            command, 1, "", "Failed to connect to bus: No medium found")

    monkeypatch.setattr(wall_view.subprocess, "run", systemctl_with_runtime)
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    healthy = wall_view.render(home, "health")
    assert "SERVICES: GREEN — 1 listed units active/running, 0 restarts" in healthy
    assert "STATE: GREEN — listed services running" in healthy

    def systemctl_failed(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 1, "", "Failed to connect to bus: No medium found")

    monkeypatch.setattr(wall_view.subprocess, "run", systemctl_failed)
    unavailable = wall_view.render(home, "health")
    assert "STATE: UNKNOWN — services unavailable: Failed to connect to bus: No medium found" in unavailable


def test_empty_service_manifest_prints_no_phantom_service_row(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps([]), encoding="utf-8")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("systemctl show called for an empty manifest")

    monkeypatch.setattr(wall_view.subprocess, "run", unexpected)
    lines, roots, note = wall_view._service_block(home)
    assert lines == []
    assert roots == {}
    assert note == "RED — service failure or empty manifest"


def test_health_dashboard_flags_a_frozen_renderer_lease(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text(json.dumps(["health.service"]), encoding="utf-8")
    (home / "top-pains").mkdir()
    (home / "top-pains" / "health").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "sess")
    capture = {"stdout": ""}

    def run(argv, **_kwargs):
        if argv[0] == "tmux" and "capture-pane" in argv:
            return subprocess.CompletedProcess(argv, 0, capture["stdout"], "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(
                argv, 0, "Id=health.service\nActiveState=active\nSubState=running\nNRestarts=0\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    def footer(age_seconds):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
        return f"-- pane live {stamp} · refresh 5s · ticks every frame --\n"

    monkeypatch.setattr(wall_view.subprocess, "run", run)
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")

    capture["stdout"] = footer(4.0)
    fresh = wall_view.render(home, "health")
    assert "PANE LEASE: GREEN 1 renderers advancing" in fresh
    assert "STATE: GREEN — listed services running" in fresh

    capture["stdout"] = footer(600.0)
    frozen = wall_view.render(home, "health")
    assert "PANE LEASE: RED stale=health(" in frozen
    assert "STATE: RED — a top-pane renderer lease stopped advancing" in frozen

    capture["stdout"] = "no footer here\n"
    unknown = wall_view.render(home, "health")
    assert "PANE LEASE: UNKNOWN no-lease=health" in unknown


def test_tmux_send_failure_stays_visible_without_killing_supervisor(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    from mishe_tauftauf.tmux import TmuxError
    setup_wall(tmp_path)
    def fail(*a):
        raise TmuxError("pane disappeared during send")
    monkeypatch.setattr(wall, "tick", fail)
    assert "UNKNOWN" in seed.tick(tmp_path, "session", "genome")


def test_exhausted_send_can_be_reconciled_and_retried_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    from mishe_tauftauf.tmux import TmuxError
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    def fail(*a):
        raise TmuxError("lost pane")
    monkeypatch.setattr(seed, "_send", fail)
    journal = tmp_path / "checks" / f"wall-send-genome-{wake.sequence}.json"
    for attempt in range(2):
        with pytest.raises(TmuxError):
            wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
        data = json.loads(journal.read_text())
        data["at"] = (datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
        journal.write_text(json.dumps(data))
    assert "exhausted" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert "UNKNOWN" in wall.pane(tmp_path, "genome")
    wall.retry(tmp_path, "genome")
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    assert wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger").startswith("wake ")
    assert len(sent) == 1 and seed._state(tmp_path, "genome")[1] == wake.sequence
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1


def test_exhausted_send_recovers_after_bounded_hold_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    journal = tmp_path / "checks" / f"wall-send-genome-{wake.sequence}.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"at": (datetime.now(timezone.utc)-timedelta(minutes=45)).isoformat(),
                                   "attempts": 2, "phase": "delivered", "wake": wake.sequence}))
    assert wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger").startswith("wake ")
    assert len(sent) == 1 and "Reconcile any prior effects" in sent[0]
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1
    data = json.loads(journal.read_text())
    assert data["attempts"] == 2 and data["phase"] == "delivered"
    # Recovery-armed exhaustion is not presented as a dead channel.
    assert "TRANSPORT: UNKNOWN" not in wall.pane(tmp_path, "genome")
    # The recovery is bounded: a fresh exhausted counter stays held, no hot loop.
    assert "exhausted" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert len(sent) == 1
    # A busy mind is never interrupted: hold even after the bounded interval.
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: False)
    journal.write_text(json.dumps({"at": (datetime.now(timezone.utc)-timedelta(minutes=45)).isoformat(),
                                   "attempts": 2, "phase": "sending", "wake": wake.sequence}))
    assert "mind busy" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert len(sent) == 1


def test_pane_keeps_shared_chat_visible_when_only_transport_is_recent(tmp_path):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    wall.write(tmp_path, "witness", "Long edited notes\n" * 70)
    Feed(tmp_path).append("health", "The observable caller failed; investigate this shared finding.")
    for _ in range(15):
        Feed(tmp_path).append("seed", "Transport chatter; no human finding.")
    text = wall.pane(tmp_path, "witness")
    assert "CHAT.LOG" in text
    assert "The observable caller failed" in text.split("CHAT.LOG", 1)[1]
    assert "The observable caller failed" in wall.context(tmp_path, "witness")


def test_omp_attachment_requires_actual_submit_text(tmp_path, monkeypatch):
    from types import SimpleNamespace
    typed = []
    started = []
    captures = [0]
    card = "╭── 📄 #1 ───╮\n│WAKE text│\n╰ +18 lines ╯\n π > INSERT >\n╰─ 📄 #1"
    def tmux(*args, **kwargs):
        if args[0] == "send-keys" and "-l" in args:
            typed.append(args[-1])
        if args[0] == "send-keys" and args[-1] == "C-m" and typed:
            started.append(True)
        if args[0] == "capture-pane":
            captures[0] += 1
            out = " π > INSERT >" if captures[0] == 1 else card
        elif args[0] == "display-message":
            out = "omp"
        elif args[0] == "show-option":
            out = str(tmp_path)
        else:
            out = ""
        return SimpleNamespace(returncode=0, stdout=out.encode())
    monkeypatch.setattr(seed, "_tmux", tmux)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    seed._send("session:witness.1", "WAKE text")
    assert started, "Enter alone leaves the OMP attachment idle"


def test_pane_frame_leads_with_headline(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    setup_wall(tmp_path)
    (tmp_path/"checks").mkdir()
    (tmp_path/"checks"/"silence-watch.json").write_text(json.dumps(
        {"state":"ENDED","idle_seconds":5000.0,"last_action":"2026-01-01T00:00:00+00:00",
         "evidence":"chat.log 1 seed","threshold_seconds":600.0,
         "delivery":{"last_commit_seconds":63000.0,"last_activation_seconds":None}}))
    (tmp_path/"checks"/"silence-heartbeat.json").write_text(json.dumps(
        {"at":datetime.now(timezone.utc).isoformat(),"state":"ENDED"}))
    monkeypatch.setattr(observations,"run_renderer",
                        lambda *a, **k: observations.RenderedPain("genome","BODY\n",True))
    body=observations.compose_frame(tmp_path,"genome").body
    assert body.startswith("HEADLINE: RED — mind ended")
    assert "commit 17.5h ago" in body and "BODY" in body


def test_restarting_unit_stays_visible(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(["flaky.service"]))
    monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    monkeypatch.setattr(wall_view.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, "Id=flaky.service\nActiveState=active\nSubState=running\nNRestarts=7\n", ""))
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    body = wall_view.render(home, "health")
    assert "SERVICE flaky.service: GREEN active/running restarts=7" in body
    assert "0 restarts" not in body


def test_display_ages_do_not_create_observations_or_wakes(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    sensor = ["HEADLINE: GREEN — mind ok idle 5s; watcher live; commit 0.1h ago; activation 0.5h ago\nPATCH repair: applied\nSTATE: GREEN steady\n"]
    monkeypatch.setattr(dashboard, "read", lambda *a: (sensor[0], True))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    note = tmp_path / "note"
    note.write_text("Waiting for named producer evidence.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    before = len(Feed(tmp_path).entries())
    sensor[0] = sensor[0].replace("idle 5s", "idle 22s").replace("0.1h ago", "0.2h ago")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("waiting")
    assert len(Feed(tmp_path).entries()) == before
    sensor[0] = sensor[0].replace("HEADLINE: GREEN", "HEADLINE: UNKNOWN").replace("watcher live", "watcher unknown")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake")


@pytest.mark.parametrize("role", ["health", "witness", "genome"])
def test_patch_and_headline_failures_are_meaningful_for_every_monitor(role):
    from mishe_tauftauf import wall
    healthy = "HEADLINE: GREEN — mind ok idle 5s; watcher live; commit unknown; activation unknown\nPATCH repair: applied\nSERVICES: GREEN — 1 listed\nSTATE: GREEN\n"
    assert wall.observation_text(role, healthy) != wall.observation_text(role, healthy.replace("PATCH repair: applied", "PATCH repair: revert-failed"))
    assert wall.observation_text(role, healthy) != wall.observation_text(role, healthy.replace("watcher live", "watcher unknown"))


def test_witness_digest_normalizes_only_volatile_evidence_and_chat_text():
    from mishe_tauftauf.wall import observation_text
    first = (
        "CHAT RATE: RED — source: 8 entries. Trace it.\n"
        "PUBLICATION RESULT: CLEAR semantic=clear\n"
        "Active evidence: private report=/site/private/a.json\n"
        "Evidence: private report=/site/private/b.json\n"
        "Evidence: /site/private/c.json\n"
        "ANOMALY: RED id=stable transition=opened\n"
        "LATEST CHAT.LOG TEXT (all roles):\n"
        "42 genome: first changing message\n"
        "GOAL: preserve monitoring\n"
        "STATE: RED\n"
    )
    second = first.replace("/site/private/a.json", "/other/private/a.json")
    second = second.replace("/site/private/b.json", "/other/private/b.json")
    second = second.replace("/site/private/c.json", "/other/private/c.json")
    second = second.replace("8 entries. Trace it.", "11 entries. More churn.")
    second = second.replace("first changing message", "later changing message")
    assert observation_text("witness", first) == observation_text("witness", second)
    assert observation_text("witness", first) != observation_text("witness", first.replace("CHAT RATE: RED", "CHAT RATE: GREEN"))
    assert observation_text("witness", first) != observation_text("witness", first.replace("id=stable", "id=changed"))
    assert observation_text("witness", first) != observation_text("witness", first.replace("STATE: RED", "STATE: GREEN"))
    framed = ("HEADLINE: GREEN — mind ok; watcher live\n"
              "HEADLINE: GREEN — mind ok idle 5s; watcher live; commit 0.1h ago; activation 3.4h ago\n") + first
    assert observation_text("witness", framed) == observation_text(
        "witness", framed.replace("idle 5s", "idle 61s").replace("0.1h ago", "0.9h ago"))


def test_witness_digest_excludes_advisor_status_lines():
    from mishe_tauftauf.wall import observation_text
    frame = (
        "HEADLINE: GREEN — mind ok\n"
        "ANALYSIS ADVISOR: READY wake=1 suggested=investigate seconds=2 artifact=/tmp/1.json\n"
        "STATE: GREEN\n"
    )
    changed_advice = frame.replace(
        "READY wake=1 suggested=investigate seconds=2 artifact=/tmp/1.json",
        "UNKNOWN wake=2 suggested=wait seconds=9 artifact=/tmp/2.json",
    )
    assert observation_text("witness", frame) == observation_text("witness", changed_advice)
    assert observation_text("witness", frame) != observation_text(
        "witness", frame.replace("STATE: GREEN", "STATE: RED"))


def test_wall_outcome_requires_evidence_and_cli_records_prose(tmp_path):
    from mishe_tauftauf import wall
    from mishe_tauftauf.cli import main
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    note = tmp_path / "note.md"
    note.write_text("Removed the failed camera hypothesis after a controlled comparison.")
    evidence = tmp_path / "comparison.txt"
    evidence.write_text("control=4 candidate=4")
    with pytest.raises(ValueError, match="evidence"):
        wall.outcome(tmp_path, "discover", "hypothesis-changed", note.read_text(), tmp_path / "absent")
    assert main(["--home", str(tmp_path), "wall", "outcome", "--owner", "discover", "--kind", "hypothesis-changed", "--file", str(note), "--evidence", str(evidence)]) == 0
    entry = Feed(tmp_path).entries()[-1]
    assert entry.body.startswith("Wall outcome hypothesis-changed")
    assert payload(entry)["evidence"]["sha256"]


def test_delivery_dashboard_separates_source_and_runtime_and_marks_incomplete(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["test.service"]))
    (home / "patches").mkdir()
    (home / "patches/change.json").write_text(json.dumps({"phase": "applied"}))
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS sha=older")
    def commands(command, **kwargs):
        output = ("newer\n" if "rev-parse" in command else " M file\n" if "status" in command else
                  "Id=test.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
        return subprocess.CompletedProcess(command, 0, output, "")
    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    text = wall_view.render(home, "genome")
    assert "CI: PASS sha=older" in text and "SOURCE: checkout=newer changed_paths=1" in text
    assert "DEPLOYED: root=" in text and "sha256=" in text
    assert "PATCH change: applied delivery=incomplete" in text
    (home / "patches/change.json").write_text(json.dumps({"phase": "applied", "delivery_verified": True}))
    assert "PATCH change: applied delivery=incomplete" in wall_view.render(home, "genome")
    (home / "patches/change.json").write_text(json.dumps({
        "phase": "applied", "delivery_verified": True, "verification": {"at": "2026-10-01T00:00:00+00:00"}}))
    assert "PATCH change: applied delivery=verified" in wall_view.render(home, "genome")


def test_dashboard_flags_runtime_drift_against_the_pin(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    running = tmp_path / "running"
    assert wall_view._runtime_state(home, running) == ("UNPINNED", "RUNTIME: pin=UNPINNED")
    (home / "health/runtime-release.json").write_text("{}")
    pinned = tmp_path / "release"

    def at(source):
        return lambda h, default: source

    monkeypatch.setattr(runtime_source, "source_for", at(pinned))
    state, line = wall_view._runtime_state(home, running, {"unit.service": [str(pinned)]})
    assert state == "MATCH" and line.endswith("MATCH")
    state, line = wall_view._runtime_state(home, running, {"unit.service": [str(tmp_path / "other")]})
    assert state == "DRIFT" and line.endswith("DRIFT") and str(pinned) in line
    # The release coordinator is declared on the development checkout, not the pin.
    state, line = wall_view._runtime_state(
        home, running, {"session-coordination.service": [str(tmp_path)]})
    assert state == "DECLARED" and line.endswith("observes the checkout")
    # Any other root for that unit is still a drift, not a declared exception.
    state, line = wall_view._runtime_state(
        home, running, {"session-coordination.service": [str(tmp_path / "other")]})
    assert state == "DRIFT"
    assert wall_view._runtime_state(home, running)[1].endswith("services=UNKNOWN")
    assert wall_view._runtime_state(home, running, {})[1].endswith("services=none")

    def broken(h, default):
        raise ValueError("runtime pin invalid: missing release")

    monkeypatch.setattr(runtime_source, "source_for", broken)
    assert "pin=UNKNOWN" in wall_view._runtime_state(home, running, [])[1]


def test_import_roots_reads_pythonpath_from_systemd_environment():
    from mishe_tauftauf import wall_view
    assert wall_view._import_roots("PYTHONPATH=/srv/checkout/src") == ["/srv/checkout"]
    assert wall_view._import_roots(
        "PYTHONPATH=/a/releases/x:/a/releases/x/src XDG_RUNTIME_DIR=/run/user/1000") == ["/a/releases/x", "/a/releases/x"]
    assert wall_view._import_roots("HOME=/root") == []


def test_dashboard_runtime_drift_downgrades_the_state_line(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["test.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    other = tmp_path / "other"

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=test.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={other}/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: other)
    matched = wall_view.render(home, "genome")
    assert f"RUNTIME: pin={other} services={other} MATCH" in matched
    assert "STATE: GREEN — listed services running" in matched

    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: tmp_path / "pinned")
    drifted = wall_view.render(home, "genome")
    assert "RUNTIME: pin=" in drifted and f"services={other} DRIFT" in drifted
    assert "STATE: RED — runtime drift: service import roots differ from the pinned release" in drifted
    assert "STATE: GREEN" not in drifted


def test_dashboard_declares_the_coordination_checkout_exception(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["session-coordination.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: tmp_path / "release")

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=session-coordination.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={tmp_path}/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    text = wall_view.render(home, "genome")
    assert f"services={tmp_path} DECLARED" in text
    assert "STATE: GREEN — listed services running" in text
    assert "STATE: RED" not in text


def test_runtime_drift_state_forces_notification_past_a_hold_filter(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    monkeypatch.setattr(observations, "run_filter", lambda *a, **k: observations.FilterResult(False, "hold"))
    sensor = ["HEADLINE: GREEN — mind ok idle 5s; watcher live; commit unknown; activation unknown\n"
              "STATE: GREEN — listed services running; CI reported separately\n"]
    monkeypatch.setattr(dashboard, "read", lambda *a: (sensor[0], True))

    def last_notify():
        record = [e for e in Feed(tmp_path).entries() if "[record] " in e.body][-1]
        return payload(record)["notify"]

    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    assert last_notify() is False
    note = tmp_path / "note"
    note.write_text("Held for the drift observation.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    sensor[0] = sensor[0].replace(
        "STATE: GREEN — listed services running; CI reported separately",
        "STATE: RED — runtime drift: service import roots differ from the pinned release")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake")
    assert last_notify() is True


@pytest.mark.parametrize("value", [[1800], {"genome": -1}, {"genome": float("inf")}, {"genome": "soon"}])
def test_invalid_periodic_review_config_is_visible_unknown(tmp_path, value):
    setup_wall(tmp_path)
    config = json.loads((tmp_path / "coordination-mode.json").read_text())
    config["self_pick_seconds"] = value
    (tmp_path / "coordination-mode.json").write_text(json.dumps(config))
    assert "UNKNOWN" in seed.tick(tmp_path, "session", "genome")
