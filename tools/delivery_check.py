#!/usr/bin/env python3
"""The deterministic delivery check: build every Rust binary from this tree, then run the whole test suite on it.

Run with the release tree as the working directory (``mishe release advance`` does).  Each crate under
``rust/`` is built from its committed bytes (locked, offline, capped in memory and priority) and exported to
the tests as ``MISHE_<BINARY>_BIN``: the binary name without its ``mishe-`` prefix, upper-cased, dashes to
underscores (mishe-tape -> MISHE_TAPE_BIN, mishe-signal-sample -> MISHE_SIGNAL_SAMPLE_BIN).  A crate that
does not build fails the check: the suite must never run against a stale or missing binary.

Builds go to a shared target directory so an unchanged crate is reused between deliveries.  The
release build is fsync-heavy and blocks root fsync for seconds while it runs
(``checks/root-fs-stall.py``), so the target dir lives off the root filesystem when the scratch mount
is a separate writable filesystem (the same convention as ``operator/dual_probe.py``): the scratch
mount's ``mishe-cargo-target``, else ``MISHE_SEED_HOME/cache/cargo-target``, with ``MISHE_BUILD_CACHE``
overriding both.  Exit status is the suite's, or 2 when a build fails.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from mishe_tauftauf import release_boundary  # noqa: E402

SCRATCH_MOUNT = Path("/mnt/mishe-scratch")


def binaries(crate_dir: Path) -> list[str]:
    return release_boundary.crate_binaries((crate_dir / "Cargo.toml").read_text(encoding="utf-8"))


def env_name(binary: str) -> str:
    return release_boundary.binary_env_name(binary)


def build(crate_dir: Path, target: Path, cargo: str) -> None:
    command = [cargo, "build", "--release", "--locked", "--offline", "--jobs", "2",
               "--manifest-path", str(crate_dir / "Cargo.toml"), "--target-dir", str(target)]
    if shutil.which("systemd-run"):
        command = ["systemd-run", "--user", "--quiet", "--scope", "-p", "MemoryMax=2G", "nice", "-n", "19", *command]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=1500)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"build of {crate_dir.name} timed out after 1500 s\n")
        raise SystemExit(2)
    if done.returncode != 0:
        sys.stderr.write(f"build of {crate_dir.name} failed ({done.returncode}):\n{done.stderr[-2000:]}\n")
        raise SystemExit(2)


def build_cache(root: Path, home: str | None) -> Path:
    """Where the shared cargo target dirs live.

    ``MISHE_BUILD_CACHE`` wins; otherwise the scratch mount when it is a separate, writable
    filesystem (a multi-second root fsync stall while a build runs would otherwise recur); otherwise
    the site cache, or ``root/target-delivery-check`` when the site home is unset.
    """
    override = os.environ.get("MISHE_BUILD_CACHE")
    if override:
        return Path(override)
    if home is None:
        return root / "target-delivery-check"
    if os.path.ismount(SCRATCH_MOUNT) and os.access(SCRATCH_MOUNT, os.W_OK):
        return SCRATCH_MOUNT / "mishe-cargo-target"
    return Path(home) / "cache" / "cargo-target"


def main() -> int:
    root = Path.cwd()
    home = os.environ.get("MISHE_SEED_HOME")
    cache = build_cache(root, home)
    cargo = os.environ.get("CARGO") or shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    env = dict(os.environ, PYTHONPATH="src")
    for crate in sorted((root / "rust").glob("*/Cargo.toml")):
        crate_dir = crate.parent
        target = cache / crate_dir.name
        build(crate_dir, target, cargo)
        for binary in binaries(crate_dir):
            built = target / "release" / binary
            if not built.is_file():
                sys.stderr.write(f"{crate_dir.name} built no {binary} at {built}\n")
                return 2
            env[env_name(binary)] = str(built)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
