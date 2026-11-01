---
field: host-capacity-anon-reading-and-memory-stat
angle: misread
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity_reading.py
---

## What we read

Primary source, read this turn:

- Linux kernel documentation, "Control Group v2", Memory Interface Files, https://docs.kernel.org/admin-guide/cgroup-v2.html (fetched this turn; the first 100,000 of 141,089 characters, which include the whole memory section). Quoted:
  - memory.high: "Memory usage throttle limit." and "...the processes of the cgroup are throttled and put under heavy reclaim pressure."
  - memory.stat file: "Amount of memory used to cache filesystem data, including tmpfs and shared memory."
  - memory.stat shmem: "Amount of cached filesystem data that is swap-backed, such as tmpfs, shm segments, shared anonymous mmap()s".
  - The page does not say the file category is reclaimable. The only reclaimability wording in memory.stat is for slab_reclaimable.

Plant source read beside it:

- `src/mishe_tauftauf/host_capacity_reading.py:3-14` (module docstring): "cgroup memory.current includes reclaimable file cache. With memory.high set, the kernel reclaims cache to hold the slice near high, so current/high parks near 0.9 whether or not anything is wrong."
- `artifacts/signals-replay-20261101/replay.py`, read in full. It calls `hcr.decide_anon` on each recorded joined row (`for r, lr in joined`). This is the precondition from DM 159948, and it holds: the replay reads recorded rows, not docstrings. A grep for `refault`, `full60`, `psi`, `shmem` and `swap` in the driver returns nothing.
- `artifacts/signals-replay-20261101/result.json`, read in full. It is the driver's output and the source for every number below.

## What this plant already does

- `memory_stat_reading.py` reads anon, file and shmem as three separate Signals (`MEMORY_STAT_KEYS = ("anon", "file", "shmem")` in `memory_stat_text.py`). The plant already has the distinction the kernel text draws. The decision docstring does not use it.
- `host_capacity_reading.py` keeps anon, file and current apart and reports UNKNOWN for a missing part. The anon rule does not read file at all.

## What is new here

The docstring makes three claims. Each is checked against the kernel text and the replay.

1. **"memory.current includes reclaimable file cache."** Half right. `file` includes tmpfs and shared memory. The kernel calls that part swap-backed (`shmem`). Reclaiming it means swap, not dropping cache. So "reclaimable file cache" describes only the page-cache part of `file`. The anon rule ignores all of `file`, so it is wrong about the shmem share in either direction. The replay cannot measure that share: the driver reads only anon, file and current. Observation gap: the `shmem` key that `memory_stat_reading` already decodes. If the lifeline tape records it, the share is measurable; if not, it is UNKNOWN.

2. **"the kernel reclaims cache to hold the slice near high."** Not established by the kernel text, which says only that the group is "throttled and put under heavy reclaim pressure". The replay does not test the mechanism either. Its cache-only counterfactual (file pinned to 0.97 × high, anon unchanged, decisions identical) is true by construction: the anon rule has no file input. The counterfactual is an invariance check on the rule, not evidence about what the kernel does with cache.

3. **"current/high parks near 0.9."** Overstated. `result.json` `ratios_2026_11_01.current_over_high`: p50 0.777, p90 0.881, max 0.999. The median is 0.78. "Near 0.9" describes the upper tail, not the typical sample.

The replay numbers the docstring cites are consistent with `result.json`: 33 starts on the current rule (`same_code_total_current_starts`) and 14 on the anon rule (`anon_replay_starts`). Both counts are on joined rows only: 15,384 of 47,117 host rows, 33%. The docstring says "on the same rows", which is true; it should also say "joined rows only".

Provenance gaps in the artifact:

- `REPLAY.md` states "17,950 anon vs 1,508 file refault deltas" and "full60 >= 25" for the 14 residual starts. Neither figure is produced by `replay.py` or `result.json`. The docstring then repeats "PSI memory stalls with anon refaults dominant" as a finding. As the artifact stands, that claim cannot be checked.
- `REPLAY.md` and the census trace give the join as 15,378 rows. `result.json` gives 15,384. The file the driver wrote is the authority.
- `REPLAY.md` gives the same-code denominator as 8,899. The `result.json` state counts sum to 8,905 (same-code 5,505 + 3,400; anon 8,905). The 3,400 → 1,772 totals are right; the denominator is not.
- Fidelity: 10,831 of 47,117 state mismatches (23.0%), so about 77% reproduce. This matches the artifact.

## Application

target: src/mishe_tauftauf/host_capacity_reading.py

Proposal for health (owner of the host-capacity controller). Docstring only, no behaviour change, and only this file: `host_capacity.py` is dirty in the working tree and is not part of this proposal.

- Replace "cgroup memory.current includes reclaimable file cache" with: "memory.current includes file memory. The kernel's file category is cached filesystem data including tmpfs and shared memory; the shmem share is swap-backed, so file is not all droppable cache."
- Replace "the kernel reclaims cache to hold the slice near high" with "not established by the kernel documentation; not tested by the replay".
- Replace "current/high parks near 0.9" with the measured distribution: "on the 2026-11-01 joined rows current/high p50 0.78, p90 0.88 (result.json, ratios_2026_11_01)."
- Say "joined rows only" beside the 33 → 14 counts, and cite `result.json`, not `REPLAY.md`, for the numbers.
- Mark the PSI refault claim as unverified until a driver writes it out, or remove it from the docstring.

Test before landing: `python3 -m py_compile src/mishe_tauftauf/host_capacity_reading.py` and `tests/test_host_capacity_reading.py` pass unchanged. A docstring edit needs no new test.

Closure, per field `host-capacity-anon-reading-and-memory-stat`: this review closes only the misread of the docstring's claims. Wiring the readers into the controller, or retiring them with their tests, remains open. A shmem measurement, if the lifeline tape carries it, would be the evidence that decides claim 1.

## Limit

This review does not verify the replay numbers by rerunning the driver. It checks the code path, the output file and their agreement. It does not judge whether the anon rule is a better controller rule. The kernel text was read to the 100,000-character offset, which covers the memory section.
