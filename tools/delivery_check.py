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
overriding both.  The suite's own temp follows the same rule (``suite_temp``): it is exported as
``TMPDIR``, so pytest's per-run numbered base temp (``pytest-of-<user>/pytest-N``, with its own
retention) lands on the scratch mount's ``mishe-test-tmp``, else the environment default, with
``MISHE_TEST_TMPDIR`` overriding.  Exit status is the suite's, or 2 when a build fails.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from mishe_tauftauf import release_boundary  # noqa: E402

SCRATCH_MOUNT = Path("/mnt/mishe-scratch")
SUITE_DURATIONS = 25
"""Report the 25 slowest tests in the retained check output.

The check's output is stored in the release record (``check.output``) and copied to the
review log, so every promotion carries a comparable per-test time-series without a
separate measurement run. The promotion-level check-stage timestamp is too coarse to
resolve individual test trims (see analysis/check-stage-durations-20261119.md).
"""


def scratch_mount() -> Path | None:
    """The scratch mount when it is a separate, writable filesystem, else None."""
    if os.path.ismount(SCRATCH_MOUNT) and os.access(SCRATCH_MOUNT, os.W_OK):
        return SCRATCH_MOUNT
    return None


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


def is_elf(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(4) == b"\x7fELF"


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
    scratch = scratch_mount()
    if scratch is not None:
        return scratch / "mishe-cargo-target"
    return Path(home) / "cache" / "cargo-target"


def suite_temp() -> Path | None:
    """Where the delivery test run's temp files live, or None to keep the default.

    One suite run builds tens of thousands of ``tmp_path`` entries.  By default
    they land in the root filesystem's temp directory, where that churn joins the
    root-fsync pressure ``checks/root-fs-stall.py`` surfaces and, because the
    suite runs are concurrent enough that pytest's own retention never fires,
    it accumulates (2026-11-16: every multi-second stall that day landed inside a
    moving-pin delivery, and ``/tmp`` held 14 GB of stale pytest runs).  Point
    ``TMPDIR`` at the scratch mount, the same convention as the build cache; the
    default is kept when there is no separate writable scratch filesystem.

    The caller must set ``TMPDIR`` only and must *not* pass ``--basetemp``: an
    explicit base temp is a single directory pytest empties at every session
    start (``_pytest.tmpdir`` ``rm_rf``), so concurrent delivery runs would
    delete each other's ``tmp_path`` trees mid-run.  Without it pytest numbers a
    fresh one per run under ``TMPDIR`` and keeps its own retention.
    """
    override = os.environ.get("MISHE_TEST_TMPDIR")
    if override:
        return Path(override)
    scratch = scratch_mount()
    return scratch / "mishe-test-tmp" if scratch is not None else None


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
            if not is_elf(built):
                # A cargo build that reports success can leave a text stub in place when cargo's fingerprint
                # is fresh (2026-11-16: a shell stub in the shared cache poisoned the 08547645 check). Fail closed.
                sys.stderr.write(f"{built} is not an ELF binary; refusing to test against it\n")
                return 2
            env[env_name(binary)] = str(built)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--durations", str(SUITE_DURATIONS)]
    temp = suite_temp()
    if temp is not None:
        temp.mkdir(parents=True, exist_ok=True)
        env["TMPDIR"] = str(temp)
    argv.append("tests")
    return subprocess.run(argv, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
