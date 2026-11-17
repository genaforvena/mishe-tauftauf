"""checks/component-verify.py: a promoted component's ledger and its live install must agree.

The check closes the two gaps docs/component-deploy.md names: nothing ran
``components.verify`` on a schedule, and the ``$SITE/bin/NAME`` install (which
consumers still read before step 3) was never compared with the promoted bytes.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("component_verify", REPO / "checks/component-verify.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NAME = "tool-x"
SHA = "cc071b625049f1770778a5529a2bbd03d63169ef"


def component(home: Path, payload: bytes = b"#!/bin/sh\necho one\n") -> Path:
    """Create components/NAME/releases/SHA/{bin/NAME,manifest.json} and point current at it."""
    release = home / "components" / NAME / "releases" / SHA
    (release / "bin").mkdir(parents=True)
    binary = release / "bin" / NAME
    binary.write_bytes(payload)
    binary.chmod(0o755)
    (release / "manifest.json").write_text(json.dumps({
        "name": NAME, "sha": SHA,
        "bin_sha256": hashlib.sha256(payload).hexdigest(),
    }))
    (home / "components" / NAME / "current").symlink_to(Path("releases") / SHA)
    return binary


def install(home: Path, payload: bytes) -> None:
    (home / "bin").mkdir(parents=True, exist_ok=True)
    binary = home / "bin" / NAME
    binary.write_bytes(payload)
    binary.chmod(0o755)


def test_green_when_install_matches_promoted_bytes(tmp_path):
    component(tmp_path)
    install(tmp_path, b"#!/bin/sh\necho one\n")
    code, lines = check.evaluate(tmp_path, REPO)
    assert code == 0, lines
    assert "install matches" in lines[0]


def test_green_without_an_install(tmp_path):
    component(tmp_path)
    code, lines = check.evaluate(tmp_path, REPO)
    assert code == 0, lines
    assert "no $SITE/bin install" in lines[0]


def test_red_when_the_install_drifted_from_the_promoted_bytes(tmp_path):
    component(tmp_path)
    install(tmp_path, b"#!/bin/sh\necho two\n")
    code, lines = check.evaluate(tmp_path, REPO)
    assert code == 2, lines
    assert any("install drift" in line for line in lines)


def test_red_when_the_promoted_bytes_are_tampered(tmp_path):
    binary = component(tmp_path)
    binary.write_bytes(b"#!/bin/sh\necho tampered\n")
    code, lines = check.evaluate(tmp_path, REPO)
    assert code == 2, lines
    assert any("sha256" in line for line in lines)


def test_unknown_without_a_components_directory(tmp_path):
    code, lines = check.evaluate(tmp_path, REPO)
    assert code == 3, lines
    assert "UNKNOWN" in lines[0]
