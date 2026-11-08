#!/bin/sh
# Post-commit hook body: push main to origin after every commit, in the background.
# Installed as .git/hooks/post-commit by tools/install-post-commit-push.sh (a symlink to this file).
# Never blocks or fails a commit: it always exits 0, skips when HEAD is not on main, and bounds the push
# with a timeout. Outcomes append one line to .mishe-tauftauf/push-hook.log (gitignored site directory).
# A failed push is visible in that log and to checks/push-lag.py (local main ahead of origin/main).
set -u

branch=$(git symbolic-ref --short -q HEAD 2>/dev/null) || exit 0
[ "$branch" = main ] || exit 0
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
log="$root/.mishe-tauftauf/push-hook.log"
mkdir -p "$(dirname "$log")" 2>/dev/null || true

(
	stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
	out=$(nice -n 10 timeout 120 git -C "$root" push --quiet origin main 2>&1)
	rc=$?
	if [ "$rc" -eq 0 ]; then
		echo "$stamp ok $(git -C "$root" rev-parse --short HEAD)" >> "$log"
	else
		echo "$stamp FAILED rc=$rc $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)" >> "$log"
	fi
) >/dev/null 2>&1 &
exit 0
