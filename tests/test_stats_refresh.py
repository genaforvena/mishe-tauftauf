"""Regression: stats-refresh reports UNKNOWN, not a traceback, when systemd-run is absent."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("stats_refresh", ROOT / "checks" / "stats-refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_systemd_run_is_unknown_with_state(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = mod.main(["--home", str(tmp_path / "site")])
    assert rc == mod.UNKNOWN
    state = json.loads((tmp_path / "site" / "health" / "stats-refresh.json").read_text())
    assert state["refresh_rc"] == mod.UNKNOWN
    assert "systemd-run unavailable" in state["refresh_stderr_tail"]
    assert "stats-refresh UNKNOWN" in capsys.readouterr().err
