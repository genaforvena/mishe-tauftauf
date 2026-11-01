from pathlib import Path

import pytest

from mishe_tauftauf import plant


def test_gate_line_names_caller_socket_and_owner_read(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    monkeypatch.setattr(plant, "current_cgroup_slice", lambda: "mishe-lifeline.slice")
    monkeypatch.setattr(plant, "session_owner", lambda session, home=None: "/foreign/home")
    monkeypatch.setenv("TMUX_TMPDIR", "/foreign/tmux")

    line = plant.promotion_gate_line(home, "core", "refused:not-owned")

    assert line.startswith("PROMOTION-GATE ")
    assert "caller_slice=mishe-lifeline.slice" in line
    assert "socket_source=ambient" in line
    assert "owner_read=/foreign/home" in line
    assert "verdict=refused:not-owned" in line


def test_gate_line_reports_home_socket_and_missing_owner(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    monkeypatch.setattr(plant, "current_cgroup_slice", lambda: None)
    monkeypatch.setattr(plant, "session_owner", lambda session, home=None: None)
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)

    line = plant.promotion_gate_line(home, "core", "owner-gate-passed")

    assert "caller_slice=none" in line
    assert "socket_source=home" in line
    assert "owner_read=none" in line
    assert "verdict=owner-gate-passed" in line


def test_refusal_emits_gate_line_before_raising(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(plant, "owns_session", lambda home, session: False)
    monkeypatch.setattr(plant, "current_cgroup_slice", lambda: "mishe.slice")
    monkeypatch.setattr(plant, "session_owner", lambda session, home=None: None)
    monkeypatch.setattr(plant, "site_and_session", lambda *a, **k: (tmp_path, "core"))
    monkeypatch.setattr(plant, "plant", lambda *a, **k: None)
    src = tmp_path / "release"

    with pytest.raises(ValueError, match="existing owned session"):
        plant.main(["--workspace", str(tmp_path), "--runtime-source", str(src)])

    err = capsys.readouterr().err
    assert "PROMOTION-GATE" in err and "verdict=refused:not-owned" in err
