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


def test_suite_temp_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MISHE_TEST_TMPDIR", str(tmp_path / "custom"))
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: False)
    assert dc.suite_temp() == tmp_path / "custom"


def test_suite_temp_prefers_a_separate_writable_scratch_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_TEST_TMPDIR", raising=False)
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: p == dc.SCRATCH_MOUNT)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: True)
    assert dc.suite_temp() == dc.SCRATCH_MOUNT / "t"


def test_suite_temp_keeps_the_default_without_a_writable_scratch_mount(monkeypatch):
    monkeypatch.delenv("MISHE_TEST_TMPDIR", raising=False)
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: False)
    assert dc.suite_temp() is None
    monkeypatch.setattr(dc.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(dc.os, "access", lambda p, mode: False)
    assert dc.suite_temp() is None


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


def test_the_suite_gets_our_tmpdir_but_no_fixed_basetemp(tmp_path, monkeypatch):
    # A fixed --basetemp is one directory pytest empties at every session start, so concurrent
    # delivery runs would delete each other's tmp_path trees.  Point TMPDIR at the scratch mount
    # and let pytest number its own per-run base temp underneath it.
    crate = tmp_path / "rust" / "tape"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "mishe-tape"\n')
    scratch = tmp_path / "scratch"

    def fake_build(crate_dir, target, cargo):
        (target / "release").mkdir(parents=True)
        (target / "release" / "mishe-tape").write_bytes(b"\x7fELF" + b"\0" * 8)

    seen = {}

    def fake_run(argv, env, **kwargs):
        seen["argv"] = argv
        seen["env"] = env

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(dc, "build", fake_build)
    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    monkeypatch.setattr(dc.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(dc, "scratch_mount", lambda: scratch)
    monkeypatch.setattr(dc, "MAX_WORKER_TMPDIR_LEN", 10000)  # this test mocks the suite, not path lengths
    monkeypatch.delenv("MISHE_TEST_TMPDIR", raising=False)
    monkeypatch.setenv("MISHE_BUILD_CACHE", str(tmp_path / "cache"))
    assert dc.main() == 0
    assert seen["env"]["TMPDIR"] == str(scratch / "t")
    assert (scratch / "t").is_dir()
    assert not any(str(a).startswith("--basetemp") for a in seen["argv"])


def test_the_suite_records_per_test_durations(tmp_path, monkeypatch):
    # The check output is retained in the release record and review log; keep the
    # per-test time-series in it so a future trim's effect is measurable per promotion.
    crate = tmp_path / "rust" / "tape"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "mishe-tape"\n')

    def fake_build(crate_dir, target, cargo):
        (target / "release").mkdir(parents=True)
        (target / "release" / "mishe-tape").write_bytes(b"\x7fELF" + b"\0" * 8)

    seen = {}

    def fake_run(argv, env, **kwargs):
        seen["argv"] = argv

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(dc, "build", fake_build)
    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    monkeypatch.setattr(dc.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(dc, "scratch_mount", lambda: None)
    monkeypatch.setenv("MISHE_BUILD_CACHE", str(tmp_path / "cache"))
    assert dc.main() == 0
    assert seen["argv"][seen["argv"].index("-n") + 1] == str(dc.SUITE_WORKERS)


def test_a_too_long_suite_tmpdir_fails_closed(tmp_path, monkeypatch, capsys):
    # A 30-byte scratch dir made 117-byte tmux sockets in the measured xdist run, past
    # the 108-byte sun_path limit. An override that cannot fit must fail before the suite.
    monkeypatch.setenv("MISHE_TEST_TMPDIR", str(tmp_path / ("d" * 120)))
    monkeypatch.setattr(dc.Path, "cwd", staticmethod(lambda: tmp_path))
    try:
        dc.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("a too-long suite TMPDIR was accepted")
    assert "sun_path" in capsys.readouterr().err


def test_worker_tmpdir_boundary_is_exact():
    at_bound = Path("/" + "d" * (dc.MAX_WORKER_TMPDIR_LEN - 1))
    dc.check_worker_tmpdir(at_bound)
    try:
        dc.check_worker_tmpdir(Path(str(at_bound) + "d"))
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("one byte over the bound was accepted")
