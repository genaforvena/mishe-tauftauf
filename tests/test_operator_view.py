from pathlib import Path
from subprocess import CompletedProcess

import pytest


def test_operator_view_reads_brief_commands_and_current_wall(tmp_path):
    from mishe_tauftauf.operator_view import render
    (tmp_path / "operator").mkdir()
    (tmp_path / "walls").mkdir()
    (tmp_path / "operator/brief.md").write_text("Boundary-only parsing.\n")
    (tmp_path / "operator/commands.md").write_text("send-to-telegram --file REPORT\n")
    (tmp_path / "walls/operator.md").write_text("Keep an older obligation.\n")
    text, ok = render(tmp_path)
    assert ok
    assert "Boundary-only parsing." in text
    assert "send-to-telegram --file REPORT" in text
    assert "Keep an older obligation." in text


def test_missing_and_broken_operator_inputs_are_unknown(tmp_path):
    from mishe_tauftauf.operator_view import render
    text, ok = render(tmp_path)
    assert not ok and "UNKNOWN" in text
    (tmp_path / "operator").mkdir()
    (tmp_path / "operator/brief.md").write_bytes(b"\xff")
    (tmp_path / "operator/commands.md").write_text("commands")
    text, ok = render(tmp_path)
    assert not ok and "UNKNOWN" in text


