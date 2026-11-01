---
field: probe-process-group-bounds
angle: recent
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary sources, read this turn, not summaries:

- Linux man-pages, `PR_SET_PDEATHSIG(2const)` (the parent-death signal, current man-pages release): https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html
  Quoted: "The parent-death signal is sent upon subsequent termination of the parent thread." and "The "parent" in this case is considered to be the thread that created this process." and "The parent-death signal setting is cleared for the child of a fork(2)."
- Linux kernel admin guide, "Control Group v2", the `cgroup.kill` entry (read at the 6.19 docs build): https://docs.kernel.org/6.19/admin-guide/cgroup-v2.html
  Quoted: "Writing "1" to the file causes the cgroup and all descendant cgroups to be killed." and "This means that all processes located in the affected cgroup tree will be killed via SIGKILL." and "In a threaded cgroup, writing this file fails with EOPNOTSUPP as killing cgroups is a process directed operation, i.e. it affects the whole thread-group."

Recency: the man page and kernel entry are the current primary texts; `cgroup.kill` was added in the 5.14 line. Neither is a 2023-2026 publication, so the "recent" angle here means "current text of the primary mechanism", and that is stated as the limit of this reading.

The module's own text, read side by side: `src/mishe_tauftauf/probe_supervisor.py` says "bound a probe's process group even if its caller is killed". It arms `prctl(PR_SET_PDEATHSIG, SIGTERM)` in the supervisor, then checks `os.getppid() != args.parent` to close the race, then runs the probe with `start_new_session=True` and `os.killpg(..., SIGKILL)` in `finally`.

## What this plant already does

- `recovery_io.run_probe` spawns the supervisor by path (`recovery_io.py:101`) with `--parent os.getpid()`, and on its own timeout or exit sends `killpg` SIGTERM (`recovery_io.py:145`). The supervisor's `finally` kills the probe group with SIGKILL after natural exit as well, so a pipe-holding grandchild cannot outlive the probe.
- The bound is therefore two-layered: the caller's timeout in `recovery_io`, and the supervisor's own deadline (`--timeout`, capped at 300s).
- Tests exist for the recovery boundary: `tests/test_recovery_io.py`, `tests/test_host_capacity.py`, `tests/test_render_admission_failure_backoff.py`.
- Measured by grep this turn: no `Thread`, `ThreadPoolExecutor` or `concurrent.futures` call in `src/mishe_tauftauf/` reaches `run_probe`, so the thread-scoped caveat below is latent, not live.

## What is new here

Reading the two man-page sentences against the supervisor exposes two limits the module's docstring does not name:

1. **Parent death is a thread event, not a process event.** `PR_SET_PDEATHSIG` fires when the thread that forked the supervisor exits, even while the process lives. The module reads "its caller is killed" as the guarantee; the kernel gives "the forking thread exits". Today the callers are single-threaded, so the difference never fires. A future caller that runs `run_probe` on a short-lived worker thread would get its probe cancelled as if the caller died, with a 124 that reads as a probe timeout. That is a false verdict on the recovery path, which field-review criterion 3 names as the load-bearing kind.
2. **A SIGKILLed supervisor leaves the probe group running.** `PDEATHSIG` only delivers to the supervisor, and the supervisor's cleanup runs in a `finally` that a SIGKILL does not reach. The `cgroup.kill` entry is the kernel's own answer to "kill everything under this owner even when the owner is gone", but only for processes placed in a cgroup. `probe_supervisor` places nothing in a cgroup, so it has no kernel-level backstop for its own death by SIGKILL. The `cgroup.kill` text also says the operation "is protected against migrations" and "deal[s] with concurrent forks appropriately", which the group-kill in the supervisor does not claim.

Neither limit has been observed in this plant. This review does not claim a leaked probe exists. It names the gap so the detector can be built: a controlled test that SIGKILLs a supervisor with a sleeping grandchild and checks whether the grandchild survives.

## Application

Proposal to the owner (health): replace the thread-scoped parent watch with a process-scoped one, and add the controlled failure as a test before any live change.

target: src/mishe_tauftauf/probe_supervisor.py

- Change: give the supervisor an inherited pipe from `run_probe`, held open only by the caller. The supervisor treats EOF on that pipe as parent death. EOF follows the process's descriptor table, not the forking thread, so the false cancellation in limit 1 cannot fire. The `prctl` call and the `getppid` race check can then go.
- Test (must exist before activation): spawn `run_probe` from a worker thread that exits early and assert the probe is still running; SIGKILL the supervisor with a live grandchild and record whether the grandchild survives. The second assertion documents limit 2 and, if the grandchild survives, selects the cgroup or a pid-file reaper as the next experiment.
- Limits: this change does not address limit 2. A SIGKILLed supervisor still leaves the group. The thread test is latent in the current tree, so the change buys robustness, not a present fix.
- Disposition: health reviews, runs the two-sided test, and decides keep or revise. No edit was made to `probe_supervisor.py` by field-review.
