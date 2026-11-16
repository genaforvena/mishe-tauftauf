"""Reader tests for ``mishe-unit-paths``.

The binary's golden answers live in ``tests/test_rust_unit_paths.py``; these tests
cover the reader around it: resolution fails closed, a non-zero exit or an answer
outside the stdout contract is UNKNOWN, and the binary's DEFER line routes the
original text to the Python boundary instead of being returned as a path.
Binary cases SKIP when the binary is absent, never pass silently.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import mishe_tauftauf.unit_paths as paths_mod
from mishe_tauftauf.unit_paths import UnitPathsUnknown, declared_paths
from mishe_tauftauf.unit_properties import declared_paths as python_declared_paths


def _resolve() -> str | None:
    """Resolve the binary, fail closed; never return an unverified path."""
    override = os.environ.get("MISHE_UNIT_PATHS_BIN")
    if override:
        path = Path(override)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    site = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not site:
        return None
    binary = Path(site) / "bin" / "mishe-unit-paths"
    record = binary.with_name(binary.name + ".install.json")
    if not binary.is_file() or not record.is_file():
        return None
    try:
        expected = json.loads(record.read_text(encoding="utf-8"))["sha256"]
    except (ValueError, KeyError, OSError, TypeError):
        return None
    if not isinstance(expected, str) or hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        return None
    return str(binary)


BINARY = _resolve()
needs_binary = pytest.mark.skipif(
    BINARY is None,
    reason="mishe-unit-paths not built: set MISHE_UNIT_PATHS_BIN (cargo build --release --locked in rust/mishe-unit-paths)",
)

CASES = [
    ("ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /home/host/tool run }\n"
     "WorkingDirectory=/home/host/work\nEnvironment=PATH=/usr/bin:/bin\n",
     ["/usr/bin/python3", "/home/host/tool", "/home/host/work", "/usr/bin", "/bin"]),
    ("Environment=RELATIVE=./tool some/where/tool\n", []),
    ("", []),
]

DEFER_TEXT = "Environment=X=\u00e9/home/u/site/tool\n"


def _fake_binary(tmp_path: Path, body: str) -> str:
    fake = tmp_path / "mishe-unit-paths"
    fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    fake.chmod(0o755)
    return str(fake)


@needs_binary
@pytest.mark.parametrize("text, expected", CASES)
def test_binary_answer_is_returned_in_order(monkeypatch: pytest.MonkeyPatch, text, expected) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", BINARY)
    assert declared_paths(text) == expected


@needs_binary
def test_binary_defers_non_ascii_to_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", BINARY)
    assert declared_paths(DEFER_TEXT) == python_declared_paths(DEFER_TEXT)


def test_defer_line_routes_to_python_not_to_the_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake_binary(tmp_path, "printf 'DEFER\\n'\n")
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", fake)
    expected = python_declared_paths(DEFER_TEXT)
    assert expected == []  # Python's Unicode-aware \w suppresses the path after 'é'.
    assert declared_paths(DEFER_TEXT) == expected


def test_empty_result_is_no_paths_not_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", _fake_binary(tmp_path, "exit 0\n"))
    assert declared_paths("Environment=RELATIVE=./tool\n") == []


def test_explicit_binary_wins_over_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake_binary(tmp_path, "printf '/opt/tool\\n'\n")
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", "/nonexistent/mishe-unit-paths")
    assert declared_paths("ExecStart=/opt/tool\n", binary=fake) == ["/opt/tool"]


def test_missing_override_is_unknown_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", "/nonexistent/mishe-unit-paths")
    with pytest.raises(UnitPathsUnknown):
        declared_paths("ExecStart=/opt/tool\n")


def test_non_executable_override_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "mishe-unit-paths"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", str(fake))
    with pytest.raises(UnitPathsUnknown, match="not an executable file"):
        declared_paths("ExecStart=/opt/tool\n")


def test_no_site_and_no_override_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISHE_UNIT_PATHS_BIN", raising=False)
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)
    with pytest.raises(UnitPathsUnknown):
        paths_mod.resolve_binary()


def test_site_install_with_bad_sha_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "mishe-unit-paths"
    binary.parent.mkdir()
    binary.write_bytes(b"\x7fELF not really")
    binary.chmod(0o755)
    (binary.parent / "mishe-unit-paths.install.json").write_text(
        json.dumps({"sha256": "deadbeef"}), encoding="utf-8")
    monkeypatch.delenv("MISHE_UNIT_PATHS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(UnitPathsUnknown, match="does not match"):
        paths_mod.resolve_binary()


@pytest.mark.parametrize("record_text", ["[]", "1", '"sha"', "null"])
def test_site_install_record_that_is_not_an_object_is_unknown(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path, record_text) -> None:
    binary = tmp_path / "bin" / "mishe-unit-paths"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    (binary.parent / "mishe-unit-paths.install.json").write_text(record_text, encoding="utf-8")
    monkeypatch.delenv("MISHE_UNIT_PATHS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(UnitPathsUnknown, match="not a JSON object"):
        paths_mod.resolve_binary()


def test_failing_binary_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", _fake_binary(tmp_path, "exit 2\n"))
    with pytest.raises(UnitPathsUnknown, match="exit 2"):
        declared_paths("ExecStart=/opt/tool\n")


@pytest.mark.parametrize("output", ["relative/path\\n", "/opt/tool\\n\\n", "DEFER\\n/opt/tool\\n"])
def test_out_of_contract_output_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", _fake_binary(tmp_path, f"printf '{output}'\n"))
    with pytest.raises(UnitPathsUnknown, match="outside contract"):
        declared_paths("ExecStart=/opt/tool\n")


def test_non_utf8_output_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", _fake_binary(tmp_path, "printf '\\377\\n'\n"))
    with pytest.raises(UnitPathsUnknown, match="not UTF-8"):
        declared_paths("ExecStart=/opt/tool\n")
