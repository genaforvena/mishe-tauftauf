#!/bin/bash
# Root-drive migration cutover: plan steps 7-10 of docs/root-fs-migration-plan.md.
#
# This script renames three live trees, creates symlinks in their place and
# restarts the plant. It runs from OUTSIDE the trees it moves, because its own
# path must survive the rename:
#
#   cp tools/root-migration-flip.sh /mnt/mishe-scratch/mishe-migration/flip.sh
#   sha256sum tools/root-migration-flip.sh /mnt/mishe-scratch/mishe-migration/flip.sh
#   /mnt/mishe-scratch/mishe-migration/flip.sh --dry-run        # plan only
#   /mnt/mishe-scratch/mishe-migration/flip.sh --execute        # the cutover
#
# Run it from a wake-held slot (delivery waits for wakes to end) so the window is
# delivery-free. The default verify shape is the scoped one accepted in step 7;
# --full selects the exhaustive shape, which needs ~23-24 min of source reads.
#
# Safety: before anything is stopped, a watchdog transient unit is armed with a
# timeout longer than the whole flip. If this script dies for any reason, the
# watchdog restores a working plant (rollback if the rename half-happened,
# restart otherwise). It never deletes a *.pre-migration tree.
set -u

SRC=/home/mesh-home
DST=/mnt/mishe-scratch/home-mesh-home
WORK=/mnt/mishe-scratch/mishe-migration
LOG=$WORK/flip.log
UNITS=$WORK/flip.units
DONE=$WORK/flip.done
TREES=(mishe-tauftauf .omp .codex)

MODE=dry-run
SHAPE=scoped
WATCHDOG_SECONDS=900
while [ $# -gt 0 ]; do
    case "$1" in
        --execute) MODE=execute ;;
        --dry-run) MODE=dry-run ;;
        --full) SHAPE=full ;;
        --scoped) SHAPE=scoped ;;
        --watchdog-seconds) WATCHDOG_SECONDS=${2:?--watchdog-seconds needs a value}; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG" >&2; }
die() { log "FAIL: $*"; exit 1; }

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
    return "$hit"
}

