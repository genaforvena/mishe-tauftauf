---
field: unit-psi-reading-verdict
angle: recent
date: 2026-11-01
status: discarded
owner: senses
---

# unit-psi-reading-verdict — recent (2026-11-01)

## What we read

- Linux kernel documentation, "PSI - Pressure Stall Information", current accounting page: <https://docs.kernel.org/accounting/psi.html> (fetched this turn). It defines `some` and `full`, says per-cgroup PSI is exported as `cpu.pressure`, `memory.pressure` and `io.pressure` in the same format as `/proc/pressure`, and tracks `total` in microseconds alongside the avg10/avg60/avg300 ratios.
- Web search for recent per-cgroup PSI accounting (2025 query). It returned only 2021–2022 kernel patch threads, e.g. the per-cgroup accounting disable interface, <https://lkml.iu.edu/hypermail/linux/kernel/2209.1/02470.html>, and the `cgroup_disable=pressure` boot-option patch, <https://lkml.iu.edu/hypermail/linux/kernel/2105.2/02859.html>. The 2021 patch description attributes per-cgroup overhead to accounting at each level of the tree.

## What this plant already does

`src/mishe_tauftauf/unit_psi_reading.py` reads the newest sampler record, takes `psi.some.total` as the exact stall counter, and ranks units by `psi.full.avg60`. Its `stall_inversions` docstring (`:175-176`) states that "Kernel PSI charges a stall to the cgroup and every ancestor", and the check flags a child whose some-stall delta exceeds an ancestor's by more than 1 s.

## What is new here

Nothing recent. The current kernel page confirms the per-cgroup file format the reader parses, and it confirms that `total` is the exact counter the reader prefers over the averages. The page does not say that stall is charged to ancestors, and it does not say a child cannot exceed its parent. The 2021 patch implies per-level accounting but does not state the rule. So the premise behind `stall_inversions` is supported only indirectly, and the docstring states it as fact. The 2025-era search found no new result that changes the reader.

Observation gap: the cell's closure asks that verdicts be read against the sampler JSONL rows, not the docstring. I did not read those rows this turn, so this review does not check the live inversion rule against data.

## Discard

No recent (2023–2026) primary source changes this reader; the current kernel doc confirms its format and counter but not the ancestor-charging premise. Routed to senses as an observation: the `stall_inversions` docstring should cite its basis or state it as an assumption, and the rule should be checked against the sampler rows.
