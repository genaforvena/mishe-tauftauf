import importlib.util
from pathlib import Path


def test_revert_refuses_changed_config_and_restores_matching_bytes(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "tools/guard-activation/register-unit.py"
    spec = importlib.util.spec_from_file_location("register_unit", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    config = tmp_path / "config.json"
    backup = tmp_path / "backups"
    monkeypatch.setattr(tool, "CONFIG", config)
    monkeypatch.setattr(tool, "BACKUP_DIR", backup)
    original = b'{"life_support":{"units":["existing.service"]}}\n'
    config.write_bytes(original)
    monkeypatch.setattr(tool.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool.sys, "argv", [str(path), "add"])
    assert tool.main() == 0
    registered = config.read_bytes()
    changed = registered + b" "
    config.write_bytes(changed)
    monkeypatch.setattr(tool.sys, "argv", [str(path), "revert"])
    assert tool.main() == 1
    assert config.read_bytes() == changed
    config.write_bytes(registered)
    assert tool.main() == 0
    assert config.read_bytes() == original
