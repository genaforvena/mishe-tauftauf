"""The pure parts of tools/delivery_check.py: binary names and the environment variable each one is exported as."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "delivery_check.py"
spec = importlib.util.spec_from_file_location("delivery_check", SCRIPT)
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)


def test_env_names_follow_the_binary_name():
    assert dc.env_name("mishe-tape") == "MISHE_TAPE_BIN"
    assert dc.env_name("mishe-tape-facts") == "MISHE_TAPE_FACTS_BIN"
    assert dc.env_name("mishe-signal-sample") == "MISHE_SIGNAL_SAMPLE_BIN"
    assert dc.env_name("mishe-systemd-show") == "MISHE_SYSTEMD_SHOW_BIN"


def test_binaries_come_from_bin_entries_or_the_package_name(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    (tmp_path / "a" / "Cargo.toml").write_text('[package]\nname = "mishe-a"\n[[bin]]\nname = "mishe-a-bin"\n')
    (tmp_path / "b" / "Cargo.toml").write_text('[package]\nname = "mishe-b"\n')
    assert dc.binaries(tmp_path / "a") == ["mishe-a-bin"]
    assert dc.binaries(tmp_path / "b") == ["mishe-b"]


def test_every_committed_crate_maps_to_a_distinct_variable():
    names = []
    for crate in sorted((SCRIPT.parents[1] / "rust").glob("*/Cargo.toml")):
        names += [dc.env_name(b) for b in dc.binaries(crate.parent)]
    assert len(names) == len(set(names)) >= 6


def test_a_hung_build_exits_2_not_with_a_traceback(tmp_path, monkeypatch, capsys):
    import subprocess
    import pytest

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="cargo", timeout=1500)

    monkeypatch.setattr(dc.subprocess, "run", hang)
    monkeypatch.setattr(dc.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        dc.build(tmp_path, tmp_path / "target", "cargo")
    assert exc.value.code == 2
    assert "timed out" in capsys.readouterr().err
