#!/usr/bin/env python3
"""Detector: the shared cargo build cache holds only real binaries under release/.

tests/test_install_rust_binaries.py stands in a fake cargo that writes 26-40 byte shell scripts
named after the installed binaries into --target-dir/release. If such a test ever writes to the
shared cache (the scratch mount), those stubs sit where a real build's output belongs, and a
delivery check that builds from that cache fails with a malformed framer (2026-11-16, 08547645).

Exit status:
  0  every installed binary name under <cache>/*/release/ is an ELF file (or absent)
  2  at least one such file is not an ELF file: a stub or other non-binary output
  3  undecidable: the cache root cannot be read
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_CACHE = Path("/mnt/mishe-scratch/mishe-cargo-target")
BINARY_NAMES = {
    "mishe-tape", "mishe-tape-facts", "mishe-records", "mishe-systemd-show",
    "mishe-signal-sample",
    "mishe-unit-paths", "mishe-cgroup-counters",
}
ELF_MAGIC = b"\x7fELF"


def resolve_cache(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    return Path(os.environ.get("MISHE_BUILD_CACHE") or DEFAULT_CACHE)


def evaluate(cache: Path) -> tuple[int, list[str]]:
    if not cache.is_dir():
        return 3, [f"cache root {cache} is not a directory"]
    bad: list[str] = []
    try:
        crate_dirs = sorted(p for p in cache.iterdir() if p.is_dir())
    except OSError as exc:
        return 3, [f"cannot list {cache}: {exc}"]
    for crate in crate_dirs:
        release = crate / "release"
        if not release.is_dir():
            continue
        # a stub may carry the crate dir name (crate mishe-signal-samples, bin mishe-signal-sample),
        # so the crate's own name is checked alongside the installed binary names
        for name in sorted(BINARY_NAMES | {crate.name}):
            path = release / name
            if not path.is_file() or path.is_symlink():
                continue
            try:
                with path.open("rb") as fh:
                    magic = fh.read(4)
                size = path.stat().st_size
            except OSError as exc:
                return 3, [f"cannot read {path}: {exc}"]
            if magic != ELF_MAGIC:
                bad.append(f"{path} is not ELF ({size} bytes)")
    if bad:
        return 2, bad
    return 0, [f"every installed binary under {cache} is ELF or absent"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", default=None, help="cargo target cache root (default: MISHE_BUILD_CACHE or the scratch mount)")
    args = parser.parse_args(argv)
    code, lines = evaluate(resolve_cache(args.cache))
    status = {0: "GREEN", 2: "RED", 3: "UNKNOWN"}[code]
    print(f"build-cache-stubs: {status}")
    for line in lines:
        print(f"  {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
