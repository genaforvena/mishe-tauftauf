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

committed=$(git -C "$root" rev-parse HEAD 2>/dev/null) || exit 0

# A commit made in the last ~30 s of a mind's wake must still be published: a background job started
# here stays in the committing wake's cgroup, and systemd tears that cgroup down when the wake ends,
# killing the settle sleep and the push. Re-run this script as a transient user unit (outside the wake)
# and let that unit do the settle and push. Falls back to the in-process job when systemd-run cannot
# start the unit, so a commit is never left unpublished for lack of the manager.
if [ -z "${MISHE_PUSH_DETACHED:-}" ] && command -v systemd-run >/dev/null 2>&1; then
	self=$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")
	if systemd-run --user --collect --quiet --slice=mishe.slice --working-directory="$root" \
		--unit="mishe-push-$(printf '%s' "$committed" | cut -c1-12)-$$" \
		--setenv=MISHE_PUSH_DETACHED=1 \
		${MISHE_PUSH_SETTLE_S:+--setenv=MISHE_PUSH_SETTLE_S="$MISHE_PUSH_SETTLE_S"} \
		/bin/sh "$self" >/dev/null 2>&1; then
		exit 0
	fi
	echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) detach-fallback systemd-run failed; pushing in-process" >> "$log"
fi

publish() {
	stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
	# Settle first: an amend within the window (a trailer fix, say) must not publish the superseded
	# commit, which then leaves local main diverged from origin. Push only the commit still at HEAD.
	sleep "${MISHE_PUSH_SETTLE_S:-30}"
	now=$(git -C "$root" rev-parse HEAD 2>/dev/null)
	if [ "$now" != "$committed" ]; then
		echo "$stamp skipped superseded $(printf '%s' "$committed" | cut -c1-8) head=$(printf '%s' "$now" | cut -c1-8)" >> "$log"
		return 0
	fi
	out=$(nice -n 10 timeout 120 git -C "$root" push --quiet origin "$committed:refs/heads/main" 2>&1)
	rc=$?
	if [ "$rc" -eq 0 ]; then
		echo "$stamp ok $(git -C "$root" rev-parse --short HEAD)" >> "$log"
	else
		echo "$stamp FAILED rc=$rc $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)" >> "$log"
	fi
}

# The detached unit already is the background job: it runs the publish in the foreground, because a
# child left running after this script exits would be killed with the unit's cgroup.
if [ -n "${MISHE_PUSH_DETACHED:-}" ]; then
	publish
	exit 0
fi
(publish) >/dev/null 2>&1 &
exit 0
