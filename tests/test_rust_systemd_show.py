"""Golden test: mishe-systemd-show against answers recorded from the retired Python decoder.

The Python decoder the Rust binary replaced is gone; its answers for CASES were
recorded once into GOLDEN (a literal per case) and the decoder copy was deleted, so
there is one implementation (the binary) and no shadow. The binary is found through
MISHE_SYSTEMD_SHOW_BIN or the site install; if neither resolves, the golden tests are
skipped with the reason, so a checkout without a built binary still runs the rest.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import mishe_tauftauf.unit_psi_text as text_mod
from mishe_tauftauf.unit_psi_text import SystemdShowUnknown, parse_systemd_show


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
# Answers recorded from the retired Python decoder (line split on str.splitlines, first '='
# splits key from value, empty keys dropped, last duplicate wins in dict form) for CASES[i].
GOLDEN = [
    {},
    {"ActiveState": "active", "SubState": "running", "LoadState": "loaded", "UnitFileState": "enabled"},
    {"ActiveState": "active"},
    {"Description": "a=b=c", "Empty": ""},
    {"Key With Space": "ok"},
    {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5", "F": "6", "G": "7", "H": "8", "I": "9", "J": "10", "K": "11"},
    {"Unicode": "тест ✓ 日本", "Z": "ok"},
    {"Trailing": "no newline"},
]


@needs_binary
@pytest.mark.parametrize("index", range(len(CASES)))
def test_binary_matches_recorded_golden(monkeypatch, index):
    monkeypatch.setenv("MISHE_SYSTEMD_SHOW_BIN", BINARY)
    assert parse_systemd_show(CASES[index]) == GOLDEN[index]


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


@pytest.mark.parametrize("record_text", ["[]", "1", '"sha"', "null"])
def test_site_install_record_that_is_not_an_object_is_unknown(tmp_path, monkeypatch, record_text):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "mishe-systemd-show"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    (bindir / "mishe-systemd-show.install.json").write_text(record_text, encoding="utf-8")
    monkeypatch.delenv("MISHE_SYSTEMD_SHOW_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(SystemdShowUnknown, match="not a JSON object"):
        text_mod._systemd_show_binary()
