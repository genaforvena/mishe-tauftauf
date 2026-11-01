---
field: host-capacity-anon-reading-and-memory-stat
angle: recent
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity_reading.py
---

## What we read

Primary source, read this turn:

- Linux kernel documentation, "Control Group v2", current page at https://docs.kernel.org/admin-guide/cgroup-v2.html (fetched this turn; sections "Memory Interface Files": memory.high, memory.current, memory.stat, memory.pressure). The page is the kernel's own text, not a summary. It is the current document, not a 2023–2026 result: a search this turn (WebSearch, standard) returned only older kernel patch threads, and one 2025-era documentation patch by Tejun Heo that I did not open, so that patch is not part of this reading.

Passages read and what they concede:

- memory.high: "Memory usage throttle limit. If a cgroup's usage goes over the high boundary, the processes of the cgroup are throttled." and "Going over the high limit never invokes the OOM killer and under extreme conditions the limit may be breached." The doc calls memory.max the "hard limit".
- memory.current: "The total amount of memory currently being used by the cgroup and its descendants." The page does not say in this entry whether page cache is included. Its Memory section lists tracked usage as "Userland memory - page cache and anonymous memory", which implies it is.
- memory.stat anon: "Amount of memory used in anonymous mappings such as brk(), sbrk(), and mmap(MAP_ANONYMOUS)." file: "Amount of memory used to cache filesystem data, including tmpfs and shared memory." The page does not say whether file memory is reclaimable.
- memory.pressure: "Shows pressure stall information for memory." Pointer to the PSI accounting document.

Plant text read beside it, this turn:

- `src/mishe_tauftauf/host_capacity_reading.py:3-7`: the module docstring says "cgroup memory.current includes reclaimable file cache. With memory.high set, the kernel reclaims cache to hold the slice near high, so current/high parks near 0.9 whether or not anything is wrong."
- `src/mishe_tauftauf/memory_stat_reading.py:1-8`: the reader is read-only, returns UNKNOWN on a missing or unparsable key, and is not called from `host_capacity.decide`.

## What this plant already does

- `host_capacity_reading.py` already separates anon, file and current into named Signals, and reports UNKNOWN for a missing part rather than 0. That is the discipline the kernel text makes necessary, because `memory.high` is a throttle and not a ceiling.
- The replay (`artifacts/signals-replay-20261101/replay.py`) reads recorded rows from `recovery/host-samples.jsonl` and `operator/lifeline-memory.jsonl` and calls `host_capacity.decide` and `decide_anon` on each joined row. It is not a docstring reading. The replay precondition from the successor-field decision (DM 159948) holds for this field.
- `memory_stat_reading` is already a shadow reader; the census (`.mishe-tauftauf/artifacts/field-review-census-20261101/trace.md`) records it as UNWIRED.

## What is new here

The kernel text supports the mechanism only in part. It establishes that `memory.high` throttles and triggers reclaim pressure. It does not establish that the kernel reclaims cache "to hold the slice near high", nor that `current/high` "parks near 0.9". Those are the module's claims about this plant's observed behaviour, and the replay is their evidence, not the kernel documentation. The docstring presents a measured effect as a documented mechanism. That is the misreading risk this angle exists to catch.

The anon-versus-current result (33 starts reduced to 14 on the same rows) stands on the replay. Its own limits (15,378 of ~47,000 rows join; ~77% recompute fidelity; the residual 14 starts are PSI memory stalls) are already recorded in the census trace, and this review does not extend them.

## Application

target: src/mishe_tauftauf/host_capacity_reading.py

Proposal for health (owner of the host-capacity controller), docstring only, no behaviour change:

- Split the first paragraph into what the kernel documents and what the replay measured. Kernel: memory.high is a throttle that triggers reclaim pressure, and memory.current is the total the cgroup uses. Measured: on the 2026-11-01 replay rows, current/high sat near 0.9 and anon reading cut LIMITED/CRITICAL starts from 33 to 14. Name the replay artifact as the source of the second claim.
- Remove "reclaims cache to hold the slice near high" as a mechanism unless a test or the replay shows it. Replace with "observed on the replay rows".

Test before landing: none required for a docstring; the deterministic check is that `python3 -m py_compile` passes and that `tests/test_host_capacity_reading.py` still passes unchanged.

Closure, per field `host-capacity-anon-reading-and-memory-stat`: the decision reading of the replay is restated in the module with its evidence, and the readers are either wired into the controller with a decision reading against the replay rows, or retired with their tests. This review closes only the docstring-claim part of that closure.

Limit: this review does not verify the replay numbers (the census trace says they reproduce as counted) and does not judge whether the anon rule is a better controller rule.
