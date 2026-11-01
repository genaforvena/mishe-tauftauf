---
field: probe-process-group-bounds
angle: misread
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary sources, read this turn, not summaries:

- Linux man-pages, `setsid(2)`: https://man7.org/linux/man-pages/man2/setsid.2.html
  Quoted: "setsid() creates a new session if the calling process is not a process group leader." and "The calling process also becomes the process group leader of a new process group in the session." and "The calling process will be the only process in the new process group and in the new session."
- Linux man-pages, `kill(2)`: https://man7.org/linux/man-pages/man2/kill.2.html
  Quoted: "If pid is less than -1, then sig is sent to every process in the process group whose ID is -pid."

The `setsid(2)` page does not state in words that a caller leaves its old group, so that step is an inference from its text. The experiment below supplies the observation for it.

The module's own text, read side by side: `src/mishe_tauftauf/probe_supervisor.py` says "bound a probe's process group even if its caller is killed". Its cleanup is `os.killpg(child.pid, signal.SIGKILL)` in a `finally`. The misread is that a group kill bounds everything the probe started. The kernel text says `killpg` reaches only processes whose group ID is the target. A descendant that calls `setsid()` is no longer in that group.

## What this plant already does

- `probe_supervisor.py:42` runs the probe with `start_new_session=True`, and the `finally` at the end sends `killpg` SIGKILL to the probe's group after natural exit or timeout.
- `recovery_io.py:101` spawns the supervisor with `--parent os.getpid()`, so the supervisor's own bound applies to the probe.
- Grep this turn: `src/mishe_tauftauf/` has many `start_new_session=True` spawns (engine_boundary, host_containment, inference_worker, model_boundary, runtime and others). None of them has a reaper beyond the group kill. The supervisor is the only one on the recovery probe path.

## What is new here

The experiment (run this turn, not committed, no source edit):

- Probe command: a `#!/bin/sh` script that runs `setsid sleep 997 &`, writes the background PID to a file, then sleeps 1 s.
- Command: `python3 -I src/mishe_tauftauf/probe_supervisor.py --parent $$ --timeout 5 -- <script> <pidfile>`, run directly without a `timeout` wrapper. A first attempt through `timeout` returned rc=124 at once, because `getppid()` no longer matched `--parent`; that run measured nothing and is discarded.
- Result: supervisor rc=0 (the script exited, and the `finally` ran `killpg` on the probe group). One second later the `setsid` grandchild was alive at its recorded PID. It was then killed by hand.

So the group kill bounds the probe's group, and the grandchild left that group before the kill. The docstring's "bound a probe's process group" holds only for descendants that stay in it. The supervisor has no reaper for descendants that leave.

This is a second, separate gap from the one in the `--recent` review (PDEATHSIG thread scope, and a SIGKILLed supervisor). Both gaps leave a probe alive, but here the supervisor is alive and cleans up in its `finally`, and the process escapes the group. Whether real probes do this is not measured. The commands `recovery_io` runs are not known to call `setsid`, `setpgid` or a daemonizing helper; that is an observation gap.

## Application

Proposal to the owner (health): before any change, measure whether the probe commands on the recovery path leave their group. Then decide on the reaper.

target: src/mishe_tauftauf/probe_supervisor.py

- Measurement first (no source change): record the children and grandchildren of real recovery probes after completion, by PGID and session, and report any that differ from the probe group.
- If any leave the group, the candidate fix is a reaper that the group kill cannot be replaced by: a cgroup (`cgroup.kill`, which the kernel documents as killing "all processes located in the affected cgroup tree") or a child subreaper (`prctl(PR_SET_CHILD_SUBREAPER)`) that collects orphans and kills them. Choose between them by test on this host.
- Test (must exist before activation): run the `setsid` script above through the supervisor and assert the grandchild is dead after the supervisor returns. This is the test that the current code fails.
- Limits: the experiment uses a bare `sh` probe. It shows the escape is possible, not that a live probe escapes. Nothing here is an observed leak. A cgroup needs delegated write access, which this host may not grant; that is unverified.
- Disposition: health reads this, runs the measurement, and keeps or revises the proposal. No edit was made to `probe_supervisor.py` by field-review.
