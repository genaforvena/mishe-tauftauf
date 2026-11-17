#!/bin/bash
# Root-drive migration, step 7 verification — scoped shape (docs/root-fs-migration-plan.md).
#
# What full `rsync -aHxc` adds over this is post-write disk corruption on files the
# bulk pass already checksummed and copied with rc=0. Full costs ~23-24 min of
# source reads (89.4 GB at 62-64 MB/s, measured 2026-11-17) and cannot fit a wake;
# this shape fits and covers everything that changed since.
#
#   1. exhaustive size+mtime equality: non-checksummed dry run reports zero transfers
#   2. content equality (`-c`) for every file modified since the bulk pass
#   3. content equality (`-c`) for a deterministic sample of the rest, capped by bytes
#   4. file counts and apparent byte counts equal
#
# Usage: verify-scoped.sh [--since ISO8601] [--sample N] [--sample-bytes BYTES] [tree ...]
# Prints VERIFIED / NOT-CLEAN per tree and a final rc.
set -u
SRC=${MIGRATION_SRC:-/home/mesh-home}
DST=${MIGRATION_DST:-/mnt/mishe-scratch/home-mesh-home}
WORK=${MIGRATION_WORK:-/mnt/mishe-scratch/mishe-migration}
SINCE=
SAMPLE=200
SAMPLE_BYTES=$((2 * 1024 * 1024 * 1024))
trees=()
while [ $# -gt 0 ]; do
    case "$1" in
        --since) SINCE=${2:?--since needs a value}; shift ;;
        --sample) SAMPLE=${2:?--sample needs a value}; shift ;;
        --sample-bytes) SAMPLE_BYTES=${2:?--sample-bytes needs a value}; shift ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) trees+=("$1") ;;
    esac
    shift
done
[ ${#trees[@]} -eq 0 ] && trees=(mishe-tauftauf .omp .codex)
[ -n "$SINCE" ] || SINCE=$(date -u -r "$WORK/bulk.log" +%FT%TZ 2>/dev/null)
[ -n "$SINCE" ] || { echo "no --since and no $WORK/bulk.log mtime to derive one" >&2; exit 2; }
# The changed list and the sample are evidence files; a verdict computed without
# them is a guess. Until 2026-11-17 a missing MIGRATION_WORK made every write fail,
# the comparisons ran on empty strings and the tree still printed VERIFIED.
[ -d "$WORK" ] || mkdir -p "$WORK" || { echo "cannot create work dir $WORK" >&2; exit 2; }
[ -w "$WORK" ] || { echo "work dir $WORK is not writable" >&2; exit 2; }

rc_all=0
for t in "${trees[@]}"; do
    echo "=== $t scoped verify $(date -u +%FT%TZ) since=$SINCE"
    s="$SRC/$t"; d="$DST/$t"
    # 1. size+mtime: what the delta pass should already have closed.
    delta=$(rsync -aHx --dry-run --itemize-changes "$s/" "$d/" 2>&1)
    n=$(printf '%s' "$delta" | grep -c '^[<>ch.*]' || true)
    # 2. changed since the bulk pass, by content.
    list=$WORK/verify-$(printf '%s' "$t" | tr -c 'A-Za-z0-9' _).changed
    ( cd "$s" && find . -xdev -type f -newermt "$SINCE" -print ) >"$list"
    changed=$(rsync -aHxc --dry-run --itemize-changes --files-from="$list" "$s/" "$d/" 2>&1)
    # Only file entries: rsync adds the parents of --files-from paths, and their
    # directory mtimes are not what this check is about (the size+mtime check above
    # covers the whole tree, directories included).
    nchanged=$(printf '%s' "$changed" | grep -c '^[<>ch]f' || true)
    nchanged_files=$(wc -l <"$list")
    # 3. deterministic sample of everything else: evenly spaced by path, capped by
    # cumulative bytes so a handful of huge logs cannot dominate the read.
    sample=$WORK/verify-$(printf '%s' "$t" | tr -c 'A-Za-z0-9' _).sample
    total=$(cd "$s" && find . -xdev -type f ! -newermt "$SINCE" -print 2>/dev/null | wc -l)
    step=$(( total / SAMPLE + 1 ))
    : >"$sample"
    acc=0
    while read -r p; do
        sz=$(stat -c %s "$s/$p" 2>/dev/null || echo 0)
        acc=$((acc + sz))
        # The cap stops a handful of huge logs from dominating the read, but it must
        # never leave the sample empty: a first sampled file larger than the cap used
        # to break before writing anything, and `nsampled=0` then passed as clean
        # while no content was compared at all (2026-11-17).
        [ -s "$sample" ] && [ "$acc" -gt "$SAMPLE_BYTES" ] && break
        printf '%s\n' "$p" >>"$sample"
    done < <( cd "$s" && find . -xdev -type f ! -newermt "$SINCE" -print 2>/dev/null | sort | awk -v k="$step" 'NR % k == 0' )
    nsample=$(wc -l <"$sample")
    [ -f "$list" ] || { echo "changed list $list is missing" >&2; exit 2; }
    [ -f "$sample" ] || { echo "sample file $sample is missing" >&2; exit 2; }
    sampled=$(rsync -aHxc --dry-run --itemize-changes --files-from="$sample" "$s/" "$d/" 2>&1)
    nsampled=$(printf '%s' "$sampled" | grep -c '^[<>ch]f' || true)
    # 4. counts and bytes, same filters as the copy.
    nsrc=$(find "$s" -xdev -printf . 2>/dev/null | wc -c)
    ndst=$(find "$d" -xdev -printf . 2>/dev/null | wc -c)
    bsrc=$(du -sb --apparent-size "$s" | cut -f1)
    bdst=$(du -sb --apparent-size "$d" | cut -f1)
    echo "  size+mtime-delta=$n changed-since=$nchanged_files files ($nchanged entries) sample=$nsample files ($nsampled entries)"
    echo "  entries-src=$nsrc entries-dst=$ndst bytes-src=$bsrc bytes-dst=$bdst"
    # Clause 3 is "a deterministic sample of the rest": with old files present, an
    # empty sample compares nothing and must not read as clean (it did until
    # 2026-11-17, when a size+mtime-preserving corruption passed as VERIFIED).
    nosample=0
    [ "$total" -gt 0 ] && [ "$nsample" -eq 0 ] && nosample=1
    if [ "$nosample" -eq 1 ]; then
        echo "  NOT-CLEAN $t: no file sampled from $total unchanged file(s)"
        rc_all=1
    elif [ "$n" -eq 0 ] && [ "$nchanged" -eq 0 ] && [ "$nsampled" -eq 0 ] &&
       [ "$nsrc" -eq "$ndst" ] && [ "$bsrc" -eq "$bdst" ]; then
        echo "  VERIFIED $t"
    else
        echo "  NOT-CLEAN $t"
        printf '%s\n%s\n%s\n' "$delta" "$changed" "$sampled" | head -20
        rc_all=1
    fi
done
echo "=== scoped verify done rc=$rc_all $(date -u +%FT%TZ)"
exit "$rc_all"
