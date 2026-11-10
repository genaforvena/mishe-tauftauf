# Root-drive migration cutover plan (shape A)

Source root: `/` on `/dev/mapper/ubuntu--vg-ubuntu--lv` (nvme0, 914G, 41% used, 525G free).
Destination: `/mnt/mishe-scratch` on `/dev/nvme1n1p1` (ext4, 234G, 2% used, 219G free).

Move targets (total ~75G, fits 219G free):
- `/home/mesh-home/.omp` (33G)
- `/home/mesh-home/.codex` (23G)
- `/home/mesh-home/mishe-tauftauf` (19G)

OS, /boot, /boot/efi stay on nvme0.

## Steps

1. Pre-check: ensure no active process using targets beyond shell; record `lsof`/`fuser` for each.
2. Quiesce writers before any copy: stop minds/wakes/observer and any process writing to `.omp`, `.codex`, `mishe-tauftauf`. Named units: mishe wake units, observer-checks, work-watch. Confirm via `lsof`/`fuser` that targets are idle.
3. Realpath users of `mishe-tauftauf` (list exact fix for each before moving):
   - runtime/current pin: resolve `readlink -f` and update pin to new path (or keep via symlink).
   - unit files: grep `mishe-tauftauf` in `/etc/systemd/system` and user units; update `WorkingDirectory`/paths.
   - PYTHONPATH entries resolving through symlinks: enumerate and repoint.
   - git toplevel: `git -C /home/mesh-home/mishe-tauftauf rev-parse --show-toplevel`; ensure worktree path updated or symlink preserves it.
4. Causal caveat: stalls show no plant-load correlation and no SMART error. Moving ~75G is a relief bet, not a diagnosed fix. Keep root-stall detector armed; report whether stalls stop after move.
5. Create destination dirs: `/mnt/mishe-scratch/home-mesh-home/{.omp,.codex,mishe-tauftauf}`.
6. `rsync -aHxc SRC/ DST/` for each (preserve perms/xattrs, checksums).
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
- No movement until human confirms reading this plan.
- No deletion of `*.pre-migration` without explicit human approval.