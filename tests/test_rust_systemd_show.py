"""Differential test: mishe-systemd-show against the retired Python decoder.

The Python reference below is the decoder the Rust binary replaces, kept here only
as the oracle. The binary is found through MISHE_SYSTEMD_SHOW_BIN or the site
install; if neither resolves, the differential tests are skipped with the reason,
so a checkout without a built binary still runs the rest of the suite.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import mishe_tauftauf.unit_psi_text as text_mod
from mishe_tauftauf.unit_psi_text import SystemdShowUnknown, parse_systemd_show


def _python_reference(raw: str) -> dict[str, str]:
    """The retired Python decoder, verbatim in behaviour (oracle only)."""
    props: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            props[key] = value
    return props


def _resolve() -> str | None:
    override = os.environ.get("MISHE_SYSTEMD_SHOW_BIN")
    if override:
        return override if os.access(override, os.X_OK) else None
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if home and os.access(Path(home) / "bin" / "mishe-systemd-show", os.X_OK):
        return str(Path(home) / "bin" / "mishe-systemd-show")
    return None


BINARY = _resolve()
needs_binary = pytest.mark.skipif(
    BINARY is None,
    reason="mishe-systemd-show not built: set MISHE_SYSTEMD_SHOW_BIN (cargo build --release --locked in rust/mishe-systemd-show)",
)

CASES = [
    "",
    "ActiveState=active\nSubState=running\nLoadState=loaded\nUnitFileState=enabled\n",
    "ActiveState=inactive\nActiveState=active\n",
    "Description=a=b=c\nEmpty=\n",
    "no separator here\n=orphan\n\n   \nKey With Space=ok\n",
    "A=1\r\nB=2\rC=3 D=4 E=5\x0bF=6\x0cG=7\x1cH=8\x1dI=9\x1eJ=10\x85K=11",
    "Unicode=тест ✓ 日本\nZ=ok\n",
    "Trailing=no newline",
]


@needs_binary
@pytest.mark.parametrize("raw", CASES)
def test_binary_matches_python_reference(monkeypatch, raw):
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", BINARY)
    assert parse_systemd_show(raw) == _python_reference(raw)


@needs_binary
def test_binary_keeps_first_position_for_duplicates(monkeypatch):
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", BINARY)
    assert list(parse_systemd_show("B=1\nA=2\nB=3\n")) == ["B", "A"]


def test_missing_override_is_unknown_not_empty(monkeypatch):
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", "/nonexistent/mishe-systemd-show")
    with pytest.raises(SystemdShowUnknown):
        parse_systemd_show("A=1\n")


def test_failing_binary_is_unknown(monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    fake.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", str(fake))
    with pytest.raises(SystemdShowUnknown, match="exit 2"):
        parse_systemd_show("A=1\n")


def test_out_of_contract_output_is_unknown(monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    fake.write_text("#!/bin/sh\nprintf 'bare line\\n'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", str(fake))
    with pytest.raises(SystemdShowUnknown, match="outside its contract"):
        parse_systemd_show("A=1\n")


def test_no_site_and_no_override_is_unknown(monkeypatch):
    monkeypatch.delenv("MISHE_SYSTEMD_SHOW_BIN", raising=False)
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)
    with pytest.raises(SystemdShowUnknown):
        text_mod._systemd_show_binary()
