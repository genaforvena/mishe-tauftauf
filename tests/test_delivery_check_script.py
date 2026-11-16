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


def test_build_cache_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MISHE_BUILD_CACHE", str(tmp_path / "custom"))
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: True)
    assert dc.build_cache(tmp_path, "/site") == tmp_path / "custom"


def test_build_cache_prefers_a_separate_writable_scratch_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_BUILD_CACHE", raising=False)
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: p == dc.SCRATCH_MOUNT)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: True)
    assert dc.build_cache(tmp_path, "/site") == dc.SCRATCH_MOUNT / "mishe-cargo-target"


def test_build_cache_falls_back_when_scratch_is_absent_or_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_BUILD_CACHE", raising=False)
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: False)
    assert dc.build_cache(tmp_path, "/site") == Path("/site") / "cache" / "cargo-target"
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: False)
    assert dc.build_cache(tmp_path, "/site") == Path("/site") / "cache" / "cargo-target"


def test_build_cache_without_a_site_home_uses_the_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_BUILD_CACHE", raising=False)
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: True)
    assert dc.build_cache(tmp_path, None) == tmp_path / "target-delivery-check"


def test_is_elf_accepts_only_elf_magic(tmp_path):
    elf = tmp_path / "real"
    elf.write_bytes(b"\x7fELF\x02\x01\x01rest")
    stub = tmp_path / "stub"
    stub.write_bytes(b"#!/bin/sh\necho mishe-tape\n")
    assert dc.is_elf(elf)
    assert not dc.is_elf(stub)


def test_a_non_elf_built_binary_fails_the_check_before_the_suite(tmp_path, monkeypatch, capsys):
    crate = tmp_path / "rust" / "tape"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "mishe-tape"\n')
    cache = tmp_path / "cache"

    def fake_build(crate_dir, target, cargo):
        (target / "release").mkdir(parents=True)
        (target / "release" / "mishe-tape").write_bytes(b"#!/bin/sh\necho mishe-tape\n")

    ran = []
    monkeypatch.setattr(dc, "build", fake_build)
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: ran.append(a) or None)
    monkeypatch.setattr(dc.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MISHE_BUILD_CACHE", str(cache))
    assert dc.main() == 2
    assert ran == []
    assert "is not an ELF binary" in capsys.readouterr().err
