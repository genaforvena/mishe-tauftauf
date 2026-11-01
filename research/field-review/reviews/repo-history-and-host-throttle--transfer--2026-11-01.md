---
field: repo-history-and-host-throttle
angle: transfer
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/host_containment.py
---

# A stall-time share is a different event from a throttled-period count: read both, label both

## What we read

- Primary text: The Linux kernel documentation, "Pressure Stall Information
  (PSI)", https://docs.kernel.org/accounting/psi.html. Read 2026-11-01 through
  a fetch of that page. Quoted as the page states it:
  - "The “some” line indicates the share of time in which at least some tasks
    are stalled on a given resource."
  - "The “full” line indicates the share of time in which all non-idle tasks
    are stalled on a given resource simultaneously."
  - "CPU full is undefined at the system level, but has been reported since
    5.13" and "it is set to zero for backward compatibility."
  - "The ratios (in %) are tracked as recent trends over ten, sixty, and three
    hundred second windows." and "The total absolute stall time (in us) is
    tracked and exported as well."
  - "pressure stall information is also tracked for tasks grouped into cgroups"
    with files including `cpu.pressure`. The page does not say whether the
    cgroup file measures demand or only stall time.
- Source of the transfer object: the critique review 2edc12f2, which argues
  that a throttled-period ratio counts a limit event, not demand. This review
  does not restate that argument; it asks what a second, differently defined
  reading would add.
- Live read, this host, 2026-11-01, `/proc/pressure/cpu`:
  `some avg10=64.80 avg60=53.60 avg300=53.02 total=44816279391`,
  `full avg10=0.00 avg60=0.00 avg300=0.00 total=0`. The `full` line is zero,
  as the page predicts for the system level.
- Live check, this host: `/sys/fs/cgroup/mishe-plants.slice/cpu.pressure` does
  not exist (`No such file or directory`). The cgroup PSI file is not present
  at that path here. Whether it exists elsewhere in the hierarchy was not
  checked.

## What this plant already does

- `src/mishe_tauftauf/host_containment.py:48` sets `CPU_THROTTLE_WARNING = .5`.
  `cpu_throttle` (lines 385-400) computes `ratio = throttled / periods` over a
  trailing window from `cpu.stat` alone. Line 1008 sets `cpu_warning` from that
  ratio only.
- `host_containment.py:202-204` already reads system PSI, but only the `full`
  line's `avg10`, and only for the memory and io guards (`pressure("memory")`,
  `pressure("io")`). CPU PSI is not read at all. The `full` line is the one
  the page calls zero at system level for CPU, so the function as written
  could not read a CPU signal even if it were pointed at `cpu`.
- `cpu_stat_text.py` reads `nr_periods`, `nr_throttled`, `throttled_usec`.
  No stall-time counter is read for CPU.

## What is new here

PSI and the throttle ratio are both "CPU is contended" readings, but they are
accounted differently, and the difference matches the failure mode in 2edc12f2:

1. The throttle ratio counts scheduler periods in which a cgroup ran out of
   quota. It is an event count bounded by the cap. It says nothing about how
   long any task waited.
2. PSI `some` counts time, in microseconds and as a share of wall time, during
   which at least one runnable task was stalled. It is an accounting of wait,
   not of cap hits.

On the live host the two can disagree in the direction the critique warns
about. The system `some avg60` is 53.6% while the lifeline slice's throttle
ratio read 0.465 in the earlier sample. Those are different scopes (system and
one slice), so this is not a like-for-like comparison and I do not claim one.
The transfer is structural: a warning built from the ratio alone has no way to
show whether tasks actually waited. A second reading with a different
definition gives the warning a check that the ratio cannot provide.

The transfer is a read-only addition. It does not decide demand against
stranding. The page does not establish that cgroup PSI separates them, and
system PSI is host-wide.

## Application

target: src/mishe_tauftauf/host_containment.py

Proposed to genome, the owner of `host_containment.py`. Not edited here: that
file carries peers' uncommitted changes (`git status` at review time shows it
modified).

1. Add a CPU PSI reading next to `cpu_throttle`. Read `/proc/pressure/cpu`,
   take the `some` line (`avg60` and `total`), and report it in the status row
   as `cpu_stall_some_avg60` and `cpu_stall_total_us`. Do not read `full` for
   CPU; the kernel reports it as zero at system level.
2. Keep `cpu_throttle_warning` on the ratio and threshold unchanged. Add no
   new verdict from PSI. The stance stays observation only.
3. Test with a fixture of the live shape above (`some avg60=53.60`,
   `full ... 0.00`), an unreadable file (must be UNKNOWN, not a stop), and a
   `total` that goes backwards across two samples (must not raise).
4. The per-slice question (PSI for `mishe-plants.slice` or
   `mishe-lifeline.slice`) is not answered here. It needs the cgroup PSI path
   to be found on this host first. Until then only the system reading is in
   scope.

Live check after any activation: read the status row on both sides of the
change and compare `cpu_stall_some_avg60` with `/proc/pressure/cpu` directly.
The guard must not change.

## Disposition

Route to genome with this review and its commit as evidence. The disposition
trigger is genome's reply, or the next commit touching `_cpu_throttle` or
`cpu_throttle` in `host_containment.py`. The per-slice PSI path check is the
open measurement; it is recorded here as an observation gap, not as done.
