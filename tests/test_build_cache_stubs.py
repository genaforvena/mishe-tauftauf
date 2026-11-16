"""checks/build-cache-stubs.py flags a non-ELF installed-binary name in the cargo cache."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_cache_stubs", REPO / "checks" / "build-cache-stubs.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _cache_with(tmp_path, name, payload):
    cache = tmp_path / "cache"
    release = cache / "mishe-records" / "release"
    release.mkdir(parents=True)
    (release / name).write_bytes(payload)
    return cache


def test_real_binary_is_green(tmp_path):
    cache = _cache_with(tmp_path, "mishe-tape", b"\x7fELF" + b"\x00" * 60)
    code, _ = mod.evaluate(cache)
    assert code == 0


def test_shell_stub_is_red(tmp_path):
    cache = _cache_with(tmp_path, "mishe-tape", b"#!/bin/sh\necho mishe-tape\n")
    code, lines = mod.evaluate(cache)
    assert code == 2
    assert "mishe-tape is not ELF" in lines[0]


def test_unrelated_names_are_ignored(tmp_path):
    cache = _cache_with(tmp_path, "build", b"not a binary")
    code, _ = mod.evaluate(cache)
    assert code == 0


def test_missing_cache_is_unknown(tmp_path):
    code, _ = mod.evaluate(tmp_path / "absent")
    assert code == 3
