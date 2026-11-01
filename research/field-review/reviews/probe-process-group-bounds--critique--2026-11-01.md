---
field: probe-process-group-bounds
angle: critique
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary source, read this turn:

- Linux man-pages, `PR_SET_PDEATHSIG(2const)`: https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html
  Quoted: "The parent-death signal is sent upon subsequent termination of the parent thread." and "The "parent" in this case is considered to be the thread that created this process." and "The parent-death signal setting is cleared for the child of a fork(2)." and "It is also (since Linux 2.4.36 / 2.6.23) cleared when executing a set-user-ID or set-group-ID binary" and "The parent-death signal setting is also cleared upon changes to any of the following thread credentials".

Not read: Linux kernel Bugzilla #43300 (https://bugzilla.kernel.org/show_bug.cgi?id=43300). A search snippet describes it as a report of a child killed when a worker thread exits while the process lives. I cite it only as that snippet; its contents are not verified here.

The critique, in the man page's own words: the parent-death guarantee is attached to a thread, not a process. The module's docstring says the supervisor bounds "a probe's process group even if its caller is killed". That holds for a caller process that dies. It does not hold in the same way for a caller thread that exits while the process lives. Our practice would reproduce the misread if we adopted `PR_SET_PDEATHSIG` as the whole parent-death story.

## What this plant already does

- `probe_supervisor.py:23` arms `prctl(1, SIGTERM)`, and installs a SIGTERM handler first. The handler only sets a `cancelled` flag, so the supervisor's loop exits and its `finally` runs `os.killpg` on the probe group. A thread-scoped SIGTERM therefore reaches the same cleanup path as a process death.
- `probe_supervisor.py:26` re-checks `os.getppid()` against `--parent` after arming the signal, to close the race the code comment names.
- `recovery_io.py:101` spawns the supervisor with `--parent os.getpid()`. Whether `run_probe` is ever called from a non-main thread is not checked here. That is the observation gap that decides whether the thread scope is live.
- The same review pair already on the branch (`--recent`, `--misread`) proposes an inherited pipe whose EOF means parent death. That design follows the descriptor table, not thread lifetime.

## What is new here

The critique's concession, read carefully: the man page does not claim the signal is process-scoped. It defines "parent" as the creating thread. So the docstring's wording is a stronger claim than the primitive gives. The gap is narrow. The thread-exit case still lands in the same `finally`, so the group kill is not lost. What is lost is the precision of the docstring.

Also new: the setting is cleared after `fork(2)` and after a set-user-ID or capability-carrying `exec`. The supervisor is not setuid, so that clearing does not apply to it. The probe is a child of the supervisor, and it does not inherit the setting. Neither point changes the design; both should be stated, not assumed.

## Application

Proposal to health (owner). Documentation and one test, no behavior change yet.

target: src/mishe_tauftauf/probe_supervisor.py

- Correct the docstring so it says what the primitive gives: the supervisor is killed by the death of the thread that spawned it, and the group kill covers only the probe's group. Drop "even if its caller is killed" unless the pipe design lands.
- Measure, before any change, whether `run_probe` can be reached from a non-main thread on the recovery path. If it can, the thread-scope gap is live and the pipe design from the `--recent` review is the candidate fix. If it cannot, record that as the observed bound.
- Test that must exist before activation: spawn a caller thread that calls `run_probe`, let the thread exit while the process lives, and assert the probe group is dead after the supervisor returns.
- Limits: this review did not run anything. The thread-exit behaviour is read from the man page; the bugzilla report was not read. Nothing here is an observed leak.
- Disposition: health reads this, measures the thread reachability, and keeps, revises or drops the docstring change. field-review made no edit to `probe_supervisor.py`.
