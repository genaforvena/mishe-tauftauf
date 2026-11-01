from __future__ import annotations

import subprocess
from pathlib import Path

from mishe_tauftauf import seed, window_manifest


def _tmux_listing(output: bytes):
    return lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, output)


def test_contract_precheck_skips_seed_lock_when_declared_windows_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(window_manifest, "read", lambda home: {"ledger", "tail"})
    monkeypatch.setattr(seed, "_tmux", _tmux_listing(b"ledger\ntail\nsenses\n"))
    assert seed._contract_windows_missing(tmp_path, "s") is False


def test_contract_precheck_requests_repair_when_a_declared_window_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(window_manifest, "read", lambda home: {"ledger", "tail"})
    monkeypatch.setattr(seed, "_tmux", _tmux_listing(b"senses\ntail\n"))
    assert seed._contract_windows_missing(tmp_path, "s") is True


def test_contract_precheck_ignores_manifest_without_utility_windows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(window_manifest, "read", lambda home: {"other"})
    monkeypatch.setattr(seed, "_tmux", _tmux_listing(b"senses\n"))
    assert seed._contract_windows_missing(tmp_path, "s") is False
