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
  under I/O and memory contention, not write volume.
- Bounded, reversible experiment applied: an 8 GiB swapfile on
  `/mnt/mishe-scratch` at priority 0 (above the root file's -2) so new swapout
  goes to the idle drive. Baseline before it: 24 h = 7 stalls >= 5 s, worst
  8.46 s; revert is `swapoff /mnt/mishe-scratch/swap.img`.
- This is evidence for a candidate chain (reclaim/swapout to nvme0 → long
  fsync), not proof. If the stall rate does not fall, the hypothesis is
  falsified and the copy stays a relief bet. fstab persistence waits for that
  observation.

## Steps

1. Pre-check: ensure no active process using targets beyond shell; record `lsof`/`fuser` for each.
2. Quiesce writers before any copy: stop minds/wakes/observer and any process writing to `.omp`, `.codex`, `mishe-tauftauf`. Named units: mishe wake units, observer-checks, work-watch. Confirm via `lsof`/`fuser` that targets are idle.
3. Path users of `mishe-tauftauf` (inventoried 2026-11-16): because the cutover renames the source and
   leaves a **symlink at the original path** (steps 8-9), no path reference needs editing. Verified:
   - `~/.config/systemd/user`: 116 unit files name the checkout in `WorkingDirectory`, `ExecStart`
     and `Environment` (`PATH=…/.mishe-tauftauf/bin`, `PYTHONPATH=…/.mishe-tauftauf/runtime/current/src`).
     All resolve through the symlink unchanged.
   - `git`: `rev-parse --show-toplevel` = `/home/mesh-home/mishe-tauftauf`; delivery worktrees'
     `.git/worktrees/<sha>` gitdir points at the same absolute path and stays valid through the
     symlink (`git -C …/.mishe-tauftauf/runtime/current rev-parse HEAD` still works).
   - `/etc/systemd/system/mesh-router-phaedra-relay.service` names `~/.mesh/…` only — not moved.
   The realpath check is still run once after the flip (step 10) as a guard, not as a repointing task.
4. Causal caveat: stalls show no plant-load correlation and no SMART error. Moving ~85G is a relief bet, not a diagnosed fix. Keep root-stall detector armed; report whether stalls stop after move.
5. Create destination dirs: `/mnt/mishe-scratch/home-mesh-home/{.omp,.codex,mishe-tauftauf}`.
6. Copy, in two passes so the quiesced window is short: first `rsync -aHx SRC/ DST/` (bulk, safe while
   live — sources untouched, resumable), then under the step-2 quiesce the final
   `rsync -aHxc --itemize-changes SRC/ DST/` (checksummed delta). Do not run a heavy pass while a
   moving-pin delivery check is running: it competes for I/O and can push the check past its timeout
   (a wasted delivery window).
7. Verification after copy: `rsync -aHxc --dry-run --itemize-changes SRC/ DST/` MUST show zero changes; plus matching file count (`find ... | wc -l`) and `du -sb` match. All three must agree.
8. Rename source dirs to `*.pre-migration` (atomic, reversible): `mv .omp .omp.pre-migration` etc. Keep for rollback window.
9. Create symlinks: `ln -s /mnt/mishe-scratch/.../.omp /home/mesh-home/.omp` etc.
10. Restart affected services/processes; verify reads/writes to new path.
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