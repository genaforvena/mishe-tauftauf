#!/usr/bin/env bash
# Build the Rust components a fresh clone needs and install them into the site.
#
# The Python resolvers (feed._tape_binary, feed._records_binary, ...) read
# $SITE/bin/NAME and fail closed unless NAME.install.json holds the sha256 of
# those exact bytes. Nothing in git carries the built binaries, so this script
# is the bootstrap step: locked release build per crate, then an atomic copy and
# an install record. Run it once after `mishe init` (and again after a Rust change).
#
# Usage: tools/install-rust-binaries.sh [SITE_HOME]
#   SITE_HOME defaults to $MISHE_SEED_HOME, then $MISHE_TAUFTAUF_HOME, then ./.mishe-tauftauf
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
site="${1:-${MISHE_SEED_HOME:-${MISHE_TAUFTAUF_HOME:-$repo/.mishe-tauftauf}}}"
cargo_bin="${MISHE_CARGO:-$(command -v cargo || true)}"
# A relative MISHE_CARGO would stop resolving after the per-crate `cd` below: resolve it now, against the caller's cwd.
case "$cargo_bin" in
  */*) [ -n "$cargo_bin" ] && cargo_bin="$(cd "$(dirname "$cargo_bin")" && pwd)/$(basename "$cargo_bin")" ;;
  ?*) cargo_bin="$(command -v "$cargo_bin" || true)" ;;
esac
if [ -z "$cargo_bin" ] && [ -x "$HOME/.cargo/bin/cargo" ]; then
  cargo_bin="$HOME/.cargo/bin/cargo"
fi
if [ -z "$cargo_bin" ]; then
  echo "install-rust-binaries: cargo not found; install rustup (toolchain pinned by rust-toolchain.toml)" >&2
  exit 1
fi
if [ ! -d "$site" ]; then
  echo "install-rust-binaries: site $site does not exist; run 'mishe init' first" >&2
  exit 1
fi

# crate directory under rust/ : installed binary name
components="mishe-tape:mishe-tape
mishe-tape-facts:mishe-tape-facts
mishe-records:mishe-records
mishe-systemd-show:mishe-systemd-show
mishe-signal-freshness:mishe-signal-freshness
mishe-unit-paths:mishe-unit-paths
mishe-signal-samples:mishe-signal-sample
mishe-cgroup-counters:mishe-cgroup-counters"

# Build output goes off the root filesystem: MISHE_BUILD_CACHE wins, then the scratch
# mount when it is a writable mountpoint, else the site cache. Same policy as delivery_check.py.
if [ -n "${MISHE_BUILD_CACHE:-}" ]; then
  cache="$MISHE_BUILD_CACHE"
elif mountpoint -q /mnt/mishe-scratch 2>/dev/null && [ -w /mnt/mishe-scratch ]; then
  cache=/mnt/mishe-scratch/mishe-cargo-target
else
  cache="$site/cache/cargo-target"
fi
mkdir -p "$cache"

commit="$(git -C "$repo" rev-parse HEAD)"
bin_dir="$site/bin"
mkdir -p "$bin_dir"

# Build caps from docs/rust-runtime-migration-plan.md §6.2: two jobs, nice 19 and a
# transient cgroup memory cap. Cargo's default -j is one job per online CPU, and a
# 2026-11-17 07:06Z release build at that default drove the host into a root-fs stall.
build_jobs="${MISHE_BUILD_JOBS:-2}"
build_wrap=()
if command -v systemd-run >/dev/null 2>&1 &&
   systemd-run --user --quiet --scope -p MemoryMax=2G true >/dev/null 2>&1; then
  build_wrap=(systemd-run --user --quiet --scope -p MemoryMax=2G nice -n 19)
elif command -v nice >/dev/null 2>&1; then
  build_wrap=(nice -n 19)
fi

while IFS=: read -r crate binary; do
  crate_dir="$repo/rust/$crate"
  echo "build $crate -> $binary"
  # Build from the crate directory: rustup resolves rust-toolchain.toml from cwd,
  # so --manifest-path from the caller's cwd would build with the default toolchain.
  (cd "$crate_dir" && "${build_wrap[@]}" "$cargo_bin" build --release --locked \
     --jobs "$build_jobs" --target-dir "$cache/$crate")
  built="$cache/$crate/release/$binary"
  if [ ! -x "$built" ]; then
    echo "install-rust-binaries: build produced no $built" >&2
    exit 1
  fi
  tmp="$bin_dir/.$binary.tmp.$$"
  cp "$built" "$tmp"
  chmod 0755 "$tmp"
  mv -f "$tmp" "$bin_dir/$binary"
  digest="$(sha256sum "$bin_dir/$binary" | cut -d' ' -f1)"
  printf '{"binary":"%s","sha256":"%s","source_commit":"%s","built":"%s","installed_by":"tools/install-rust-binaries.sh"}\n' \
    "$binary" "$digest" "$commit" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$bin_dir/$binary.install.json.tmp"
  mv -f "$bin_dir/$binary.install.json.tmp" "$bin_dir/$binary.install.json"
  echo "installed $bin_dir/$binary sha256=$digest"
done <<EOF
$components
EOF
