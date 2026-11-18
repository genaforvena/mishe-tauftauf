#!/bin/bash
# Root-drive migration cutover: plan steps 7-10 of docs/root-fs-migration-plan.md.
#
# This script renames three live trees, creates symlinks in their place and
# restarts the plant. It runs from OUTSIDE the trees it moves, because its own
# path must survive the rename:
#
#   cp tools/root-migration-flip.sh                /mnt/mishe-scratch/mishe-migration/flip.sh
#   cp tools/root-migration-verify-scoped.sh       /mnt/mishe-scratch/mishe-migration/verify-scoped.sh
#   cp tools/root-migration-verify-copy.sh         /mnt/mishe-scratch/mishe-migration/verify-copy.sh   # --full only
#   sha256sum tools/root-migration-flip.sh /mnt/mishe-scratch/mishe-migration/flip.sh
#   /mnt/mishe-scratch/mishe-migration/flip.sh --dry-run        # plan only
#   /mnt/mishe-scratch/mishe-migration/flip.sh --execute        # the cutover
#
# Preflight refuses to run unless its own sha256 and the sha256 of the helper for
# the selected --shape both match the committed copies under tools/. A stale
# helper cannot weaken the verify: the pre-0e8bafdc scoped check printed VERIFIED
# after comparing nothing.
#
# Run it from a wake-held slot. Entry order in quiesce is: stop the wake and
# delivery triggers, drain live wake engines (up to MIGRATION_ENGINE_TIMEOUT),
# re-sample the moving-pin-delivery guard and wait up to
# MIGRATION_DELIVERY_DRAIN_TIMEOUT for a delivery started during that drain, then
# stop the recorded units. The preflight guard alone cannot vouch for the run: it
# samples before a wait that can outlast a cadence interval. The default verify
# shape is the scoped one accepted in step 7; --full selects the exhaustive shape,
# which needs ~23-24 min of source reads.
#
# Safety: before anything is stopped, a watchdog transient unit is armed. If this
# script is killed outright (SIGKILL, host reset) the watchdog restores a working
# plant: rollback if the rename half-happened, restart otherwise. Every other
# ending is restored in-process by the EXIT trap, in seconds rather than after the
# watchdog's sleep -- a failed delta at minute 3 used to leave the plant down for
# the rest of the timeout. Neither path deletes a *.pre-migration tree. The
# watchdog waits for this process to disappear before acting, so a slow flip is
# never mistaken for a dead one -- up to WATCHDOG_FLIP_WAIT_TICKS, which covers
# the sum of the stage bounds below (8700 s), not the 600 s it used to wait.
# Both restorers release only the hold this flip wrote (its `root migration flip
# <pid>` token); a manual `mishe hold on` or plant_boot's boot-guard hold passes
# through untouched.
set -u

# Defaults are the production paths; the overrides exist so the abort paths can be
# rehearsed on a sandbox tree instead of asserted (the scoped verify helper already
# takes the same three variables). Whenever they are overridden the run logs the
# paths it used.
SRC=${MIGRATION_SRC:-/home/mesh-home}
DST=${MIGRATION_DST:-/mnt/mishe-scratch/home-mesh-home}
WORK=${MIGRATION_WORK:-/mnt/mishe-scratch/mishe-migration}
LOG=$WORK/flip.log
UNITS=$WORK/flip.units
DONE=$WORK/flip.done
TREES=(mishe-tauftauf .omp .codex)
# The site home whose `hold` file pauses wake starts: a wake that finds it exits
# `held` in milliseconds instead of running an engine (src/mishe_tauftauf/wake.py,
# `run_wake`). The flip writes it once it starts quiescing, so the plant's own
# scheduler cannot start an engine into the drain or the delta; every restore path
# removes it. Overridable so the sandbox test never touches the live site.
SITE=${MISHE_SITE:-$SRC/mishe-tauftauf/.mishe-tauftauf}
HOLD=$SITE/hold

MODE=dry-run
SHAPE=scoped
WATCHDOG_SECONDS=
PRINT_WATCHDOG=0
# Measured stage bounds, not guesses: the live delta pass was 2 min 20 s for all
# three trees (docs/root-fs-migration-plan.md, 08:30:45-08:33:05Z), the scoped
# verify 9.3 s on .omp, and the full checksum pass is 23-24 min of reads. A stage
# that outlives its bound is a hang, not slow work; the EXIT trap restores then.
DELTA_TIMEOUT=1800
VERIFY_TIMEOUT_SCOPED=1800
VERIFY_TIMEOUT_FULL=3600
# How long the flip waits for live wake engines to finish before it stops the
# plant (production execute is detached, so the launching wake can yield).
ENGINE_DRAIN_TIMEOUT=${MIGRATION_ENGINE_TIMEOUT:-1800}
# How long the flip waits, after the engine drain, for a delivery that started
# during that drain to finish. Its whole-suite check plus activation measured
# about 18 min (10:08:18 -> 10:26:42Z on 2026-11-17), so the bound is generous
# but still aborts a wedged unit before anything is stopped.
DELIVERY_DRAIN_TIMEOUT=${MIGRATION_DELIVERY_DRAIN_TIMEOUT:-900}
# How long the watchdog keeps watch over a live flip pid before it gives up and
# leaves the plant alone (ticks of its 10 s poll). It must cover the sum of the
# flip's own stage bounds: engine drain 1800 + delivery drain 900 + delta 1800 +
# full verify 3600 + restart slack ~600 = 8700 s. It did not on 2026-11-17: the
# 900 s sleep plus the old fixed 600 s wait expired at 12:25:31Z while the
# 12:00:40Z run was still inside its engine drain, so the delta, verify and
# rename would have run with no restorer at all.
WATCHDOG_FLIP_WAIT_TICKS=${MIGRATION_WATCHDOG_FLIP_WAIT_TICKS:-900}
# How long writers on the moved trees get to finish after the plant is stopped.
# Any write into a tree after the delta pass makes the verify's size+mtime and
# changed-since checks report entries, and the scoped shape can then only fail --
# on 2026-11-17 the pass that ran with five live engines ended `NOT-CLEAN` after
# ~25 min of delta+verify. Aborting here costs seconds and names the processes.
WRITER_DRAIN_TIMEOUT=${MIGRATION_WRITER_DRAIN_TIMEOUT:-120}
# Where systemd exposes unit cgroups, for cancel_queued_wakes() below. Overridable
# only so the tests can point at a fake cgroup tree.
CGROUP_ROOT=${MIGRATION_CGROUP_ROOT:-/sys/fs/cgroup}
# Units that START a promotion. They are stopped before the drain (a delivery
# begun inside a 1800 s drain checks and activates against the trees being
# renamed) and are recorded in $UNITS so the restart paths bring them back.
DELIVERY_TRIGGERS=(mishe-producer-delivery-cadence.timer)
PROD_SRC=/home/mesh-home
DETACH=0
while [ $# -gt 0 ]; do
    case "$1" in
        --execute) MODE=execute ;;
        --detach) MODE=execute; DETACH=1 ;;
        --dry-run) MODE=dry-run ;;
        --full) SHAPE=full ;;
        --scoped) SHAPE=scoped ;;
        --print-watchdog) PRINT_WATCHDOG=1 ;;
        --watchdog-seconds) WATCHDOG_SECONDS=${2:?--watchdog-seconds needs a value}; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# The watchdog's sleep now only bounds how long a hard kill can leave the plant
