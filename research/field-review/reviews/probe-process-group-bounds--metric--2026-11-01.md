---
field: probe-process-group-bounds
angle: metric
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary sources, read this turn:

- Linux kernel documentation, cgroup v2, `cgroup.events` (`populated`), `cgroup.stat` (`nr_descendants`, `nr_dying_descendants`) and the pids controller (`pids.current`): https://docs.kernel.org/admin-guide/cgroup-v2.html
  Quoted: "1 if the cgroup or its descendants contains any live processes; otherwise, 0." (`populated`) and "The number of processes currently in the cgroup and its descendants." (`pids.current`).
- Linux man-pages, `PR_SET_CHILD_SUBREAPER(2const)`: https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html
  Quoted: "When a process becomes orphaned (i.e., its immediate parent terminates)," and "then that process will be reparented to the nearest still living ancestor subreaper." The page does not say what happens when no living subreaper exists.

## What this plant already does

- `src/mishe_tauftauf/probe_supervisor.py` has no census. Its `finally` sends `os.killpg(child.pid, SIGKILL)` and nothing counts, names or reports what was alive before or after.
- The `--mechanism` and `--misread` reviews on this branch established that a `setsid` grandchild leaves the probe's group and survives the group kill.

## What is new here

A metric that counts escapees by process lineage, not group membership. Measured this turn, scratch only, no source edit, no commit (artifacts: `.mishe-tauftauf/artifacts/field-review-probe-metric-20261101/RESULT.md`).

- Probe: `sh` runs `setsid sleep 997 &` and then `sleep 30`. The supervisor ran it with `--timeout 3` under a parent shell.
- A 50 ms `/proc` sampler recorded every process whose PPid chain reached the shell during the run: the probe shell, `sleep 997` and `sleep 30`.
- The supervisor returned rc=124. Afterwards only `sleep 997` was alive, so the metric's "sampled but alive after return" set is exactly the escapee.

What this gives the plant: an escape count that is a number, not a log line. Two limits decide where it can be read.

1. Lineage is exact only while the direct child is alive. Once it exits, an orphan is reparented to the nearest living subreaper, per the man page. On natural exit, the census taken after the child is gone sees no PPid chain and reports zero escapees. That zero is false. The metric must be taken before the direct child exits, or use a different counter.
2. A sampler can miss short-lived descendants. A census taken synchronously in the supervisor, immediately before `killpg`, removes the sampling gap while the probe is still alive.

The cgroup counter is the exact one where delegation exists. `pids.current` counts every process in the cgroup and its descendants, whatever its group or session, so the escape count is `pids.current` read before the `cgroup.kill` write, and `populated` after it should be 0. That needs the child-cgroup mechanism from the `--mechanism` review. Where it is not available, the lineage census is the fallback metric, and it reports which method was used.

## Application

Proposal to the owner (health). Measurement only first: this changes what the supervisor reports, not what it kills.

target: src/mishe_tauftauf/probe_supervisor.py

1. Add a pre-kill census in `finally`, before `os.killpg`: walk `/proc` for every process whose PPid chain reaches `child.pid` and record its PID and PGID. Write `escaped_candidates` (the census members whose PGID differs from `child.pid`) to stderr or the returned status, labelled `method=lineage`.
2. After `killpg`, count which census members are still alive. Report `escaped_survivors`. A non-zero survivor count is the metric's visible failure, and it should reach the probe's caller, not be swallowed.
3. Test with the probe in this review (`setsid sleep 997 &` then a sleep). Expected: `escaped_survivors=1`. Then a probe that does not escape: expected 0. A natural-exit probe that leaves a `setsid` grandchild: the census must run before the child exits, so it should be written as a race test, not assumed.
4. Do not replace `killpg` in this change. The cgroup reaper stays a separate, conditional step from the mechanism review, decided by this metric's count on real recovery probes.

Limits: one host, one probe shape, sampling-free census only while the direct child lives. The measurement cannot see a grandchild that exits and is reaped before the census. Health owns the decision to keep, revise or revert.

Disposition: health reads this, takes step 1 as the metric definition, and keeps, revises or reverts it on the observed `escaped_survivors` count from real recovery probes. field-review made no edit to `probe_supervisor.py`.
