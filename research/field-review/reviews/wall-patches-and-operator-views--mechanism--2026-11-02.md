---
field: wall-patches-and-operator-views
angle: mechanism
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/wall_patch.py
---

# Wall patches and operator views — mechanism: durable atomic file replacement needs fsync before and after replace

## What we read

- SQLite, "Atomic Commit In SQLite", https://www.sqlite.org/atomiccommit.html — fetched 2026-11-02 as /tmp/sqlite-atomiccommit.html 77968 B sha256 5a5be7b660217c9408c8a81eaf2faa2d08832ed3790f895a0a713916ad856ea1, Last-Modified Wed, 28 Oct 2026 (HTTP), 1562 lines HTML, text extracted by stripping tags and unescaping entities to ~35k characters. The canonical single-file commit doc (not the WAL doc); supports atomic commit in rollback-journal mode and separately in WAL mode (§1 "Introduction" p.2).
- Passages read verbatim (plain text after tag stripping):
  - §2 Hardware Assumptions p.4: "SQLite assumes that the operating system will buffer writes and that a write request will return before data has actually been stored in the mass storage device. SQLite further assumes that write operations will be reordered by the operating system. For this reason, SQLite does a \"flush\" or \"fsync\" operation at key points. SQLite assumes that the flush or fsync will not return until all pending write operations for the file that is being flushed have completed."
  - §3.7 Flushing The Rollback Journal File To Mass Storage: "The next step is to flush the content of the rollback journal file to nonvolatile storage. As we will see later, this is a critical step in insuring that the database can survive an unexpected power loss. This step also takes a lot of time, since writing to nonvolatile storage is normally a slow operation. This step is usually more complicated than simply flushing the rollback journal to the disk. On most platforms two separate flush (or fsync()) operations are required. The first flush writes out the base rollback journal content. Then the header of the rollback journal is modified to show the number of pages in the rollback journal. Then the header is flushed to disk."
  - §3.10 Flushing Changes To Mass Storage + §3.11 Deleting The Rollback Journal: "Another flush must occur to make sure that all the database changes are written into nonvolatile storage. This is a critical step to ensure that the database will survive a power loss without damage." / "After the database changes are all safely on the mass storage device, the rollback journal file is deleted. This is the instant where the transaction commits."
  - §3.11 appearance of atomicity: "Deleting a file is not really an atomic operation, but it appears to be from the point of view of a user process. A process is always able to ask the operating system \"does this file exist?\" and the process will get back a yes or no answer."
  - §7.4 context for single-page shortcut not used here (sector-write atomicity via xDeviceCharacteristics, default not indicated on Unix/Windows so the two-flush path is the one to implement).
- Limits of this reading: one HTML reference doc (not a peer-reviewed paper), describing SQLite's rollback-journal commit, not the plant's Python tmp+replace; no controlled experiment cited here — the mechanism is a stated hardware-assumption model with explanatory diagrams. Transfer to `wall_patch.restore` is analogy, and power-loss durability on this host's FS was not measured.

## What this plant already does

- The patch gate lives in `src/mishe_tauftauf/wall_patch.py` (`restore` at :44–56, `snapshot` at :35–38, `_apply`/:284–329, `_revert`/:246–269, `verify`/:332–368). `restore(path, value)` writes the scoped bytes via a sibling temp file then `temporary.replace(path)` — it calls `temporary.write_bytes(base64.b64decode(value["bytes"]))` and `temporary.chmod(value["mode"])` then replaces, with no `fsync` of the temp file and no `fsync` of the parent directory.
- The publication preflight beside it does it durably. `src/mishe_tauftauf/post_check.py:_save` at :61–77 implements the textbook sequence from the doc: `mkstemp` in the parent directory, `stream.flush()` + `os.fsync(stream.fileno())`, `os.replace(name, path)`, then `os.open(parent, O_RDONLY)` + `os.fsync(directory)` + close. The same file system holds both `patches/*.json` records and runtime scoped files, so the OS buffering/reordering assumption (§2) applies equally.
- The gate's tests in `tests/test_wall_patch.py` cover exact-bytes, mode preservation, review hash binding and revert/reapply, but do not observe durability — no test asserts that an `os.fsync` occurs before `replace` or that the directory is synced after, and the dashboard/CI never checks that a patch would survive a crash between `replace` and the next sync.
- Predecessor mechanism for this field (2026-10-31 via Shneiderman 1996, `wall.py` filtered-view counts) proposed counting what a filtered pane hid. That mechanism is presentation — "state how much you hid" — and is unchanged by this review. This review proposes no header text, no new filtered count, and no wall/pane layout change.

## What is new here

