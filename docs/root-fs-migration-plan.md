# Root-drive migration cutover plan (shape A)

Source root: `/` on `/dev/mapper/ubuntu--vg-ubuntu--lv` (nvme0, 914G).
Destination: `/mnt/mishe-scratch` on `/dev/nvme1n1p1` (ext4, 234G).

Move targets (re-measured 2026-11-17 02:58Z, total ~87G, fits 218G free):
- `/home/mesh-home/.omp` (33G)
- `/home/mesh-home/.codex` (23G)
- `/home/mesh-home/mishe-tauftauf` (31G; the plan's original 19G is stale — the checkout grew)

OS, /boot, /boot/efi, `/home/mesh-home/.mesh` (relay/router state) stay on nvme0.
The destination already holds `/mnt/mishe-scratch/mishe-cargo-target` (the Rust build cache that
`cd79beaf` moved off root) — do not wipe it, and account for its space.

## Measured state at 2026-11-16 06:28Z

- root-fs stalls >= 5 s: 18 in the last 24 h, all fsync-dominated, worst 16.97 s
  (`checks/root-fs-stall.py`; detector supervised as `mishe-dual-probe.service`). This is a
  true-positive host condition — the drive, not a plant writestorm (`create` ~0, `fsync` = total).
- The checkout is on the *same* LVM/nvme0 as root, so the whole 85 G is on the DRAM-less drive.
- 116 user unit files under `~/.config/systemd/user` reference the checkout path. **The retained
  symlink keeps every one valid** — see step 3.

## Measured 2026-11-17 07:21Z: the swapfile is on the stalling drive

- `/proc/swaps` had one swapfile, `/swap.img`, on the same
  `ubuntu--vg-ubuntu--lv` (nvme0) as root, and it was 98.3 % used (8.0 GiB of
  8.4 GiB). The destination drive `/mnt/mishe-scratch` (nvme1n1) was idle.
- The 07:06:26Z stall (5.43 s, `fsync` 5.43, `create` 0.0001, io_psi 21.17,
  mem_psi 5.18) is the first stall with a memory-pressure sample; the next two
  probes in that span recorded mem_psi 18.72 and 22.03. It was one fsync waiting
  under I/O and memory contention, not write volume. (Superseded in part by the
  07:53Z measurement below: across the whole population memory pressure is not
  required for a slow root fsync; I/O pressure is.)
- Bounded, reversible experiment applied: an 8 GiB swapfile on
  `/mnt/mishe-scratch` at priority 0 (above the root file's -2) so new swapout
  goes to the idle drive. Baseline before it: 24 h = 7 stalls >= 5 s, worst
  8.46 s; revert is `swapoff /mnt/mishe-scratch/swap.img`.
- Routing is now **confirmed** (census 07:27:42Z → 08:00:32Z, 32 min of probe
  records): `pswpout` grew 84792 pages (331 MiB); `/mnt/mishe-scratch/swap.img`
  used went 0 → 301 MiB while `/swap.img` on nvme0 *fell* 7808 → 7736 MiB. New
  swapout goes to the fast drive, and the root swapfile even gave pages back.
  So the priority-0 file is doing exactly what it was added for.
- The candidate chain itself (reclaim/swapout to nvme0 → long root fsync) is not
  what produces the stall class: the 07:41-07:57Z measurement below shows a
  purely I/O-bound fsync cliff with `psi_mem 0.0`. Keep the fast swapfile as
  headroom and containment (the root file is 94 % full and cannot grow), not as
  the remedy; the remedy is the move. fstab persistence is a small separate step
  if the operator wants it to survive reboot.

## Measured 2026-11-17 07:24Z: the coincident load was Rust builds on the other drive

- Six `nice-19 cargo build --release` jobs from release `16df940c` started at
  07:04:30-31 with target dir `/mnt/mishe-scratch`; at the stall the host had
  23 GB buff/cache, 2 GB free and both old swap files full. That is the load the
  probe sampled as mem_psi 5.18/18.72/22.03.
- The build outputs landed on nvme1n1 (all `mishe-cargo-target/*/release`
  artifacts dated 07:04), and the probe's own nvme1n1 directories
  (`/var/tmp`, `/mnt/mishe-scratch/probe`) recorded no slow probe in the
  07:00-07:10Z window — they have 3 and 4 slow probes in the log's whole
  history. The root probe (`/var/lib/mishe-rootprobe`, nvme0) has 276 slow
  probes in 24 h, 7 of them >= 5 s. So the stall is specific to the nvme0 root
  LV path, which also carries the site home, the journal and `/swap.img`.
- Census before the swapfile experiment: per UTC hour (root probes >= 0.3 s,
  `>= 5 s` marked): 11-16 12Z 32, 13Z 38 (3), 14Z 12, 19Z 5 (1), 21Z 16, 22Z 15;
  11-17 02Z 18, 03Z 28, 05Z 27 (1), 07Z 6 (1). Total 24 h: 276 slow, 7 >= 5 s.
- The probe now also logs `{"event": "swap"}` every 10 s with `pswpin`,
  `pswpout`, `swap_free_kib` and each swap file's used KiB
  (`operator/dual_probe.py` in the site home, restarted 07:27:42Z, verified: two
  records, `/swap.img` 7995808 KiB used, the nvme1 file 0). A future stall can
  therefore be read against swapout directly: pages charged to `/swap.img`
  support the hypothesis, a flat `/swap.img` beside pswpout growth does not.

## Measured 2026-11-17 07:41-07:57Z: the root device itself is 20-100x slower

Controlled, reversible experiment (test files deleted afterwards) with the probe
running as the instrument. All commands `nice -n 19 ionice -c 3`.

- **1 GiB `dd bs=4M conv=fsync` on the root LV** (`/var/lib/mishe-rootprobe`):
  took 68.10 s = **15 MiB/s**. During it the probe recorded eight root fsyncs of
  6.51, 8.00, 10.23, 9.79, 10.06, 9.99 and 13.39 s, every one with
  `psi_mem 0.0` and `psi_io` rising 17.5 → 91.2. The same burst on
  `/mnt/mishe-scratch` took **1.00 s**, and the probe's `scratch` directory worst
  was 0.068 s. Identical load, opposite result, no memory pressure on either.
- **Repeatability and confound checks**, root LV each time: buffered 67.8 s,
  `oflag=direct` 67.45 s, and the same write as root outside the contained
  `mishe-plants.slice` 67.6 s. So it is not the page cache and not the cgroup
  (`io.weight` only, no `io.max`).
- **Raw partition reads** (64 x 4 MiB `iflag=direct`, deep offset, output
  `/dev/null`): `/dev/nvme0n1p3` 62 MiB/s vs `/dev/nvme1n1p1` 1280 MiB/s. The
  gap survives bypassing ext4 and dm, so it is the device path, not the
  filesystem.
- **Population check**: of 273 root-dir probe events with `fsync >= 0.3 s` in
  the last 24 h, **251 had `psi_mem < 0.01`**; the seven `>= 5 s` events all
  have `psi_io` 14.6-50.8. Memory pressure is not necessary for this latency.
- **Hardware state, not wear**: `nvme smart-log` for nvme0: 26 % used, 81.9 TB
  written, spare 99 %, `media_errors 0`, no error-log entries, temps 57-66 °C,
  zero thermal-throttle transitions, PCIe link 16 GT/s x4. The other drive is a
  DRAM-less MAXIO MAP1608 negotiated at 8 GT/s x4 yet 20x faster on raw reads.
  The weekly `fstrim` ran 2026-11-16 01:17 and trimmed 129.4 GiB on `/`, so
  discard is not the missing piece.
- **Consequence for scheduling and for the probe**, not just for the copy: any
  root writeback backlog drains at ~15 MiB/s, so 1 GiB of root-resident dirty
  data costs about 68 s of fsync tail for anything that fsyncs on root — the
  site-home journal, git/index writes, delivery checks, and the probe's own
  one-second fsync. That is the incident mechanism; the six cargo builds of
  07:04Z were load on the same host, and the build cap now in `645d71c3` reduces
  load but cannot raise this number.

## Measured 2026-11-17 08:29-08:35Z: the delta pass, and what step 7 really costs

- Live size+mtime delta 30 min after the bulk pass (dry run 08:29:03Z): checkout
  2,133 files / 1.00 GB in 27 s, `.omp` 21 files / 15 MB, `.codex` 28 files /
  **5.95 GB**. Then the real delta pass, 08:30:45-08:33:05Z, log
  `/mnt/mishe-scratch/mishe-migration/delta-live-0827.log`: all three rc=0 in
  139.9 s — checkout 1.007 GB at 24.7 MB/s (2,156 small files), `.omp` 12 MB,
  `.codex` 5.95 GB at 63.7 MB/s.
- The checkout delta is almost one new release tree, `releases/0ccfd809…`
  (delivered 08:23Z, between wakes), plus `signals/` and `wakes/`. **Every
  delivery adds ~1 GB to the destination delta**, so the flip must not straddle
  a delivery. A wake-held window is delivery-free by construction (delivery
  waits for wakes to end), which puts delta and flip in the same wake.
- `.codex` churn is 5.95 GB in 28 files: `logs_2.sqlite`, `state_5.sqlite`,
  `thread_history_1.sqlite` and their `-wal`/`-shm`. Cheap to re-copy, but
  `.codex` cannot be verified clean while a coder CLI runs.
- 1.5 min after the delta the checkout already showed 1,812 changed files /
  385 MB, and 229 MB of that is `chat.log` alone, then
  `recovery/host-samples.jsonl` (27 MB), `feed.identities.sqlite3` (24 MB),
  `operator/unit-psi.jsonl` (21 MB). Local rsync defaults to `--whole-file`, so
  one appended line in a 229 MB log re-copies the whole file. Quiesce **before**
  the delta; the delta must be the last write before the flip.
- No mind was running but this one, and the site home still gained 1,785 files
  in ten minutes: ~40 producer timers write signals and journals. The step-2
  quiesce list must include them, plus dispatch, the probe and the bots — not
  only wakes/observer/work-watch.
- **Step 7's cost:** a checksummed verify over all three trees reads 89.4 GB on
  the source. Measured source read is 62-64 MB/s (raw partition read above; the
  `.codex` delta sustained 63.7 MB/s), so the full `-c` pass alone is
  **~23-24 min** — it does not fit the 35-minute wake cap alongside delta and
  flip.

## Measured 2026-11-17 08:47-08:53Z: the cutover scripts, self-tested

- `tools/root-migration-flip.sh --dry-run` prints the preflight readings (pin, source and
  destination HEAD, its own sha256 against the committed copy) and the stop list, then
  stops. Measured: **94 units**, of which 71 `mishe-producer-*.timer`, 10
  `mishe-release-review-retry-*`, the three bots, `mishe-dual-probe`,
  `mishe-unit-psi-sampler`, `mishe-next-due-*`, `mishe-doctor.timer`,
  `mishe-ifm-shim`, `mishe-audio-listener`, `mishe-note3-sampler`,
  `mishe-human-inbound-relay.timer`, `plant-mishe-ui` and `plant-mishe-revive.timer`.
- The first version found only 20 units: a producer's *service* names the checkout but is
  inactive between runs, so the active unit is its timer and the timer file names no path.
  Reading each timer's `Unit=` target is what makes the list complete. Without it the delta
  would have run with 71 producers still writing the site home.
- `tools/root-migration-verify-scoped.sh` self-tested against a synthetic source/destination
  pair (`MIGRATION_SRC`/`MIGRATION_DST`/`MIGRATION_WORK` overrides exist for this): a clean
  copy prints `VERIFIED`; content corrupted while preserving size and mtime is caught both
  in the changed-since set and in the sample; an extra destination file is caught by the
  count and byte checks. Two defects found and fixed by the tests: a sample stride of 1
  selected nothing (`NR % 1 == 1`), and `--files-from` adds the parent directories of the
  listed files, whose mtimes were being counted as content differences — the checksum
  stages now count file entries only.
- Live run on the unquiesced `.omp`: 9.3 s, 27 size+mtime churn entries, 22 file entries
  changed since the bulk pass, and **0 content mismatches in a 199-file sample**. That is
  the intended discrimination: churn is reported, corruption is not invented.
- Not run: the flip itself. It waits on the reviewer's answer about the step-7 shape and a
  wake window that can hold quiesce + delta + verify + cutover.

## Steps

1. Pre-check: ensure no active process using targets beyond shell; record `lsof`/`fuser` for each.
2. Quiesce writers before any copy: stop minds/wakes/observer and any process writing to `.omp`, `.codex`, `mishe-tauftauf`. Named units: mishe wake units, observer-checks, work-watch — **and the ~40 `mishe-producer-*.timer` units, `mishe-next-due-*.timer`, dispatch and the bots** (measured 08:29Z: the site home gained 1,785 files in ten minutes with no mind running). Confirm via `lsof`/`fuser` that targets are idle.
3. Path users of `mishe-tauftauf` (inventoried 2026-11-16): because the cutover renames the source and
   leaves a **symlink at the original path** (steps 8-9), no path reference needs editing. Verified:
   - `~/.config/systemd/user`: 116 unit files name the checkout in `WorkingDirectory`, `ExecStart`
     and `Environment` (`PATH=…/.mishe-tauftauf/bin`, `PYTHONPATH=…/.mishe-tauftauf/runtime/current/src`).
     All resolve through the symlink unchanged.
   - `git`: `rev-parse --show-toplevel` = `/home/mesh-home/mishe-tauftauf`; delivery worktrees'
     `.git/worktrees/<sha>` gitdir points at the same absolute path and stays valid through the
     symlink (`git -C …/.mishe-tauftauf/runtime/current rev-parse HEAD` still works).
   - `/etc/systemd/system/mesh-router-phaedra-relay.service` names `~/.mesh/…` only — not moved.
   - Sandboxing (checked 2026-11-17 07:57Z across all 110 user units naming the
     checkout): no `ProtectHome`, `ProtectSystem`, `ReadOnlyPaths`, `BindPaths` or
     `InaccessiblePaths`; the only sandbox directive is `PrivateTmp=true` in three
     units. So a destination under `/mnt` is reachable from every unit, and nothing
     narrows the view to `/home` in a way a symlink would defeat. Re-run this check
     if units change before the flip.
   The realpath check is still run once after the flip (step 10) as a guard, not as a repointing task.
4. Causal status (2026-11-17 07:57Z): diagnosed to the device path, not to a plant
   writestorm and not to swap. The root LV sustains ~15 MiB/s writes and ~62 MiB/s
   reads against ~1.3 GiB/s reads on nvme1n1, measured raw and through ext4, inside
   and outside the plant slice, with no memory pressure (measurement above). Moving
   the 87 G of writers is therefore a diagnosed remedy for the *stall class*, while
   SMART is clean (26 % used, 0 media errors). Acceptance after the move: root-dir
   probe events with `fsync >= 0.3 s` drop toward the nvme1n1 rate (3-4 ever), and
   the recurrence detector stays armed. If stalls continue after the move, the
   remaining root writers are journal and `.mesh` (left in place by design) and
   any page still swapping from the old `/swap.img`; routing is already fixed
   (see the 07:21Z section), so the next suspect is the journal's `Storage=`
   (currently the default `auto`, i.e. persistent, 3.0 G in `/var/log/journal`).
   Device-throughput watch (run when stalls recur or monthly; 1 GiB each, safe,
   delete the files):
   `dd if=/dev/zero of=/var/lib/mishe-rootprobe/t.bin bs=4M count=256 oflag=direct`
   and `sudo dd if=/dev/nvme0n1p3 of=/dev/null bs=4M count=64 skip=100000 iflag=direct`
   — a further drop in those numbers is hardware, not load.
5. Create destination dirs: `/mnt/mishe-scratch/home-mesh-home/{.omp,.codex,mishe-tauftauf}`.
6. Copy, in two passes so the quiesced window is short: first `rsync -aHx SRC/ DST/` (bulk, safe while
   live — sources untouched, resumable), then under the step-2 quiesce the final delta
   `rsync -aHx --itemize-changes SRC/ DST/` (add `-c` only in the full-verify shape of step 7). Do not run a heavy pass while a
   moving-pin delivery check is running: it competes for I/O and can push the check past its timeout
   (a wasted delivery window). A wake holds dispatch, and delivery waits for wakes to end, so a
   wake-held slot *is* the safe window for the bulk pass.
   **First pass ran 2026-11-17 07:53:47Z-08:19:46Z** as transient user unit
   `mishe-root-migration-bulk` (`IOWeight=10`, `Nice=19`), script
   `/mnt/mishe-scratch/mishe-migration/bulk-copy.sh`, log
   `/mnt/mishe-scratch/mishe-migration/bulk.log`; order checkout → `.omp` → `.codex`,
   all three rc=0. The live delta pass 08:30:45-08:33:05Z then closed 6.97 GB of
   churn (see the 08:29Z section); the unit is inactive and gone.
   Observed rate 55-63 MB/s: the checkout finished rc=0 at 08:01:58Z with
   30,925,374,169 bytes copied of 30,954,635,352 on the source (the 30 MB gap is
   live churn — sqlite `-shm`/WAL, walls — and the delta pass closes it). Watch a pass with
   `tail -2 …/bulk.log` or `…/delta-live-*.log`.
   The copy itself does not create the stall class: with it running the root probe
   recorded 2 slow events in 10 min, worst 1.11 s (vs 8 events ≥ 5 s during the
   deliberate 1 GiB `dd` bursts), so the extra load is reads, not a root writeback
   backlog.
7. Verification after copy — two shapes; a fresh reviewer who is not the author
   accepts the shape before the flip runs, because the flip moves the delivery
   worktrees and the pinned release (high-impact). Both shapes are implemented;
   the flip script calls the one it is told to use.
   - **Full:** `rsync -aHxc --dry-run --itemize-changes SRC/ DST/` shows zero
     changes, file counts equal, `du -sb` equal
     (`/mnt/mishe-scratch/mishe-migration/verify-copy.sh [tree …]` prints
     `VERIFIED`/`NOT-CLEAN`). Measured cost ~23-24 min of source reads (89.4 GB
     at 62-64 MB/s), so it cannot run inside a wake: it needs a transient unit
     with the plant quiesced across a wake gap. `flip.sh --full` selects it.
   - **Scoped (recommended, fits one wake):** after the step-2 quiesce and a
     final non-checksummed delta, check (i) exhaustive size+mtime equality — the
     dry run reports zero transfers; (ii) content equality with `-c` for every
     file changed since the bulk pass; (iii) a random content sample of the rest;
     (iv) file counts and `du -sb` equal. Implemented as
     `tools/root-migration-verify-scoped.sh` (deployed to
     `/mnt/mishe-scratch/mishe-migration/verify-scoped.sh`), which is what
     `flip.sh` runs by default. `--since` defaults to `bulk.log`'s mtime, the
     sample is evenly spaced by path and capped at 2 GiB of reads.
     rsync verifies the whole-file checksum of every file it transfers and the
     bulk pass finished rc=0, so what full `-c` adds over this is post-write disk
     corruption on untouched files.
   Either way verification can only pass on a quiesced tree; a nonzero delta
   outside one is live churn, not a copy defect. Measured 2026-11-17 08:49Z on
   the live `.omp`: 27 churn entries and 0 content mismatches in a 199-file
   sample, i.e. the check separates churn from corruption as intended.
8. Rename source dirs to `*.pre-migration` (atomic, reversible): `mv .omp .omp.pre-migration` etc. Keep for rollback window.
9. Create symlinks: `ln -s /mnt/mishe-scratch/.../.omp /home/mesh-home/.omp` etc.
10. Restart affected services/processes; verify reads/writes to new path.
    Steps 8-10, plus the step-2 quiesce, the final delta and the step-7 verify, are one
    script: `tools/root-migration-flip.sh`. It runs from
    `/mnt/mishe-scratch/mishe-migration/flip.sh` (its own path must survive the rename)
    and refuses to start if the deployed copy's sha256 differs from the committed one.
    `--dry-run` is the default and prints the stop list and preflight readings without
    changing anything; `--execute` performs the cutover. Before anything is stopped it
    arms a watchdog transient unit (`mishe-root-migration-watchdog`, 900 s) that restores
    a working plant if the flip dies: it rolls the rename back when the symlink
    half-happened and restarts the recorded units otherwise. The stop list is recorded in
    `flip.units` and restarted from that same file.
11. After 24h stable: removal of `*.pre-migration` is deletion of data. Requires explicit human approval; not run by default. Until approved, keep pre-migration dirs.

## Rollback

- Remove symlink, `mv *.pre-migration original`. Re-run rsync back if needed. No data loss as long as pre-migration dirs remain.

## Checks after each step

- `df -h / /mnt/mishe-scratch`
- `readlink -f` targets
- service health signals (work-watch, observer-checks) remain GREEN
- root-stall detector stays armed

## Not doing

- No partition resize, no OS move, no purchase (shape B).
- The reading confirmation this plan asked for is in hand: Ilya's standing approval of 2026-11-11
  13:05Z ("go ahead with proposed root-fs fix/change. mesh-home machine and note3 are in full
  mishe's control. you have explicit approval to go ahead", `SITE_HOME/operator/standing-approvals.md`).
  Movement is Mishe's to schedule as a host operation with the step-2 quiesce — do not run the bulk
  copy inside a moving-pin delivery window (it competes for root I/O and can time the check out).
- No deletion of `*.pre-migration` without explicit human approval.