"""The bootstrap builds each crate off the root filesystem and from its own dir.

A stub cargo records cwd and argv and writes the expected binary under the
--target-dir it is given, so the script is exercised end to end without a real build.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "install-rust-binaries.sh"

STUB = r'''#!/usr/bin/env bash
set -euo pipefail
log="$STUB_LOG"
printf 'cwd=%s argv=%s\n' "$PWD" "$*" >> "$log"
target=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--target-dir" ]; then target="$arg"; fi
  prev="$arg"
done
[ -n "$target" ] || { echo "stub: no --target-dir" >&2; exit 2; }
mkdir -p "$target/release"
# every installed binary name (the bin name can differ from the crate dir, e.g. signal-samples)
for bin in mishe-tape mishe-tape-facts mishe-records mishe-systemd-show mishe-signal-freshness mishe-signal-sample; do
  printf '#!/bin/sh\necho %s\n' "$bin" > "$target/release/$bin"
  chmod 0755 "$target/release/$bin"
done
'''


@pytest.fixture
def stub_env(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash required")
    stub = tmp_path / "cargo"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    site = tmp_path / "site"
    site.mkdir()
    cache = tmp_path / "cache"
    env = dict(os.environ)
    env.update(
        MISHE_CARGO=str(stub),
        MISHE_BUILD_CACHE=str(cache),
        STUB_LOG=str(tmp_path / "stub.log"),
    )
    return env, site, cache, tmp_path / "stub.log"


def run_script(env, site, cwd):
    return subprocess.run(
        ["bash", str(SCRIPT), str(site)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_builds_into_cache_from_crate_dir_and_installs(stub_env):
    env, site, cache, log = stub_env
    result = run_script(env, site, cwd=REPO)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert len(lines) == 6
    for line in lines:
        cwd, argv = line.split(" argv=")
        crate = Path(cwd.removeprefix("cwd=")).name
        assert Path(cwd.removeprefix("cwd=")).parent == REPO / "rust"
        assert argv == f"build --release --locked --target-dir {cache / crate}"
    for binary in ["mishe-tape", "mishe-records", "mishe-signal-sample"]:
        assert (site / "bin" / binary).exists()
        assert (site / "bin" / f"{binary}.install.json").exists()
    # the build output lands in the cache, not in the crate's own target dir
    assert (cache / "mishe-tape" / "release" / "mishe-tape").exists()


def test_relative_cargo_resolves_from_caller_cwd(stub_env, tmp_path):
    env, site, cache, log = stub_env
    stub = Path(env["MISHE_CARGO"])
    rel_dir = tmp_path / "bin"
    rel_dir.mkdir()
    shutil.move(str(stub), rel_dir / "cargo")
    env["MISHE_CARGO"] = "./cargo"
    result = run_script(env, site, cwd=rel_dir)
    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == 6

