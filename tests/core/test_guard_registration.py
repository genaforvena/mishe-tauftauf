import hashlib
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def tool(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "tools/guard-activation/register-unit.py"
    spec = importlib.util.spec_from_file_location("register_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CONFIG", tmp_path / "config.json")
    return module


@pytest.mark.parametrize("arguments", [[], ["add"], ["revert"]])
def test_mutations_refused_without_touching_config_or_restore_point(
    tool, tmp_path, monkeypatch, capsys, arguments
):
    target = tmp_path / "real-config.json"
    original = b'{"life_support":{"units":["existing.service"]}}\n'
    target.write_bytes(original)
    target.chmod(0o640)
    tool.CONFIG.symlink_to(target)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "config.json.before-core-unit"
    backup.write_bytes(b"previous restore point")
    before = target.stat()
    backup_before = backup.stat()
    monkeypatch.setattr(tool.sys, "argv", ["register-unit.py", *arguments])

    assert tool.main() == 1
    assert "mutation is disabled" in capsys.readouterr().err
    assert tool.CONFIG.is_symlink()
    assert target.read_bytes() == original
    assert backup.read_bytes() == b"previous restore point"
    after = target.stat()
    backup_after = backup.stat()
    for field in ("st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(after, field) == getattr(before, field)
        assert getattr(backup_after, field) == getattr(backup_before, field)


def test_check_reports_membership_from_one_snapshot_without_claiming_guard_validation(
    tool, monkeypatch, capsys
):
    raw = b'{"life_support":{"units":["mishe-core-supervisor.service"]}}\n'
    tool.CONFIG.write_bytes(raw)
    monkeypatch.setattr(tool.sys, "argv", ["register-unit.py", "check"])

    assert tool.main() == 0
    output = capsys.readouterr().out
    assert hashlib.sha256(raw).hexdigest() in output
    assert "core unit registered: True" in output
    assert "guard configuration and admission not validated" in output
    assert tool.CONFIG.read_bytes() == raw


@pytest.mark.parametrize("raw", [b"\xff", b"{", b"[]", b'{}', b'{"life_support":{"units":[1]}}'])
def test_check_refuses_unreadable_membership(tool, monkeypatch, capsys, raw):
    tool.CONFIG.write_bytes(raw)
    monkeypatch.setattr(tool.sys, "argv", ["register-unit.py", "check"])

    assert tool.main() == 1
    assert "cannot inspect containment config" in capsys.readouterr().err
    assert tool.CONFIG.read_bytes() == raw


def test_check_refuses_missing_config(tool, monkeypatch, capsys):
    monkeypatch.setattr(tool.sys, "argv", ["register-unit.py", "check"])
    assert tool.main() == 1
    assert "cannot inspect containment config" in capsys.readouterr().err
