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


def test_wall_dm_and_shared_walls(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Investigating rollback; waiting for the health sample.")
    wall.message(tmp_path, "health", "genome", "Sample is ready; see artifacts/sample.md")
    text = wall.context(tmp_path, "genome")
    assert "Investigating rollback" in text and "Sample is ready" in text
    assert "health" in text


def test_wall_is_visible_without_hiding_failed_sensor(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "health", "Planning an investigation; no repair yet.")
    monkeypatch.setattr(observations, "run_renderer", lambda *a: observations.RenderedPain("health", "STATE: RED sensor failed\n", False))
    rendered = observations.compose_frame(tmp_path, "health")
    assert not rendered.ok
    assert "STATE: RED sensor failed" in rendered.body and "Planning an investigation" in rendered.body


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
    assert "trial window ended" in sent[-1]
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
    assert "Wall trial supersedes ledger" in sent[-1]
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
    page = docs / "mesh.md"
    page.write_text("Mesh helps minds observe their work.")
    assert "Mesh helps" in render(home, "docs")
    page.write_text("Mesh is a shared practice.")
    assert "shared practice" in render(home, "docs")
    assert "Mesh helps" not in render(home, "docs")
    page.unlink()
    assert "STATE: UNKNOWN" in render(home, "docs")

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
                        lambda *a: observations.RenderedPain("genome","BODY\n",True))
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