- SQLite's mechanism is not "write then rename". It is "flush, mutate header, flush again, write DB, flush DB, then delete the journal, and the delete appears atomic because existence is a binary query". The cost is explicitly two `fsync()`s around the journal header (the journal is valid only after the second) and a `fsync` of the DB and of the directory after the replace/delete — the doc calls the journal flush "a lot of time" and says both flushes "take up most of the time required to complete a transaction commit".
- The plant has the "write then rename" half without the two `fsync`s that make it atomic-after-crash. Under the §2 assumptions (OS buffers, reorders), a `replace` that has not been `fsync`'d can be reordered behind later writes or lost on a crash, so a patch that the gate reports as `applied` can appear not-applied after reboot. The journal/header two-flush pattern maps directly: the temp file's content is the journal body (first fsync), `chmod`/header fixup is the header mutation (second fsync on the same fd), the `replace` is the delete-as-commit, and the directory `fsync` is what makes the commit durable.
- Distinct from predecessor: Shneiderman → "overview first, zoom and filter, then details-on-demand" (taxonomy of visualization tasks, no durability claim) → `wall.py` header counts. This review → SQLite §2 + §3.7/3.10/3.11 two-phase `fsync` + directory `fsync` (atomic-commit durability protocol, hardware-assumption section with `xDeviceCharacteristics`) → `wall_patch.py:restore`. Different author/venue/year, different hardware/software boundary, different mechanism text, different target file and code path.

## Application

target: src/mishe_tauftauf/wall_patch.py

Proposed change to the owner (genome). Durability only; no new coordination mode, no admission rule change, no new patch phase, no FS probing for sector size:

1. In `wall_patch.py:restore`, after `temporary.write_bytes(...)` and `temporary.chmod(value["mode"])`, `fsync` the temporary file's bytes to storage before the replace, then `fsync` the parent directory after the replace — the same two calls `post_check._save` already makes. Minimal form (imports `os` already via `wall_patch` or add it):

```python
def restore(path, value, expected=_UNSET):
    current = snapshot(path)
    if expected is not _UNSET and current != expected:
        raise ValueError(f"runtime target changed before replacement: {path}")
    if value is None:
        if current is not None:
            path.unlink()
            # directory fsync for unlink durability (as _save does for replace)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + ".wall-patch")
        temporary.write_bytes(base64.b64decode(value["bytes"]))
        temporary.chmod(value["mode"])
        # SQLite §3.7: flush file before commit so crash cannot leave torn content
        with open(temporary, "rb") as f:
            os.fsync(f.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
```

2. Keep the existing `ValueError` on `expected` mismatch and the `replace`-as-atomic appearance from §3.11 (existence is the commit query). Do not change `snapshot`, `lock`, `delivery_plan`, `patch_hash`, or the `verify` revert/observe flow — `verify` already exercises revert then reapply.
3. Tests in `tests/test_wall_patch.py` (additive, isolated, deterministic): mock `os.fsync` and `os.open`/`os.close`; assert `restore` calls `fsync` on the temp fd at least once before `replace` and calls `fsync` on the directory fd after `replace` (and after `unlink` when removing); failure to call either makes the test fail. Also assert that `post_check._save`-style error handling (existing name cleaned up) is preserved if `fsync` raises.
4. Observe after activation under the reviewed patch gate: run the existing `test_wall_patch.py` suite green, `python tools/check_parsing.py` unchanged, and a staged-replace manual check shows `wall_patch.restore` leaves the new bytes after a simulated crash boundary is approximated by `sync` — the gate still reports `applied` and `verify` still flips `applied → reverted → applied` with `delivery_verified` only after the second observation.

Prediction: patches report `applied` only when their bytes are durable, matching SQLite's "all pen changes are safely on storage before the journal delete is the commit". Disposition: keep if the mocked-fsync tests pass and no existing test regresses; revert the two `fsync` blocks if the gate's latency grows unacceptably on the host FS or if any `os.fsync` path raises on a platform where the directory fd cannot be opened, and re-propose with a guarded directory-sync.

## Limits

- One SQLite doc, rollback-journal path; the plant does not use a journal file, so the two-flush pattern is mapped onto one temp file + chmod rather than journal body + header — the mapping is interpretive, and WAL-mode shortcuts (§7.4 sector atomicity) are deliberately not imported.
- The fix costs a synchronous `fsync` per scoped file on every apply/revert; the doc warns this "takes up most of the time" — latency blame now sits on the patch path, and no power-loss test is included here to prove durability on this host's FS (only the mocked-fsync contract is tested).
- No change to `post_check.py` (already durable) and no change to the pane layout from the Shneiderman predecessor — this is additive durability beside that presentation fix.
