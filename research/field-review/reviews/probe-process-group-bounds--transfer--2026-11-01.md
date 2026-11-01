---
field: probe-process-group-bounds
angle: transfer
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/probe_supervisor.py
---

## What we read

Primary sources, read this turn, not summaries:

- Erlang/OTP Reference Manual, "Processes" (links, exit signals, `process_flag(trap_exit, true)`): https://www.erlang.org/doc/system/ref_man_processes.html
  Quoted: "the receiver is not trapping exits, and the exit reason is something other than the atom `normal`" (the linked process terminates) and "The exit signal is converted to a message signal and added to the end of the message queue of the receiver." and "Note that if the `link` flag is set, an exit reason of `kill` will _not_ be converted to `killed`."
- Linux man-pages, `pipe(7)` (the lifeline the --recent review proposes): https://man7.org/linux/man-pages/man7/pipe.7.html
  Quoted: "If all file descriptors referring to the write end of a pipe have been closed, then an attempt to read(2) from the pipe will see end-of-file (read(2) will return 0)."

Recency: both are the current published texts of the mechanisms. Erlang is a mature runtime, not a recent result; it is read here for its relation model, and that choice is the reading's limit.

## What this plant already does

- `probe_supervisor.py` bounds a probe by one relation: the probe is started with `start_new_session=True`, and cleanup is `os.killpg(child.pid, signal.SIGKILL)` in a `finally` (`src/mishe_tauftauf/probe_supervisor.py:51`). Group membership is the only edge the supervisor uses to find the probe's descendants.
- The parent-death edge is `prctl(PR_SET_PDEATHSIG)`, which is an edge to a thread, not a process (see the --recent and --critique reviews on this branch).
- The measured gaps are already on the branch: a `setsid` grandchild leaves the group (--misread, reproduced by health), and PDEATHSIG fires on thread exit (--critique, reproduced by health).

## What is new here

Erlang separates two things this plant currently merges. A link is an explicit edge between two named processes, held by the runtime, and it carries a death notice whatever the process group, session or thread. Whether the receiver dies or gets a message is a property of the receiver (`trap_exit`), not of the sender. Transferred to the probe:

1. **Death as a message, not a kill.** The supervisor could trap parent death: the EOF on a lifeline pipe (the --recent proposal) becomes a cleanup event the supervisor handles, rather than a signal that terminates it in the middle of a `finally`. This is the Erlang move that makes the supervisor the owner of the cleanup decision. It does not add a new kill path.
2. **Edges by relation, not by group.** A `killpg` walks a group label that a descendant can leave with `setsid`. The Erlang lesson is that the cleanup should walk the relation the supervisor holds (the probe, and any descendant it registered), not a label a child can change. On Linux the closest OS relation a child cannot sever by `setsid` is cgroup membership: `cgroup.kill` (already cited in --recent) kills by membership, not group id. The honest limit is that POSIX has no runtime link table; a grandchild has an edge to the supervisor only if it inherits the lifeline descriptor. A grandchild that closes its inherited descriptors has no edge in any POSIX mechanism except cgroup membership.

What this changes in the claim: the --misread finding is not "the group kill is wrong". It is that the supervisor's relation to the probe's descendants is implicit (group label) and severable. The transfer makes the relation explicit, then asks which relation a child cannot sever.

Limits, stated as measurements to take before any change:

- Erlang's link is enforced by the runtime; this is an analogy. The plant's claim is about a process tree, not a VM.
- No cgroup is available to the probe in this plant's current configuration. Whether cgroup membership is writable from the recovery path is unmeasured here and is the first thing health should check.
- The application is health-owned. This review does not edit `probe_supervisor.py`.

## Application

target: src/mishe_tauftauf/probe_supervisor.py

Proposal to health, measurement first (no source change in this review):

1. Record the probe's descendant set at spawn: the probe pid, its pgid, and each descendant pid observed through `/proc/<pid>/task/*/children` while the probe runs. Report, per probe, whether any descendant's pgid differs from the probe's (the --misread condition) and whether any descendant's cgroup, if cgroup v2 is writable, matches the probe's.
2. Kill by the recorded descendant set and, if available, `cgroup.kill`, in addition to the group kill. Keep the group kill until the measurement shows the set covers every observed descendant.
3. Treat the lifeline EOF as a cleanup event inside the supervisor's `finally` (the trap move), with a controlled test: the parent closes the lifeline while a `setsid` grandchild holds the output pipe; the probe group, and the grandchild, must be gone within the supervisor's deadline, and the test must fail when the grandchild is not killed.

Route: health, addressed. Evidence: this review, and the two measurements above. The detector is the existing probe test; the detection interval is per recovery probe.
