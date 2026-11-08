#!/bin/sh
# Install tools/post-commit-push.sh as this checkout's post-commit hook (symlink).
# Refuses to replace a post-commit hook it did not install.
set -eu

root=$(git rev-parse --show-toplevel)
src="$root/tools/post-commit-push.sh"
dst="$(git rev-parse --git-path hooks)/post-commit"
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