def test_ensure_splits_above_existing_shell_and_never_respawns_it(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view
    calls = []
    panes = ["%old-shell"]
    def tmux(*args, **kwargs):
        calls.append(args)
        if args[0] == "list-panes":
            return CompletedProcess(args, 0, ("\n".join(panes) + "\n").encode())
        if args[0] == "split-window":
            panes.insert(0, "%dashboard")
            return CompletedProcess(args, 0, b"%dashboard\n")
        if args[0] == "show-option":
            return CompletedProcess(args, 0, b"%dashboard\n")
        return CompletedProcess(args, 0, b"")
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    monkeypatch.setattr(operator_view, "_tmux", tmux)
    operator_view.ensure(tmp_path, "owned", "operator")
    operator_view.ensure(tmp_path, "owned", "operator")
    assert len([a for a in calls if a[0] == "split-window"]) == 1
    assert "-b" in next(a for a in calls if a[0] == "split-window")
    assert all("%old-shell" not in a for a in calls if a[0] == "respawn-pane")
    assert (tmp_path / "top-pains/operator").is_file()
    assert (tmp_path / "operator/commands.md").is_file()


def test_ensure_rejects_foreign_session_before_writing(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: False)
    with pytest.raises(ValueError, match="owned"):
        operator_view.ensure(tmp_path, "foreign", "operator")
    assert not list(tmp_path.iterdir())


def test_ensure_waits_out_a_session_raised_before_its_owner_marker(tmp_path, monkeypatch):
    """The launcher raises the session and only then records the owning home.

    A consumer that starts concurrently - the operator-view service at boot - must
    wait for the marker instead of reporting a foreign session. Every boot from
    Oct 5 to Oct 10 failed here; systemd's RestartSec hid the cause.
    """
    from mishe_tauftauf import operator_view, tmux
    owners = iter([None, None, str(tmp_path.resolve())])
    monkeypatch.setattr(tmux, "session_owner", lambda session: next(owners, str(tmp_path.resolve())))
    monkeypatch.setattr(operator_view, "_tmux", lambda *a, **kw: CompletedProcess(a, 0, b"%shell\n"))
    status = operator_view.ensure(tmp_path, "owned", "operator")
    assert "restored" in status


def test_ensure_rejects_a_session_owned_by_another_home(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view, tmux
    monkeypatch.setattr(tmux, "session_owner", lambda session: str(tmp_path / "elsewhere"))
    monkeypatch.setattr(operator_view, "_tmux", lambda *a, **kw: CompletedProcess(a, 0, b""))
    with pytest.raises(ValueError, match="owned"):
        operator_view.ensure(tmp_path, "owned", "operator")
    assert not list(tmp_path.iterdir())


def test_ensure_preserves_unrecognized_existing_split(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    def tmux(*args, **kwargs):
        assert args[0] != "respawn-pane"
        return CompletedProcess(args, 0, b"%top\n%bottom\n" if args[0] == "list-panes" else b"")
    monkeypatch.setattr(operator_view, "_tmux", tmux)
    (tmp_path / "top-pains").mkdir()
    (tmp_path / "top-pains/operator").write_text("existing renderer")
    with pytest.raises(ValueError, match="preserve"):
        operator_view.ensure(tmp_path, "owned", "operator")
    assert (tmp_path / "top-pains/operator").read_text() == "existing renderer"
    assert not (tmp_path / "operator").exists()


@pytest.mark.parametrize("relative", ["operator", "operator/brief.md", "top-pains", "top-pains/operator"])
def test_ensure_rejects_symlinked_install_paths(tmp_path, monkeypatch, relative):
    from mishe_tauftauf import operator_view
    home = tmp_path / "site"
    home.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    monkeypatch.setattr(operator_view, "_tmux", lambda *a, **kw: CompletedProcess(a, 0, b"%shell\n"))
    with pytest.raises(ValueError, match="not owned"):
        operator_view.ensure(home, "owned", "operator")
    assert not list(outside.iterdir())


def test_ensure_uses_selected_runtime_and_refuses_missing_renderer(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view
    root = tmp_path / "pinned/src"
    root.mkdir(parents=True)
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    monkeypatch.setattr(operator_view, "_tmux", lambda *a, **kw: CompletedProcess(a, 0, b"%shell\n"))
    monkeypatch.setattr(operator_view, "package_for", lambda *a: root)
    with pytest.raises(ValueError, match="selected runtime lacks"):
        operator_view.ensure(tmp_path, "owned", "operator")
    assert not (tmp_path / "operator").exists()


def test_operator_renderer_participates_in_existing_lease_detector(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import json
    from mishe_tauftauf import wall_view
    (tmp_path / "health").mkdir()
    (tmp_path / "top-pains").mkdir()
    (tmp_path / "health/windows.json").write_text(json.dumps(["operator"]))
    (tmp_path / "top-pains/operator").write_text("renderer")
    monkeypatch.setenv("MISHE_SEED_SESSION", "owned")
    captured = [""]
    monkeypatch.setattr(wall_view.subprocess, "run", lambda *a, **kw: CompletedProcess(a, 0, captured[0], ""))
    lines, state = wall_view.pane_lease_lines(tmp_path)
    assert state == "UNKNOWN" and "operator" in lines[0]
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat().replace("+00:00", "Z")
    captured[0] = f"-- pane live {stamp} · refresh 5s · ticks every frame --\n"
    lines, state = wall_view.pane_lease_lines(tmp_path)
    assert state == "STALE" and "RED" in lines[0] and "operator" in lines[0]


@pytest.mark.parametrize("fault", [None, "dead", "stale", "broken", "missing-window", "missing-top", "missing-shell"])
def test_supervised_dashboard_repairs_faults_without_restarting_healthy_shell(tmp_path, monkeypatch, fault):
    from mishe_tauftauf import operator_view
    calls = []
    panes = ["%top", "%shell"] if fault != "missing-top" else ["%shell"]
    if fault == "missing-shell":
        panes = ["%top"]
    def tmux(*args, **kw):
        calls.append(args)
        data = b""
        if args[0] == "list-windows":
            data = b"" if fault == "missing-window" else b"operator\n"
        elif args[0] == "list-panes":
            data = b"%new-shell\n" if fault == "missing-window" else ("\n".join(panes) + "\n").encode()
        elif args[0] == "show-option":
            data = b"%top\n"
        elif args[0] == "split-window":
            data = b"%new-top\n"
        return CompletedProcess(args, 0, data)
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    monkeypatch.setattr(operator_view, "_tmux", tmux)
    monkeypatch.setattr(operator_view, "_pane_stopped_or_dead", lambda *a: fault == "dead")
    def read(*a, **kw):
        if fault in {"stale", "broken"}:
            raise ValueError("dashboard stale or unreadable")
        return "frame", True
    monkeypatch.setattr("mishe_tauftauf.dashboard.read", read)
    status = operator_view.ensure(tmp_path, "owned", "operator", refresh=False)
    mutations = [a for a in calls if a[0] in {"respawn-pane", "split-window", "new-window"}]
    assert bool(mutations) == (fault is not None)
    assert all("%shell" not in a for a in mutations if a[0] == "respawn-pane")
    assert bool(status) == (fault is not None)
    assert any(a[0] == "set-option" and "remain-on-exit" in a for a in calls)


def test_operator_supervisor_unit_is_persistent_and_covered(tmp_path, monkeypatch):
    from mishe_tauftauf import plant
    monkeypatch.setattr("mishe_tauftauf.runtime_source.source_for", lambda *a: tmp_path)
    text = plant.unit_text(tmp_path, "owned", "operator-view", "/python")
    assert "-m mishe_tauftauf.operator_view" in text
    assert "--session owned --window operator --follow" in text
    assert "Restart=always" in text and "WantedBy=default.target" in text
    assert "owned-operator-view.service" in plant.service_manifest(tmp_path, "owned", True)
    text = plant.unit_text(tmp_path, "owned", "operator-view", "/python", operator_window="human")
    assert "--window human --follow" in text


def test_recovery_routes_to_health_without_reporting_unchanged_state(tmp_path, monkeypatch):
    from mishe_tauftauf import operator_view
    statuses = iter(["", "controlled recovery"])
    sleeps = []
    messages = []
    monkeypatch.setattr(operator_view, "ensure", lambda *a, **kw: next(statuses))
    monkeypatch.setattr("mishe_tauftauf.wall.message", lambda *a: messages.append(a))
    def sleep(interval):
        sleeps.append(interval)
        if len(sleeps) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(operator_view.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        operator_view.run(tmp_path, "owned", "operator", 5)
    assert len(messages) == 1
    assert messages[0][1:3] == ("operator-view", "health")
    assert "controlled recovery" in messages[0][3]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_dashboard_marker_restores_publisher_and_only_removes_new_pane(tmp_path, monkeypatch, existing, cleanup_fails):
    from mishe_tauftauf import operator_view
    from mishe_tauftauf.tmux import TmuxError
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    calls = []
    def tmux(*args, **kw):
        calls.append(args)
        if args[0] == "list-panes":
            return CompletedProcess(args, 0, b"%shell\n")
        if args[0] == "split-window":
            return CompletedProcess(args, 0, b"%new\n")
        if args[0] == "set-option":
            raise TmuxError("controlled marker failure")
        if args[0] == "kill-pane" and cleanup_fails:
            raise TmuxError("controlled cleanup failure")
        return CompletedProcess(args, 0, b"")
    monkeypatch.setattr(operator_view, "_tmux", tmux)
    top = tmp_path / "top-pains/operator"
    if existing:
        top.parent.mkdir()
        top.write_text("old publisher")
        top.chmod(0o700)
    with pytest.raises(TmuxError, match="controlled"):
        operator_view.ensure(tmp_path, "owned", "operator")
    assert ("kill-pane", "-t", "%new") in calls
    assert all("%shell" not in a for a in calls if a[0] == "kill-pane")
    if existing:
        assert top.read_text() == "old publisher" and top.stat().st_mode & 0o777 == 0o700
    else:
        assert not top.exists()


@pytest.mark.parametrize("fault,expected", [
    ("dead", "(trigger: pane-stopped-or-dead)"),
    ("stale", "(trigger: publication: dashboard stale or future)"),
])
def test_recovery_message_names_the_check_that_fired(tmp_path, monkeypatch, fault, expected):
    """A dead renderer and a live renderer respawned for an unreadable, missing or
    stale publication must not read the same on the shared tape."""
    from mishe_tauftauf import operator_view
    def tmux(*args, **kw):
        if args[0] == "list-panes":
            return CompletedProcess(args, 0, b"%top\n%shell\n")
        if args[0] == "show-option":
            return CompletedProcess(args, 0, b"%top\n")
        return CompletedProcess(args, 0, b"")
    monkeypatch.setattr(operator_view, "await_owned", lambda *a: True)
    monkeypatch.setattr(operator_view, "_tmux", tmux)
    monkeypatch.setattr(operator_view, "_pane_stopped_or_dead", lambda *a: fault == "dead")
    def read(*a, **kw):
        if fault == "stale":
            raise ValueError("dashboard stale or future")
        return "frame", True
    monkeypatch.setattr("mishe_tauftauf.dashboard.read", read)
    status = operator_view.ensure(tmp_path, "owned", "operator", refresh=False)
    assert expected in status