# down, because it waits for this process to end before restoring anyway. The
# full shape's 23-24 min of reads would have outlived the old fixed 900 s.
if [ -z "$WATCHDOG_SECONDS" ]; then
    if [ "$SHAPE" = full ]; then WATCHDOG_SECONDS=3600; else WATCHDOG_SECONDS=900; fi
fi
START=$(date +%s)
elapsed() { echo $(($(date +%s) - START)); }

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG" >&2; }
die() { log "FAIL: $*"; exit 1; }

FLIP_PID=$$
FLIP_BASE=$(basename "$0")
# The hold this flip writes carries this token, and only a hold carrying it is
# removed or resumed by the flip and the watchdog. A manual `mishe hold on` holds
# its own reason text, and plant_boot's boot-guard hold must outlive the flip:
# `hold off` resumes owed wakes, which must not happen before the boot guard
# admits. HOLD_MINE is 1 only when this run created the file.
HOLD_TOKEN="root migration flip $FLIP_PID"
HOLD_MINE=0
log "paths: src=$SRC dst=$DST work=$WORK (pid $FLIP_PID)"

self_unit() { awk -F/ '/^0::/{print $NF}' /proc/$$/cgroup 2>/dev/null; }

# Cheap liveness check for writers on a tree that is about to be renamed: a
# process whose cwd or executable lives under it, or a command line naming it.
# lsof +D would be exhaustive but walks 31 G and cannot fit the window.
writers_present() {
    local t p hit=0
    for t in "${TREES[@]}"; do
        if ls -l /proc/*/cwd 2>/dev/null | grep -qF "$SRC/$t"; then
            log "process cwd under $SRC/$t"; hit=1
        fi
        if pgrep -af -- "$SRC/$t" 2>/dev/null | grep -vq "root-migration-flip"; then
            log "process naming $SRC/$t"; hit=1
        fi
    done
    # Status convention: 0 means "a writer is present", so the drain loop can be
    # `writers_present || break`. The first version returned $hit, which read as
    # "no writer" in the loop and "writer" in the warning: every execute slept the
    # full 30 s and then warned about writers it had not found.
    [ "$hit" -eq 0 ] && return 1
    return 0
}

# Everything that can start a wake engine or a promotion. Two sources are not
# covered by the $UNITS snapshot taken when quiesce starts:
#   * `mishe-next-due-<hash>` is a *transient* unit: a `due` service that runs
#     inside the drain calls arm.py, which replaces it with a timer under a new
#     hash, so one stop cannot cover it. Observed 2026-11-17 11:23-11:26Z: after
#     `stop failed (continuing): mishe-next-due-92e2a1ca02bb.timer` the new timer
#     fired and started field-review, senses, genome, network and health inside
#     the delta; the verify then found 29 changed entries and could not be clean.
#   * `mishe-wake-debounce-<role>.service` is a Type=oneshot with
#     WorkingDirectory under $SRC/mishe-tauftauf: it sleeps the coalescing window
#     (120 s) and then starts `mishe-wake@<role>`. A debounce started just before
#     quiesce therefore launches an engine after the plant stop (live example
#     11:34:19Z: cwd under the repo, `wake-debounce health --window 120`).
#     Stopping it loses no trigger: the role stays owed and the arm after the
#     restart retries it.
#   * a `mishe-wake@<role>.service` whose start job is queued behind the dispatch
#     lock is `activating` with no `omp` yet, so wake_engines() cannot see it and
#     the drain proceeds; the job then starts an engine inside the delta (live
#     example 11:36Z: `@health` and `@field-review` both activating/start while
#     this wake held dispatch). Stop it to cancel the job. A unit that already
#     has an `omp` in its cgroup is a live engine: the drain waits for it, and
#     this must never kill it.
# Disarm again inside the writer wait, immediately before the delta.
cancel_queued_wakes() {
    local u cg p own live
    own=
    [ -n "${MISHE_ROLE:-}" ] && own="mishe-wake@$MISHE_ROLE.service"
    while read -r u; do
        [ -n "$u" ] || continue
        [ "$u" = "$own" ] && continue        # the wake that launched this flip
        cg=$(systemctl --user show "$u" -p ControlGroup --value 2>/dev/null) || continue
        [ -n "$cg" ] || continue
        [ -r "$CGROUP_ROOT$cg/cgroup.procs" ] || continue
        live=0
        while read -r p; do
            [ -r "/proc/$p/cmdline" ] || continue
            if tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qE '(^|/)omp( |$)'; then
                live=1
                break
            fi
        done <"$CGROUP_ROOT$cg/cgroup.procs"
        [ "$live" -eq 1 ] && continue
        systemctl --user stop "$u" 2>/dev/null && log "cancelled queued wake $u" ||
            log "queued wake stop failed: $u"
    done < <(systemctl --user list-units --all --state=activating --no-legend --plain 'mishe-wake@*.service' 2>/dev/null | awk '{print $1}')
}

disarm_triggers() {
    local rc=0 u
    # Stop by explicit name, not by glob, and record each stopped name in $UNITS
    # before the stop. A glob stop is not filtered by target_units(), so it can
    # catch a unit whose file does not name the moved checkout, and every restore
    # path (on_exit, the symlink-failure branch, cutover, the watchdog body)
    # restarts only $UNITS -- such a unit stayed stopped after a rollback and
    # after a successful cutover (review finding, release f556cae1). Recording
    # before the stop means a kill between the two only replays a start of a unit
    # that was never stopped, which systemd treats as a no-op.
    # The debounce oneshot is the one exception, and it is stopped without being
    # recorded: it runs per trigger and would normally be inactive, so starting
    # it on restore fabricates a wake that was never owed (twelve roles at once
    # after every flip). Its trigger is not lost -- the role stays owed, and the
    # arm oneshot below re-arms the due timer that retries it.
    while read -r u; do
        [ -n "$u" ] || continue
        case "$u" in
            mishe-wake-debounce-*.service) ;;
            mishe-wake-*.path|mishe-producer-dispatch.timer|\
            mishe-next-due-*.timer|mishe-next-due-*.service|mishe-arm.service)
                grep -qxF "$u" "$UNITS" 2>/dev/null || echo "$u" >>"$UNITS" ;;
            *) continue ;;
        esac
        systemctl --user stop "$u" 2>/dev/null || rc=$?
    done < <(systemctl --user list-units --all --no-legend --plain 2>/dev/null | awk '{print $1}')
    # The caller logs a nonzero return: a trigger that refused to stop must stay
    # visible, so the queued-wake pass must not overwrite that status.
    cancel_queued_wakes
    return "$rc"
}

# A live wake engine is an `omp` process launched with a wake prompt under the
# site home (observed cmdline: ... @/home/mesh-home/mishe-tauftauf/.mishe-tauftauf/
# wakes/wake-genome-...prompt.md). Every engine writes into all three moved trees
# for as long as it runs: dispatch and routing ledgers, signal files, agent db WAL
# and per-session logs. A delta taken under one can never verify clean (measured
# 2026-11-17 09:37-09:41Z: 29 + 13 + 3 files changed between delta and verify).
wake_engines() {
    pgrep -af -- "$SRC/" 2>/dev/null | grep -F '/wakes/wake-' || true
}

# A promotion in flight runs its whole-suite check and then activates a release,
# and during cutover the source trees are briefly absent then symlinks -- the
# exact path that check exercises. Real unit names carry no `mishe-` prefix
# (`moving-pin-delivery-HHMMSS`); the pre-09:07Z pattern matched nothing while a
# real delivery ran. One predicate for both the preflight refusal and the
# post-drain re-sample, so the two cannot drift apart.
live_delivery() {
    systemctl --user list-units --all --state=running,activating --no-legend --plain 'moving-pin-delivery-*' 2>/dev/null | grep . || true
}

# Units that were active before the flip and name a moved tree or the site home.
# Data-driven rather than a hardcoded list: the plant has 250+ active units and
# the inventory changes with every delivery.
target_units() {
    local u own wake
    own=$(self_unit)
    wake=
    [ -n "${MISHE_WAKE_KEY:-}" ] && wake=$(systemctl --user list-units --state=active --no-legend --plain 'mishe-wake-*' 2>/dev/null | awk '{print $1}' | grep -F "$MISHE_WAKE_KEY" | head -1)
    systemctl --user list-units --state=active --no-legend --plain --type=service --type=timer --type=path 2>/dev/null |
    awk '{print $1}' | while read -r u; do
        case "$u" in
            mishe-root-migration-*) continue ;;   # this script and its watchdog
            "$own") continue ;;
            "$wake") continue ;;                  # the wake blocked on this run; it must survive to yield
        esac
        # A oneshot producer's service is inactive between runs, so the active
        # unit is its timer and the timer file names no path; read the timer's
        # Unit= target as well as the unit itself.
        if { systemctl --user cat "$u" 2>/dev/null
             target=$(systemctl --user show "$u" -p Unit --value 2>/dev/null)
             [ -n "$target" ] && systemctl --user cat "$target" 2>/dev/null; } |
           grep -qF -e "$SRC/mishe-tauftauf" -e "$SRC/.mishe-tauftauf" -e "$SRC/.omp" -e "$SRC/.codex"; then
            echo "$u"
        fi
    done
}

restart_recorded() {
    local u n=0
    [ -s "$UNITS" ] || { log "no unit list to restart"; return 0; }
    while read -r u; do
        # A transient next-due timer is not restartable: stopping it removes it,
        # and the snapshot name cannot be started again (`restart failed:
        # mishe-next-due-92e2a1ca02bb.timer`, 2026-11-17 11:28:18Z). The arming
        # oneshot below re-arms one under the current release instead.
        # `mishe-release-review-retry-<sha>-<ts>.timer` is the same shape: an
        # `--on-active` transient created by schedule_review_retry, so stopping it
        # destroys the retry and starting the recorded name fails (three such
        # lines at 11:28:26Z). It is recoverable, not silent: a pending review is
        # re-armed by schedule_review_retry_if_needed on the next release activity.
        case "$u" in
            mishe-next-due-*) continue ;;
            mishe-release-review-retry-*) continue ;;
        esac
        systemctl --user start "$u" 2>/dev/null && n=$((n + 1)) || log "restart failed: $u"
    done <"$UNITS"
    log "restarted $n units from $UNITS"
    # Re-arm the next-due timer that the disarm removed. Starting a unit that is
    # already active is a no-op, so this is safe when the snapshot had no timer.
    systemctl --user start mishe-arm.service 2>/dev/null && log "re-armed the next-due timer" ||
        log "re-arm failed: mishe-arm.service (the next-due producer will re-arm on its next run)"
}

# The watchdog body is built as text so `--print-watchdog` can show exactly what
# the transient unit will run; nested quoting here is not reviewable by eye.
watchdog_body() {
    cat <<EOF
sleep $WATCHDOG_SECONDS
L=$LOG
D=$DONE
F=$FLIP_PID
H=$HOLD
T='$HOLD_TOKEN'
p() { printf '%s watchdog: %s\n' "\$(date -u +%FT%TZ)" "\$*" >>"\$L"; }
flip_alive() {
    [ -r /proc/\$F/cmdline ] || return 1
    tr '\0' ' ' </proc/\$F/cmdline | grep -qF '$FLIP_BASE'
}
if [ -e "\$D" ]; then p 'flip reported done'; exit 0; fi
# A slow flip is not a dead flip. Restarting units while the flip sits between
# verify and rename puts writes into the source trees that the rename then hides
# in *.pre-migration, while the flipped tree serves stale bytes. Wait for the
# flip process to end; the script's own EXIT trap restores within seconds of any
# ordinary failure, so this path is for a hard kill. The wait covers the sum of
# the flip's own stage bounds, not an arbitrary 600 s: on 2026-11-17 the old cap
# expired at 12:25:31Z while the flip was still inside its engine drain, leaving
# delta and rename with no restorer. Past the cap the pid is treated as stuck and
# the plant is left alone, loudly.
i=0
while flip_alive; do
    if [ -e "\$D" ]; then p 'flip reported done'; exit 0; fi
    i=\$((i + 1))
    if [ "\$i" -ge $WATCHDOG_FLIP_WAIT_TICKS ]; then
        p "flip pid \$F still alive \$((i * 10))s after the timeout; not restoring under a live flip"
        exit 0
    fi
    sleep 10
done
p 'flip process is gone; restoring a working plant'
for t in ${TREES[*]}; do
    if [ -L $SRC/\$t ] && [ "\$(readlink -f $SRC/\$t)" = "$DST/\$t" ]; then
        p "\$t is flipped and consistent"
    elif [ -e $SRC/\$t.pre-migration ]; then
        p "rollback \$t"
        [ -L $SRC/\$t ] && rm -f $SRC/\$t
        rmdir $SRC/\$t 2>/dev/null
        mv $SRC/\$t.pre-migration $SRC/\$t
    elif [ -d $SRC/\$t ]; then
        p "\$t untouched"
    else
        p "\$t state unclear: left untouched"
    fi
done
# The watchdog restarts $UNITS itself (it cannot call restart_recorded: it runs
# in its own transient unit). A transient next-due or review-retry timer cannot be
# started by its snapshot name, so both are skipped and the next-due timer is
# re-armed through the oneshot, exactly as restart_recorded does after an
# in-process restore.
while read -r u; do
    case "\$u" in mishe-next-due-*|mishe-release-review-retry-*) continue ;; esac
    systemctl --user start "\$u" 2>/dev/null || p "restart failed: \$u"
done <$UNITS
systemctl --user start mishe-arm.service 2>/dev/null && p 're-armed the next-due timer' ||
    p 're-arm failed: mishe-arm.service (the next-due producer will re-arm on its next run)'
# The flip writes the hold before it stops anything, so a hard kill would
# otherwise leave every wake held with no process left to release it. Release
# only the hold this flip wrote: a manual `mishe hold on` or plant_boot's
# boot-guard hold carries other text and must survive the restore.
if [ -e "\$H" ]; then
    if [ "\$(cat "\$H" 2>/dev/null)" = "\$T" ] && ! flip_alive; then
        rm -f "\$H" && p 'released the hold: wake starts resume'
    else
        p 'hold left in place: not this flip token, or its writer is still alive'
    fi
fi
p 'restore done'
EOF
}

arm_watchdog() {
    local name=mishe-root-migration-watchdog body
    body=$(watchdog_body)
    if [ "$PRINT_WATCHDOG" = 1 ]; then printf '%s\n' "$body"; return 0; fi
    rm -f "$DONE"   # a stale marker would make the watchdog exit without restoring
    systemctl --user stop "$name.service" 2>/dev/null
    systemd-run --user --unit="$name" --collect \
        --description="root migration flip watchdog" \
        /bin/bash -c "$body" >/dev/null 2>&1 ||
        die "could not arm the watchdog; refusing to stop anything"
    log "watchdog armed: ${WATCHDOG_SECONDS}s sleep, then waits up to $((WATCHDOG_FLIP_WAIT_TICKS * 10))s for flip pid $FLIP_PID to end; unit $name.service"
}

# --- hold --------------------------------------------------------------------
# Pause wake starts for the whole quiesce window. The drains below can last the
# 1800 s engine bound, and the plant's own triggers keep starting engines into
# that window: on 2026-11-17 a debounce oneshot started genome at 12:11:55Z and
# health at 12:12:11Z, eleven minutes into a drain that was waiting for the
# previous engines to end. A held wake exits `held` in milliseconds instead of
# running an engine, so the drain converges on the engines that already exist.
# The hold suppresses wake starts only: a unit that fails still records its
# OnFailure signal, so a real fault during the window stays visible.
# An existing hold is never overwritten: a manual `mishe hold on` and plant_boot's
# boot-guard hold both mean "do not start wakes", and the flip's release resumes
# owed wakes, so clearing one early would start them before the boot guard admits.
# The claim is link(2) on a fully written temp file, so a concurrent `mishe hold
# on` that wins the create keeps its text and the flip does not own the file.
hold_plant() {
    mkdir -p "$SITE" || { log "hold: cannot create $SITE"; return 1; }
    local tmp=$HOLD.flip.$$
    printf '%s\n' "$HOLD_TOKEN" >"$tmp" || { log "hold: cannot write $tmp"; return 1; }
    if ln "$tmp" "$HOLD" 2>/dev/null; then
        HOLD_MINE=1
        log "hold: wake starts paused ($HOLD)"
    else
        HOLD_MINE=0
        if [ -e "$HOLD" ]; then
            log "hold: $HOLD was already there; left to its owner, wake starts stay paused"
        else
            log "hold: cannot create $HOLD; wake starts are not paused"
            rm -f "$tmp"
            return 1
        fi
    fi
    rm -f "$tmp"
    return 0
}

# Release the hold and resume the roles that were owed while it was in place. A
# held wake exits without running and nothing re-fires it, so the resume has to
# happen here. The site venv python is the only CLI handle this script has; when
# it is absent (the unit-test sandbox) nothing is started and the next arm
# resumes, with the skip visible in flip.log.
# Only this flip's own hold is released: HOLD_MINE set and the content still our
# token. A manual `mishe hold on` written after ours overwrites the file with its
# reason, so the content check also covers that race.
release_hold() {
    if [ "$HOLD_MINE" != 1 ]; then
        [ -e "$HOLD" ] || return 0
        log "hold: $HOLD is not this flip's; left in place"
        return 0
    fi
    HOLD_MINE=0
    [ -e "$HOLD" ] || return 0
    if [ "$(cat "$HOLD" 2>/dev/null)" != "$HOLD_TOKEN" ]; then
        log "hold: $HOLD no longer carries this flip's token; left in place"
        return 0
    fi
    rm -f "$HOLD" || { log "hold: cannot remove $HOLD; wakes stay paused until it is deleted"; return 1; }
    log "hold: released"
    local py=$SRC/mishe-tauftauf/.venv/bin/python
    if [ -x "$py" ]; then
        "$py" -m mishe_tauftauf --home "$SITE" hold off >>"$LOG" 2>&1 ||
            log "hold: resume after release failed: rc=$? (the next arm retries it)"
    else
        log "hold: no venv python at $py; owed roles resume on the next arm"
    fi
}

# --- restore -----------------------------------------------------------------
# Same three-state decision the watchdog makes, but in-process. A failed delta or
# a NOT-CLEAN at minute 3 of a ~15-minute window used to leave 90-plus units
# stopped until the watchdog's sleep expired; the plant must come back as soon as
# the flip decides not to proceed.
QUIESCED=0
RESTORED=0

restore_plant() {
    local t
    for t in "${TREES[@]}"; do
        if [ -L "$SRC/$t" ] && [ "$(readlink -f "$SRC/$t")" = "$DST/$t" ]; then
            log "restore: $t is flipped and consistent"
        elif [ -e "$SRC/$t.pre-migration" ]; then
            log "restore: rollback $t"
            [ -L "$SRC/$t" ] && rm -f "$SRC/$t"
            rmdir "$SRC/$t" 2>/dev/null   # never let mv land inside a directory
            mv "$SRC/$t.pre-migration" "$SRC/$t" || log "restore: rollback of $t failed"
        elif [ -d "$SRC/$t" ]; then
            log "restore: $t untouched"
        else
            log "restore: $t state unclear; left untouched"
        fi
    done
    restart_recorded
    release_hold
}

on_exit() {
    local rc=$?
    trap - EXIT
    [ "$RESTORED" = 1 ] && return 0
    [ "$MODE" = execute ] || return 0
    [ "$QUIESCED" = 1 ] || return 0        # nothing was stopped; leave the plant alone
    [ -e "$DONE" ] && return 0             # the flip completed; nothing to restore
    RESTORED=1
    log "flip exited rc=$rc after $(elapsed)s without a done marker; restoring the plant"
    restore_plant
    systemctl --user stop mishe-root-migration-watchdog.service 2>/dev/null
    log "plant restored after $(elapsed)s; pre-migration trees untouched"
}
trap on_exit EXIT

# --- preconditions -----------------------------------------------------------
preflight() {
    local t bad=0
    for t in "${TREES[@]}"; do
        [ -d "$SRC/$t" ] || { log "missing source $SRC/$t"; bad=1; }
        [ -d "$DST/$t" ] || { log "missing destination $DST/$t (step 6 not run?)"; bad=1; }
        [ -L "$SRC/$t" ] && { log "$SRC/$t is already a symlink; refusing to re-flip"; bad=1; }
        [ -e "$SRC/$t.pre-migration" ] && { log "$SRC/$t.pre-migration exists; a previous flip is not rolled back"; bad=1; }
    done
    [ "$bad" -eq 0 ] || die "preconditions not met"
    [ "$(readlink -f "$SRC/mishe-tauftauf/.mishe-tauftauf/runtime/current" 2>/dev/null)" = "$SRC/mishe-tauftauf/.mishe-tauftauf/runtime/current" ] ||
        log "note: runtime/current resolves elsewhere; recording it below"
    log "preflight: pin -> $(readlink -f "$SRC/mishe-tauftauf/.mishe-tauftauf/runtime/current" 2>/dev/null)"
    log "preflight: HEAD  -> $(git -C "$SRC/mishe-tauftauf" rev-parse HEAD 2>/dev/null)"
    log "preflight: dst HEAD -> $(git -C "$DST/mishe-tauftauf" rev-parse HEAD 2>/dev/null)"
    # The executed copy and every helper it runs must be the reviewed bytes. A
    # stale helper would silently weaken the verify: the pre-fix scoped check
    # printed VERIFIED after comparing nothing (fixed in 0e8bafdc), so "exists
    # and is executable" is not enough.
    local me committed helper have want
    me=$(sha256sum "$0" | cut -d' ' -f1)
    committed=$(sha256sum "$SRC/mishe-tauftauf/tools/root-migration-flip.sh" 2>/dev/null | cut -d' ' -f1)
    log "preflight: flip sha256 $me (committed copy ${committed:-absent})"
    [ -n "$committed" ] && [ "$me" != "$committed" ] && die "running copy differs from the committed script"
    helper=verify-scoped.sh
    [ "$SHAPE" = full ] && helper=verify-copy.sh
    [ -x "$WORK/$helper" ] || die "missing $WORK/$helper"
    have=$(sha256sum "$WORK/$helper" | cut -d' ' -f1)
    want=$(sha256sum "$SRC/mishe-tauftauf/tools/root-migration-$helper" 2>/dev/null | cut -d' ' -f1)
    [ -n "$want" ] || die "committed tools/root-migration-$helper absent; cannot verify the deployed copy"
    [ "$have" = "$want" ] || die "$WORK/$helper ($have) differs from committed $helper ($want); recopy it"
    log "preflight: $helper sha256 $have (matches committed)"
    # A delivery check in flight would compete for root I/O and time out (step 6).
    # This sample is a fast refusal at launch; it cannot vouch for the whole run,
    # so quiesce re-samples after the engine drain.
    if [ -n "$(live_delivery)" ]; then
        die "a moving-pin delivery unit is running; not flipping"
    fi
    # The destination must have room for the delta plus headroom.
    local avail; avail=$(df -B1 --output=avail "$DST" | tail -1)
    [ "${avail:-0}" -gt $((8 * 1024 * 1024 * 1024)) ] || die "destination has less than 8 GiB free"
}

# --- quiesce -----------------------------------------------------------------
quiesce() {
    local u i engines live
    target_units | sort >"$UNITS" || die "could not enumerate units"
    # A stopped trigger is only restarted from this list, so record the delivery
    # triggers explicitly: an inactive timer is not listed by target_units, and
    # leaving one out would end the flip with the cadence permanently stopped.
    for u in "${DELIVERY_TRIGGERS[@]}"; do
        grep -qxF "$u" "$UNITS" || echo "$u" >>"$UNITS"
    done
    log "units to stop: $(wc -l <"$UNITS") (list $UNITS)"
    if [ "$MODE" = dry-run ]; then
        log "dry-run: would stop these units, delta, verify ($SHAPE), rename, symlink, restart"
        sed 's/^/  /' "$UNITS" >&2
        engines=$(wake_engines)
        if [ -n "$engines" ]; then
            log "dry-run: wake engines that must exit first (up to ${ENGINE_DRAIN_TIMEOUT}s):"
            printf '%s\n' "$engines" >&2
        fi
        live=$(live_delivery)
        if [ -n "$live" ]; then
            log "dry-run: delivery units that must end before the plant is stopped (up to ${DELIVERY_DRAIN_TIMEOUT}s):"
            printf '%s\n' "$live" >&2
        fi
        return 0
    fi
    QUIESCED=1   # from here on, any exit path must restart these units
    # Watchdog first, hold second. The watchdog is the only restorer after a
    # SIGKILL, and a hold written before it was armed left a sub-second window
    # where the plant stayed paused with no watchdog and no runnable wake to
    # clear it (a held wake exits before running). Armed first, a kill in the
    # window leaves a watchdog with no hold: it restarts the units, finds no
    # file, and does nothing further.
    arm_watchdog
    # Pause wake starts before disarming anything: the drains below can outlive
    # a trigger's armed timer, and an engine started mid-drain rewrites every
    # moved tree. Failing to write the hold is not fatal -- the writer drain is
    # still the bound before the delta -- but it must be visible in flip.log.
    hold_plant || log "hold not written; a wake can still start during the drain"
    # A wake needs a trigger and a dispatcher, and a live engine rewrites every
    # tree until it ends. Disarm the triggers before waiting so the set of
    # engines only shrinks: a .path unit that fired mid-delta started genome,
    # pub and system1 at 09:39-09:41Z and no delta under them could verify.
    # A failed stop is not fatal -- the drain below re-samples the engines and the
    # delivery guard, so a trigger that refused to stop is caught by those bounds.
    # It must still be visible in flip.log: `|| true` here hid the 09:34 abort class,
    # where a trigger stayed armed and fired a wake into the delta.
    disarm_triggers ||
        log "wake trigger stop failed (continuing): rc=$?; a wake can still start during the drain"
    # The delivery triggers go first, for the same reason: the cadence fires
    # every 600 s, so a promotion started inside the drain below would run its
    # whole-suite check and activate while the trees are renamed (field-review,
    # 2026-11-17: preflight samples the guard before a wait that can last
    # ENGINE_DRAIN_TIMEOUT, so a clear guard is necessary but not sufficient).
    systemctl --user stop "${DELIVERY_TRIGGERS[@]}" 2>/dev/null ||
        log "delivery trigger stop failed (continuing): rc=$?; a promotion can start during the drain"
    engines=$(wake_engines)
    if [ -n "$engines" ]; then
        log "wake engines still running; waiting up to ${ENGINE_DRAIN_TIMEOUT}s for them to end"
        i=0
        while :; do
            engines=$(wake_engines)
            [ -z "$engines" ] && break
            i=$((i + 10))
            if [ "$i" -ge "$ENGINE_DRAIN_TIMEOUT" ]; then
                printf '%s\n' "$engines" >&2
                die "wake engines still live after ${ENGINE_DRAIN_TIMEOUT}s; aborting before stopping the plant"
            fi
            sleep 10
        done
        log "wake engines exited after ${i}s"
    fi
    # Re-sample the delivery guard the preflight took before that wait. The
    # triggers are stopped, so the set of live deliveries only shrinks: wait
    # bounded for a promotion started during the drain instead of dying on the
    # first look, then abort if one is still wedged.
    live=$(live_delivery)
    if [ -n "$live" ]; then
        log "a delivery started during the drain; waiting up to ${DELIVERY_DRAIN_TIMEOUT}s for it to end"
        i=0
        while :; do
            live=$(live_delivery)
            [ -z "$live" ] && break
            i=$((i + 10))
            if [ "$i" -ge "$DELIVERY_DRAIN_TIMEOUT" ]; then
                printf '%s\n' "$live" >&2
                die "a moving-pin delivery unit is still running after ${DELIVERY_DRAIN_TIMEOUT}s; not flipping"
            fi
            sleep 10
        done
        log "delivery unit exited after ${i}s"
    fi
    while read -r u; do systemctl --user stop "$u" 2>/dev/null || log "stop failed (continuing): $u"; done <"$UNITS"
    # Bounded wait for writers to drain; the EXIT trap and the watchdog both
    # restart from $UNITS if we die here. Re-disarm inside the wait: a `due`
    # service that was running when the list was stopped re-arms the transient
    # next-due timer under a new name, and that timer starts a fresh engine.
    # Writers are fatal before the delta, not a warning: every write into a tree
    # after the delta pass shows up in the verify's size+mtime and changed-since
    # checks, so the scoped shape cannot be clean under them. Abort cheap.
    for i in $(seq 1 "$WRITER_DRAIN_TIMEOUT"); do
        writers_present || break
        if [ $((i % 5)) -eq 0 ]; then disarm_triggers || log "re-disarm failed: rc=$?"; fi
        sleep 1
    done
    if writers_present; then
        die "writers still present after ${WRITER_DRAIN_TIMEOUT}s; aborting before the delta (writes under the moved trees can never verify clean)"
    fi
    log "no writers left on the moved trees"
    # One last disarm once the writers are gone: nothing the stopped plant left
    # behind can start an engine into the delta that follows.
    disarm_triggers || log "final disarm failed: rc=$?"
}

# Execute in production must be detached: the launching wake keeps writing into
# all three trees until it yields, and it cannot yield while it waits for this
# script. systemd-run gives the flip its own unit, so the wake can end and the
# queued follow-up wakes stay queued behind the disarmed triggers.
#
# The relaunch starts from a clean environment AND a clean working directory, so
# every caller-set knob has to be named here and this script must be named by
# absolute path: `./flip.sh --detach` from the work dir otherwise asks systemd to
# run `/bin/bash ./flip.sh` in $HOME and the unit exits 127 before any unit is
# stopped (observed 2026-11-17 11:01:55Z). MIGRATION_ENGINE_TIMEOUT (line 59,
# else a caller's short drain bound becomes the 1800 s default),
# MIGRATION_DELIVERY_DRAIN_TIMEOUT (line 69, else a caller's delivery-drain bound
# becomes the 900 s default), MIGRATION_WRITER_DRAIN_TIMEOUT (line 75, else a
# rehearsal's short writer bound becomes the 120 s default) and MISHE_WAKE_KEY
# (line 132, else the launching wake's unit is no longer excluded from the stop
# list). `set -u` plus an empty array would expand as an unbound variable, hence
# the ${a[@]+...} guards.
detach_relaunch() {
    local unit=mishe-root-migration-flip forward=() self
    # The unit name is fixed, so the stale-unit cleanup below must not stop a
    # flip that is already running: two minds can launch in the same minute and
    # the second `stop` would kill a live cutover mid-rename, with its EXIT trap
    # then restoring the plant under the first one.
    if systemctl --user is-active --quiet "$unit.service"; then
        die "a flip is already running as $unit.service; not relaunching"
    fi
    systemctl --user stop "$unit.service" 2>/dev/null
    self=$(readlink -f -- "$0" 2>/dev/null) || self=$0
    [ -f "$self" ] || die "cannot resolve this script's path for the detached relaunch: $0"
    [ -n "${MIGRATION_ENGINE_TIMEOUT:-}" ] && forward+=(--setenv=MIGRATION_ENGINE_TIMEOUT="$MIGRATION_ENGINE_TIMEOUT")
    [ -n "${MIGRATION_DELIVERY_DRAIN_TIMEOUT:-}" ] && forward+=(--setenv=MIGRATION_DELIVERY_DRAIN_TIMEOUT="$MIGRATION_DELIVERY_DRAIN_TIMEOUT")
    [ -n "${MIGRATION_WATCHDOG_FLIP_WAIT_TICKS:-}" ] && forward+=(--setenv=MIGRATION_WATCHDOG_FLIP_WAIT_TICKS="$MIGRATION_WATCHDOG_FLIP_WAIT_TICKS")
    [ -n "${MIGRATION_WRITER_DRAIN_TIMEOUT:-}" ] && forward+=(--setenv=MIGRATION_WRITER_DRAIN_TIMEOUT="$MIGRATION_WRITER_DRAIN_TIMEOUT")
    [ -n "${MISHE_WAKE_KEY:-}" ] && forward+=(--setenv=MISHE_WAKE_KEY="$MISHE_WAKE_KEY")
    systemd-run --user --unit="$unit" --collect \
        --setenv=MISHE_FLIP_DETACHED=1 \
        --setenv=MIGRATION_SRC="$SRC" --setenv=MIGRATION_DST="$DST" --setenv=MIGRATION_WORK="$WORK" \
        ${forward[@]+"${forward[@]}"} \
        /bin/bash "$self" --execute --"$SHAPE" >/dev/null 2>&1 ||
        die "could not start the detached flip unit $unit.service"
    log "detached as $unit.service; the launching wake may yield, the flip continues"
    exit 0
}

# --- delta and verify --------------------------------------------------------
delta() {
    local t rc_all=0 rc
    for t in "${TREES[@]}"; do
        log "delta $t start (+$(elapsed)s)"
        timeout "$DELTA_TIMEOUT" rsync -aHx --stats "$SRC/$t/" "$DST/$t/" >>"$LOG" 2>&1; rc=$?
        log "delta $t rc=$rc (+$(elapsed)s)"
        [ "$rc" -ne 0 ] && rc_all=$rc
    done
    [ "$rc_all" -eq 0 ] || die "delta pass failed rc=$rc_all (124 = outlived ${DELTA_TIMEOUT}s)"
}

verify() {
    local out rc limit helper
    if [ "$SHAPE" = full ]; then
        limit=$VERIFY_TIMEOUT_FULL; helper=verify-copy.sh
    else
        limit=$VERIFY_TIMEOUT_SCOPED; helper=verify-scoped.sh
    fi
    # The verdict is the helper's exit status plus its final summary line. The
    # helpers print tree by tree and exit 2 when a per-tree evidence file is
    # missing, so matching "VERIFIED" anywhere in the output let a partial run
    # pass on one tree's word.
    out=$(timeout "$limit" "$WORK/$helper" 2>&1); rc=$?
    printf '%s\n' "$out" >>"$LOG"
    if printf '%s\n' "$out" | grep -q 'NOT-CLEAN'; then
        die "$SHAPE verify NOT-CLEAN (helper rc=$rc)"
    fi
    [ "$rc" -eq 0 ] || die "$SHAPE verify helper exited $rc without a full verdict (124 = outlived ${limit}s)"
    printf '%s\n' "$out" | grep -q 'verify done rc=0' ||
        die "$SHAPE verify helper exited 0 but printed no 'verify done rc=0' summary"
    log "verify $SHAPE: VERIFIED (helper rc=0, +$(elapsed)s)"
}

# --- cutover -----------------------------------------------------------------
cutover() {
    local t
    for t in "${TREES[@]}"; do
        mv "$SRC/$t" "$SRC/$t.pre-migration" || die "rename $t failed"
        log "renamed $t -> $t.pre-migration"
    done
    for t in "${TREES[@]}"; do
        if ! ln -s "$DST/$t" "$SRC/$t"; then
            local failed_msg="symlink $t failed"
            log "$failed_msg; rolling back"
            for t in "${TREES[@]}"; do
                # `mv dir symlink-to-dir` would move the tree *inside* the
                # destination; drop the link first.
                [ -L "$SRC/$t" ] && rm -f "$SRC/$t"
                [ -d "$SRC/$t.pre-migration" ] && mv "$SRC/$t.pre-migration" "$SRC/$t"
            done
            # The full in-process restore, not just the restart: this branch used
            # to restart the units and die with RESTORED=1, so the EXIT trap
            # skipped restore_plant and the hold stayed on disk -- wake starts
            # paused until the watchdog's 900 s sleep expired, even though the
            # trees were already rolled back in seconds. Every other abort gets
            # the same restore; a failed cutover must not be the exception.
            restore_plant
            systemctl --user stop mishe-root-migration-watchdog.service 2>/dev/null
            RESTORED=1
            die "$failed_msg, rolled back"
        fi
        [ "$(readlink -f "$SRC/$t")" = "$DST/$t" ] || die "$SRC/$t does not resolve to $DST/$t"
        log "symlink $t -> $DST/$t ok"
    done
    # The marker means "flipped and the plant is back", not just "renamed": both
    # restorers return early on it (the EXIT trap and the watchdog's first check),
    # so writing it before restart_recorded left a kill inside the restart loop
    # with every listed unit still stopped and nothing left to start them. Without
    # the marker, both paths reach restore_plant, which sees the consistent
    # symlinks and only restarts -- no rollback, no pre-migration deletion.
    restart_recorded
    release_hold
    date -u +%FT%TZ >"$DONE"
    log "flip complete in $(elapsed)s; rollback: rm the three symlinks and mv *.pre-migration back"
}

[ "$PRINT_WATCHDOG" = 1 ] && { arm_watchdog; exit 0; }
preflight
# Production execute is detached: the wake that starts it must be free to yield,
# and the flip then waits for the running engines itself before stopping anything.
if [ "$MODE" = execute ] && [ "${MISHE_FLIP_DETACHED:-0}" != 1 ]; then
    if [ "$SRC" = "$PROD_SRC" ] && [ "$DETACH" != 1 ]; then
        die "refusing to cut over in-process: a live wake engine writes into all three trees; pass --detach"
    fi
    [ "$DETACH" = 1 ] && detach_relaunch
fi
quiesce
[ "$MODE" = dry-run ] && { log "dry-run complete"; exit 0; }
delta
verify
cutover
log "done"
