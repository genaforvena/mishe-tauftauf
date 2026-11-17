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
# never mistaken for a dead one.
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
        systemctl --user start "$u" 2>/dev/null && n=$((n + 1)) || log "restart failed: $u"
    done <"$UNITS"
    log "restarted $n units from $UNITS"
}

# The watchdog body is built as text so `--print-watchdog` can show exactly what
# the transient unit will run; nested quoting here is not reviewable by eye.
watchdog_body() {
    cat <<EOF
sleep $WATCHDOG_SECONDS
L=$LOG
D=$DONE
F=$FLIP_PID
p() { printf '%s watchdog: %s\n' "\$(date -u +%FT%TZ)" "\$*" >>"\$L"; }
flip_alive() {
    [ -r /proc/\$F/cmdline ] || return 1
    tr '\0' ' ' </proc/\$F/cmdline | grep -qF '$FLIP_BASE'
}
if [ -e "\$D" ]; then p 'flip reported done'; exit 0; fi
# A slow flip is not a dead flip. Restarting units while the flip sits between
# verify and rename puts writes into the source trees that the rename then hides
# in *.pre-migration, while the flipped tree serves stale bytes. Wait for the
# flip process to end (bounded); the script's own EXIT trap restores within
# seconds of any ordinary failure, so this path is for a hard kill.
i=0
while flip_alive; do
    if [ -e "\$D" ]; then p 'flip reported done'; exit 0; fi
    i=\$((i + 1))
    if [ "\$i" -ge 60 ]; then
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
while read -r u; do systemctl --user start "\$u" 2>/dev/null || p "restart failed: \$u"; done <$UNITS
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
    log "watchdog armed: ${WATCHDOG_SECONDS}s sleep, then waits up to 600s for flip pid $FLIP_PID to end; unit $name.service"
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
    arm_watchdog
    QUIESCED=1   # from here on, any exit path must restart these units
    # A wake needs a trigger and a dispatcher, and a live engine rewrites every
    # tree until it ends. Disarm the triggers before waiting so the set of
    # engines only shrinks: a .path unit that fired mid-delta started genome,
    # pub and system1 at 09:39-09:41Z and no delta under them could verify.
    systemctl --user stop 'mishe-wake-*.path' 'mishe-wake-debounce-*.timer' \
        mishe-producer-dispatch.timer 'mishe-next-due-*.timer' 2>/dev/null || true
    # The delivery triggers go first, for the same reason: the cadence fires
    # every 600 s, so a promotion started inside the drain below would run its
    # whole-suite check and activate while the trees are renamed (field-review,
    # 2026-11-17: preflight samples the guard before a wait that can last
    # ENGINE_DRAIN_TIMEOUT, so a clear guard is necessary but not sufficient).
    systemctl --user stop "${DELIVERY_TRIGGERS[@]}" 2>/dev/null || true
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
    # restart from $UNITS if we die here.
    for i in $(seq 1 30); do
        writers_present || break
        sleep 1
    done
    writers_present && log "warning: writers still present after 30 s (see log above)" || log "no writers left on the moved trees"
}

# Execute in production must be detached: the launching wake keeps writing into
# all three trees until it yields, and it cannot yield while it waits for this
# script. systemd-run gives the flip its own unit, so the wake can end and the
# queued follow-up wakes stay queued behind the disarmed triggers.
#
# The relaunch starts from a clean environment, so every caller-set knob has to
# be named here or it silently reverts to its default: MIGRATION_ENGINE_TIMEOUT
# (line 59, else a caller's short drain bound becomes the 1800 s default) and
# MISHE_WAKE_KEY (line 132, else the launching wake's unit is no longer excluded
# from the stop list). `set -u` plus an empty array would expand as an unbound
# variable, hence the ${a[@]+...} guards.
detach_relaunch() {
    local unit=mishe-root-migration-flip forward=()
    [ -n "${MIGRATION_ENGINE_TIMEOUT:-}" ] && forward+=(--setenv=MIGRATION_ENGINE_TIMEOUT="$MIGRATION_ENGINE_TIMEOUT")
    [ -n "${MISHE_WAKE_KEY:-}" ] && forward+=(--setenv=MISHE_WAKE_KEY="$MISHE_WAKE_KEY")
    systemctl --user stop "$unit.service" 2>/dev/null
    systemd-run --user --unit="$unit" --collect \
        --setenv=MISHE_FLIP_DETACHED=1 \
        --setenv=MIGRATION_SRC="$SRC" --setenv=MIGRATION_DST="$DST" --setenv=MIGRATION_WORK="$WORK" \
        ${forward[@]+"${forward[@]}"} \
        /bin/bash "$0" --execute --"$SHAPE" >/dev/null 2>&1 ||
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
            log "symlink $t failed; rolling back"
            for t in "${TREES[@]}"; do
                # `mv dir symlink-to-dir` would move the tree *inside* the
                # destination; drop the link first.
                [ -L "$SRC/$t" ] && rm -f "$SRC/$t"
                [ -d "$SRC/$t.pre-migration" ] && mv "$SRC/$t.pre-migration" "$SRC/$t"
            done
            RESTORED=1
            restart_recorded
            die "symlink $t failed, rolled back"
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
