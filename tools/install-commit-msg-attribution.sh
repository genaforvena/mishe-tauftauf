#!/bin/sh
# Install tools/commit-msg-attribution.py as this checkout's commit-msg hook (symlink).
# Refuses to replace a commit-msg hook it did not install. Mirrors
# tools/install-post-commit-push.sh.
set -eu

root=$(git rev-parse --show-toplevel)
src="$root/tools/commit-msg-attribution.py"
dst="$(git rev-parse --git-path hooks)/commit-msg"
case "$dst" in /*) ;; *) dst="$root/$dst" ;; esac

[ -x "$src" ] || chmod +x "$src"

if [ -e "$dst" ] || [ -L "$dst" ]; then
	if [ "$(readlink -f "$dst" 2>/dev/null || true)" != "$(readlink -f "$src")" ]; then
		echo "refusing: $dst exists and is not this hook" >&2
		exit 1
	fi
else
	mkdir -p "$(dirname "$dst")"
fi

ln -sfn "$src" "$dst"
echo "installed: $dst -> $src"
