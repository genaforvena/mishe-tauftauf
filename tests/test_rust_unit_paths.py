"""Golden test for the future `mishe-unit-paths` binary.

The binary is the authoritative decoder for the paths a unit's `systemctl show`
text declares; it replaces the Python answer recorded here. When the binary is
installed (explicit `MISHE_UNIT_PATHS_BIN` override, or a sha256-pinned site
install) the binary cases run and require byte-exact agreement with GOLDEN.
When it is absent they SKIP — never silently pass — while the Python golden
assertion still runs. This is the same shape as `tests/test_rust_systemd_show.py`.

The binary defers any input containing a non-ASCII byte: Python's `\\w` and `\\s`
are Unicode-aware where the Rust decoder is ASCII-only, so those inputs cannot
be decided byte-exactly by the binary. Deferred input is answered by the Python
decoder in the reader; the DEFER line is a decision to route, not a result.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf.unit_properties import declared_paths


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
needs_binary = pytest.mark.skipif(BINARY is None, reason="mishe-unit-paths binary not installed")

# Inputs the binary must decode byte-exactly. All ASCII; no DEFER arm applies.
CASES = [
    # systemd's three renderings in one show output.
    ("ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
     "/home/host/scripts/tool follow ; ignore_errors=no }\n"
     "WorkingDirectory=/home/host/work\n"
     "Environment=PATH=/usr/bin:/bin MESH_HOME=/home/host/state\n"),
    # Duplicate kept once, first position retained.
    "ExecStart={ path=/opt/a/tool ; argv[]=/opt/a/tool run }\nEnvironment=TOOL=/opt/a/tool",
    # Relative values are not absolute paths.
    "Environment=RELATIVE=./tool\nEnvironment=RELATIVE=some/where/tool\n",
    # Rendering punctuation ends a path; trailing '.'/',' trimmed.
    "ExecStart={ path=/opt/tool ; argv[]=/opt/tool \"quoted arg\" }\n"
    "Environment=PATH=/usr/bin:/usr/local/bin,\n",
    # Empty and separator-only text.
    "",
    # Word chars, '.', '-' and '/' before a slash suppress a false start.
    "Environment=X=1/home/a\nEnvironment=X=_/home/a\nEnvironment=X=-/home/a\nEnvironment=X=./home/a\n",
]

# Recorded from the authoritative Python decoder for CASES[i].
GOLDEN = [
    ["/usr/bin/python3", "/home/host/scripts/tool", "/home/host/work",
     "/usr/bin", "/bin", "/home/host/state"],
    ["/opt/a/tool"],
    [],
    ["/opt/tool", "/usr/bin", "/usr/local/bin"],
    [],
    [],
]

# Inputs containing a non-ASCII byte. The binary must not guess: it emits the
# single DEFER line and the reader asks the Python decoder instead.
DEFER_CASES = [
    "Environment=X=\u00e9/home/u/site/tool\n",       # Unicode \w before '/' suppresses the path in Python
    "Environment=X=/home/u/site/\u2028tool\n",        # Unicode \s ends the path in Python
    "Environment=X=/home/\u00fc/site/tool\n",         # non-ASCII byte anywhere
]


def test_python_golden_answers_match_recorded() -> None:
    for case, golden in zip(CASES, GOLDEN):
        assert declared_paths(case) == golden


@needs_binary
@pytest.mark.parametrize("index", range(len(CASES)))
def test_binary_matches_recorded_golden(index: int) -> None:
    done = subprocess.run([BINARY], input=CASES[index].encode(), capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout.decode().splitlines() == GOLDEN[index]


@needs_binary
@pytest.mark.parametrize("index", range(len(DEFER_CASES)))
def test_binary_defers_non_ascii_input(index: int) -> None:
    done = subprocess.run([BINARY], input=DEFER_CASES[index].encode(), capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout == b"DEFER\n"


def test_missing_override_is_never_a_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", "/nonexistent/mishe-unit-paths")
    assert _resolve() is None


def test_non_executable_override_is_never_a_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "mishe-unit-paths"
    fake.write_text("not executable")
    monkeypatch.setenv("MISHE_UNIT_PATHS_BIN", str(fake))
    assert _resolve() is None


def test_site_install_with_bad_sha_is_never_a_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "mishe-unit-paths"
    binary.parent.mkdir()
    binary.write_bytes(b"\x7fELF not really")
    binary.chmod(0o755)
    (tmp_path / "bin" / "mishe-unit-paths.install.json").write_text('{"sha256": "deadbeef"}')
    monkeypatch.delenv("MISHE_UNIT_PATHS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    assert _resolve() is None
