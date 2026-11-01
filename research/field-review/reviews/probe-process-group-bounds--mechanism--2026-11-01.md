---
field: probe-process-group-bounds
angle: mechanism
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary sources, read this turn:

- Linux kernel documentation, cgroup v2, `cgroup.kill`: https://docs.kernel.org/admin-guide/cgroup-v2.html
  Quoted: "Writing "1" to the file causes the cgroup and all descendant cgroups to be killed. This means that all processes located in the affected cgroup tree will be killed via SIGKILL." and "Killing a cgroup tree will deal with concurrent forks appropriately and is protected against migrations." and "In a threaded cgroup, writing this file fails with EOPNOTSUPP as killing cgroups is a process directed operation".
- Linux man-pages, `prctl(2)` index, and `PR_SET_CHILD_SUBREAPER(2const)`: https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html
  Read only as far as the page title and name index. The subreaper semantics were not read in text this turn, so the subreaper alternative below is a candidate, not a verified mechanism.

Not re-read this turn, carried from the sibling reviews on this branch (`--misread`): `setsid(2)` and `kill(2)`, which say a `setsid()` caller becomes the leader of a new group and that `kill` with `pid < -1` signals the group `-pid`.

## What this plant already does

- `src/mishe_tauftauf/probe_supervisor.py:42` starts the probe with `start_new_session=True`. Its `finally` sends `os.killpg(child.pid, SIGKILL)`. Nothing else reaps the probe's descendants.
- The same file's `prctl(1, SIGTERM)` (PDEATHSIG) bounds the supervisor to its parent thread, which the `--recent` review already covers.
- The `--misread` review found that a `setsid` grandchild leaves the probe's group and survives the `killpg`.

## What is new here

A mechanism that does not depend on group membership: confine the probe to a child cgroup and kill the cgroup. Measured this turn on this host, not committed, no source edit.

- Host: kernel 6.8.0-147-generic, cgroup v2 (`cgroup2fs`). The shell runs in a delegated user scope, `.../tmux-spawn-<id>.scope`, which contains a writable `cgroup.kill` (mode `--w-------`). The scope is writable by the user, so a child cgroup can be created under it.
- Probe: a `#!/bin/sh` script that runs `setsid sleep 997 &`, records its PID and sleeps 30 s. The supervisor ran it with `--timeout 3`.
- Run: `sh` wrote its own PID into `<child>/cgroup.procs`, then ran `probe_supervisor.py --parent $$`. This keeps the shell alive as the supervisor's parent, which the supervisor's own `getppid` check requires. An earlier attempt that used `exec` returned rc=124 at once and measured nothing; the parent PID had changed.
- Observed:
  - Grandchild PID 4020869 had PGID = SID = its own PID, so it had left the probe's group.
  - The supervisor returned rc=124 at the 3 s deadline.
  - Two seconds later the grandchild was still alive. This reproduces the `--misread` finding: the group kill does not reach it.
  - `echo 1 > <child>/cgroup.kill` killed it. A kill from the cgroup reaches the escaped grandchild.
  - The child cgroup was empty afterward and was removed.

What this shows: on this host a cgroup kill reaches a `setsid` escapee that the group kill misses. It does not show that a real recovery probe escapes, and it does not show that any production path can create a child cgroup. Both remain observation gaps.

Alternative, not tested: `prctl(PR_SET_CHILD_SUBREAPER)` in the supervisor. Orphans would reparent to the supervisor, which would then have to enumerate its descendants (for example by walking `/proc/*/stat` for PPid) and kill them. It works without delegation but needs the supervisor to find orphans itself, and it does not cover processes that were reparented before it was set. Its semantics were not read in text here.

## Application

Proposal to the owner (health). Measurement first, then the smallest change that the measurement supports.

target: src/mishe_tauftauf/probe_supervisor.py

1. Measure, no source change: on a real recovery probe (the commands `recovery_io.run_probe` runs), record PGID, SID and cgroup for every descendant after completion. A probe that leaves its group is the trigger for step 2. A probe that does not leave its group means the escape is latent, as the `--misread` review already says.
2. If a probe escapes, the candidate is a cgroup reaper: when the supervisor's cgroup is delegated, it creates `<scope>/probe-<pid>`, moves the probe into it at spawn, and writes `1` to its `cgroup.kill` in the `finally`. It falls back to the current `killpg` when `cgroup.kill` is missing or not writable, and it must report which path ran.
3. Test before activation: the `setsid` script above, run through the supervisor, must leave no live grandchild. The test should also check the fallback path on a host without delegation (or with the file made unwritable).
4. Limits: the experiment used a bare `sh` probe on one host, and it created and removed its own child cgroup. The cgroup reaper only works where the mind's cgroup is delegated, which is true here and unverified elsewhere. Killing the enclosing scope would kill the mind, so the reaper must never target the scope itself.

Disposition: health reads this, takes step 1, and keeps, revises or reverts the cgroup proposal. field-review made no edit to `probe_supervisor.py`.
