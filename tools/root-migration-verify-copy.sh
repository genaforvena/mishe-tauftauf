#!/bin/bash
# Root-drive migration, step 7 verification: dry-run delta, file count and byte count
# must all agree. Usage: verify-copy.sh [tree ...]   (default: all three)
set -u
# Same overrides as verify-scoped.sh and the flip driver: otherwise a flip run
# with overridden paths would verify (and then cut over) the default trees.
SRC=${MIGRATION_SRC:-/home/mesh-home}
DST=${MIGRATION_DST:-/mnt/mishe-scratch/home-mesh-home}
trees=("$@")
[ ${#trees[@]} -eq 0 ] && trees=(mishe-tauftauf .omp .codex)
rc_all=0
for t in "${trees[@]}"; do
    echo "=== $t verify $(date -u +%FT%TZ)"
    # 1. content delta (checksums, hardlinks, one filesystem)
    delta=$(rsync -aHxc --dry-run --itemize-changes "$SRC/$t/" "$DST/$t/" 2>&1)
    ndelta=$(printf '%s' "$delta" | grep -c '^[<>ch.*]' || true)
    # 2. file count and byte count, same filters as the copy
    nsrc=$(find "$SRC/$t" -xdev -printf . 2>/dev/null | wc -c)
    ndst=$(find "$DST/$t" -xdev -printf . 2>/dev/null | wc -c)
    bsrc=$(du -sb --apparent-size "$SRC/$t" | cut -f1)
    bdst=$(du -sb --apparent-size "$DST/$t" | cut -f1)
    echo "  delta-entries=$ndelta entries-src=$nsrc entries-dst=$ndst bytes-src=$bsrc bytes-dst=$bdst"
    if [ "$ndelta" -eq 0 ] && [ "$nsrc" -eq "$ndst" ] && [ "$bsrc" -eq "$bdst" ]; then
        echo "  VERIFIED $t"
    else
        echo "  NOT-CLEAN $t"
        printf '%s\n' "$delta" | head -20
        rc_all=1
    fi
done
echo "=== verify done rc=$rc_all $(date -u +%FT%TZ)"
# flip.sh reads the printed verdict; a direct caller must see the failure too.
exit "$rc_all"