# Units that were active before the flip and name a moved tree or the site home.
# Data-driven rather than a hardcoded list: the plant has 250+ active units and
# the inventory changes with every delivery.
target_units() {
    local u own wake
    own=$(self_unit)
    wake=
    [ -n "${MISHE_WAKE_KEY:-}" ] && wake=$(systemctl --user list-units --state=active --no-legend --plain 'mishe-wake-*' 2>/dev/null | awk '{print $1}' | grep -F "$MISHE_WAKE_KEY" | head -1)
    systemctl --user list-units --state=active --no-legend --plain --type=service --type=timer 2>/dev/null |
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

arm_watchdog() {
    local name=mishe-root-migration-watchdog
    rm -f "$DONE"   # a stale marker would make the watchdog exit without restoring
    systemctl --user stop "$name.service" 2>/dev/null
    systemd-run --user --unit="$name" --collect \
        --description="root migration flip watchdog" \
        /bin/bash -c "
            sleep $WATCHDOG_SECONDS
            L=$LOG
            p() { printf '%s watchdog: %s\n' \"\$(date -u +%FT%TZ)\" \"\$*\" >>\"\$L\"; }
            if [ -e $DONE ]; then p 'flip reported done'; exit 0; fi
            p 'flip did not report done; restoring a working plant'
            for t in ${TREES[*]}; do
                if [ ! -L $SRC/\$t ] && [ -d $SRC/\$t.pre-migration ] && [ -d $DST/\$t ]; then
                    p \"rollback \$t\"
                    mv $SRC/\$t.pre-migration $SRC/\$t
                elif [ -L $SRC/\$t ] && [ -d $DST/\$t ]; then
                    p \"\$t is flipped and consistent\"
                else
                    p \"\$t state unclear: left untouched\"
                fi
            done
            while read -r u; do systemctl --user start \"\$u\" 2>/dev/null || p \"restart failed: \$u\"; done <$UNITS
            p 'restore done'
        " >/dev/null 2>&1 ||
        die "could not arm the watchdog; refusing to stop anything"
    log "watchdog armed: ${WATCHDOG_SECONDS}s, unit $name.service"
}

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
    # The executed copy must be the reviewed bytes, and its helpers must exist.
    local me committed
    me=$(sha256sum "$0" | cut -d' ' -f1)
    committed=$(sha256sum "$SRC/mishe-tauftauf/tools/root-migration-flip.sh" 2>/dev/null | cut -d' ' -f1)
    log "preflight: flip sha256 $me (committed copy ${committed:-absent})"
    [ -n "$committed" ] && [ "$me" != "$committed" ] && die "running copy differs from the committed script"
    [ -x "$WORK/verify-scoped.sh" ] || die "missing $WORK/verify-scoped.sh"
    [ "$SHAPE" = full ] && { [ -x "$WORK/verify-copy.sh" ] || die "missing $WORK/verify-copy.sh"; }
    # A delivery check in flight would compete for root I/O and time out (step 6).
    if systemctl --user list-units --state=running --no-legend --plain 'mishe-moving-pin-delivery-*' 2>/dev/null | grep -q .; then
        die "a moving-pin delivery unit is running; not flipping"
    fi
    # The destination must have room for the delta plus headroom.
    local avail; avail=$(df -B1 --output=avail "$DST" | tail -1)
    [ "${avail:-0}" -gt $((8 * 1024 * 1024 * 1024)) ] || die "destination has less than 8 GiB free"
}

# --- quiesce -----------------------------------------------------------------
quiesce() {
    local u
    target_units | sort >"$UNITS" || die "could not enumerate units"
    log "units to stop: $(wc -l <"$UNITS") (list $UNITS)"
    if [ "$MODE" = dry-run ]; then
        log "dry-run: would stop these units, delta, verify ($SHAPE), rename, symlink, restart"
        sed 's/^/  /' "$UNITS" >&2
        return 0
    fi
    arm_watchdog
    while read -r u; do systemctl --user stop "$u" 2>/dev/null || log "stop failed (continuing): $u"; done <"$UNITS"
    # Bounded wait for writers to drain; the watchdog restarts them if we die here.
    local i
    for i in $(seq 1 30); do
        writers_present || break
        sleep 1
    done
    writers_present && log "warning: writers still present after 30 s (see log above)" || log "no writers left on the moved trees"
}

# --- delta and verify --------------------------------------------------------
delta() {
    local t rc_all=0 rc
    for t in "${TREES[@]}"; do
        log "delta $t start"
        rsync -aHx --stats "$SRC/$t/" "$DST/$t/" >>"$LOG" 2>&1; rc=$?
        log "delta $t rc=$rc"
        [ "$rc" -ne 0 ] && rc_all=$rc
    done
    [ "$rc_all" -eq 0 ] || die "delta pass failed rc=$rc_all"
}

verify() {
    local out
    if [ "$SHAPE" = full ]; then
        out=$("$WORK/verify-copy.sh" 2>&1); echo "$out" >>"$LOG"
        printf '%s\n' "$out" | grep -q 'NOT-CLEAN' && die "full verify NOT-CLEAN"
        printf '%s\n' "$out" | grep -q 'VERIFIED' || die "full verify produced no verdict"
        log "verify full: VERIFIED"
    else
        # Scoped shape (step 7): exhaustive size+mtime, -c on what the delta
        # touched, random content sample of the rest, equal counts and bytes.
        out=$("$WORK/verify-scoped.sh" 2>&1); echo "$out" >>"$LOG"
        printf '%s\n' "$out" | grep -q 'NOT-CLEAN' && die "scoped verify NOT-CLEAN"
        printf '%s\n' "$out" | grep -q 'VERIFIED' || die "scoped verify produced no verdict"
        log "verify scoped: VERIFIED"
    fi
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
            rm -f "$SRC/$t"
            for t in "${TREES[@]}"; do mv "$SRC/$t.pre-migration" "$SRC/$t" 2>/dev/null; done
            restart_recorded
            die "symlink $t failed, rolled back"
        fi
        [ "$(readlink -f "$SRC/$t")" = "$DST/$t" ] || die "$SRC/$t does not resolve to $DST/$t"
        log "symlink $t -> $DST/$t ok"
    done
    date -u +%FT%TZ >"$DONE"
    restart_recorded
    log "flip complete; rollback: rm the three symlinks and mv *.pre-migration back"
}

preflight
quiesce
[ "$MODE" = dry-run ] && { log "dry-run complete"; exit 0; }
delta
verify
cutover
log "done"
