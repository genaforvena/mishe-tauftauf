---
field: pin-promotion-caller-and-socket
angle: mechanism
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/plant.py
---

## What we read

Primary source, read this turn:

- Linux kernel documentation, "Control Group v2", admin-guide, read at https://docs.kernel.org/admin-guide/cgroup-v2.html (fetched this turn; the first 100000 characters read, which cover the process-placement sections). Passages used:
  - Fork inheritance: "When a process forks a child process, the new process is born into the cgroup that the forking process belongs to at the time of the operation."
  - Membership reading: "/proc/$PID/cgroup lists a process's cgroup membership", in the format "0::$PATH" for cgroup v2.
  - Migration: a process moves only by writing its PID to the target's cgroup.procs, and the writer "must have write access to the cgroup.procs file of the common ancestor of the source and destination cgroups."

Plant text read beside it, this turn:

- `src/mishe_tauftauf/proc_table.py:454-466`: `current_cgroup_slice()` reads `/proc/self/cgroup` and returns the most specific `mishe-*.slice` of this process.
- `src/mishe_tauftauf/proc_table.py:438-451`: `expected_workload_slice(session)` returns the slice a session must run in (lifeline for `mishe-life-support`, plants for the self-development and android-body sessions), and `None` for every other session name.
- `src/mishe_tauftauf/tmux.py:223-236`: `_refuse_misplaced_caller` already applies the kernel's fork rule to tmux starts: a caller in a different Mishe slice is refused with a message naming the slice to use.
- `src/mishe_tauftauf/plant.py:580-585`: the `--runtime-source` branch gates only on `owns_session(home, session)`. It does not look at the caller's slice.

Live reading, this turn: `cat /proc/self/cgroup` from this review shell returned `0::/user.slice/.../mishe.slice/mishe-plants.slice/tmux-spawn-....scope`, and `current_cgroup_slice()` returned `mishe-plants.slice`, so the reader works on this host.

## What this plant already does

The plant already has the mechanism for containment by placement. `_refuse_misplaced_caller` takes the kernel's fork rule seriously: a tmux server started from a lifeline process would inherit lifeline headroom, so the start refuses and names the slice. The seed raise has the same policy. The reading function is shared kernel-adjacent code with tests.

The promotion path does not use it. The `--runtime-source` branch asks only whether the session is owned (the record question the misread and critique reviews already discuss), and it never asks where the promoting process was placed.

## What is new here

The refusal we observe is a placement fact, not an ownership fact. A promotion run from the lifeline slice is a process whose fork-inherited placement is the lifeline slice, and every child it starts (the pin script, the reload, the tmux probe) is born into that slice too. The kernel text makes the consequence explicit: placement is inherited at fork and is not chosen by the child, so a containment check has to read the process's own membership, not a name the caller passes in.

The mechanism this suggests is narrow:

1. At the `--runtime-source` branch, read `current_cgroup_slice()` before any pin change.
2. Refuse when the live slice is `mishe-lifeline.slice`, with a message that names the slice and the reason (a promotion must not be placed in the lifeline headroom it is meant to protect).
3. Ownership stays as it is. Placement is a second, separate boundary, so a correct owner in the wrong slice is refused and a wrong owner in the right slice is still refused by `owns_session`.

Limit, stated and not assumed away. `expected_workload_slice` returns `None` for the plant session name, so the existing predicate would not refuse here: `_refuse_misplaced_caller` is unrestricted for ordinary sessions. That is why the proposal does not reuse the existing helper unchanged, and it names a fixed refusal set instead. The kernel text also gives only a snapshot: `/proc/self/cgroup` can change after the read if a privileged actor migrates the process, and the reading does not cover cgroup v1 layouts. The check therefore narrows the boundary; it does not prove containment. This review did not reproduce the 12:54 refusal from inside the lifeline slice, so the causal link between placement and that refusal is inferred from the code and the log name, not observed.

## Application

target: src/mishe_tauftauf/plant.py

Proposal for health (owner of promotion and the lifeline caller), additive to the misread and transfer proposals:

- In `main()` at the `--runtime-source` branch (`plant.py:580-585`), before `select_source`, call `current_cgroup_slice()` and raise a `ValueError` naming the slice when it equals `mishe-lifeline.slice`. Do not change `owns_session` or `owned_socket`; this is a placement gate, not an ownership gate.
- Test in `tests/test_plant.py`: patch `current_cgroup_slice` to return `mishe-lifeline.slice` and assert the promotion branch refuses before any pin write; patch it to return `mishe-plants.slice` and assert the branch proceeds to the ownership check. The lifeline test fails on the current code, which has no placement check.
- Detector: the promotion attempt log `.mishe-tauftauf/artifacts/pin-promotion-*.log`, read per attempt, plus a single controlled probe run from a `systemd-run --slice mishe-lifeline.slice` shell that must produce the named refusal and no pin write.
- Closure for field `pin-promotion-caller-and-socket`: a promotion from the lifeline slice is refused with the slice named, a promotion from the plants slice reaches its loaded-bytes check, and both results appear in the promotion log. Detection interval: each promotion attempt. Routing: health, with genome informed at the push and exact-SHA CI step.
