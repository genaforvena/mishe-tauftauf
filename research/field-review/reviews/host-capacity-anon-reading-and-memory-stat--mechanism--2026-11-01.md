---
field: host-capacity-anon-reading-and-memory-stat
angle: mechanism
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity.py
---

## What we read

Primary source, fetched this turn:

- Linux kernel documentation, "Control Group v2", Memory Interface Files, https://docs.kernel.org/admin-guide/cgroup-v2.html (fetched this turn; the first 100,000 of 141,089 characters, which include memory.high and memory.reclaim). Quoted:
  - memory.reclaim: "A write-only nested-keyed file which exists for all cgroups." It is "a simple interface to trigger memory reclaim in the target cgroup." Example: `echo "1G" > memory.reclaim`.
  - "If less bytes are reclaimed than the specified amount, -EAGAIN is returned."
  - "Specifying a swappiness value instructs the kernel to perform the reclaim with that swappiness value." Range "[0-200, max]"; "setting swappiness=max exclusively reclaims anonymous memory."
  - "the proactive reclaim (triggered by this interface) is not meant to indicate memory pressure on the memory cgroup."
  - memory.high: "Memory usage throttle limit." "If a cgroup's usage goes over the high boundary, the processes of the cgroup are throttled and put under heavy reclaim pressure." "Going over the high limit never invokes the OOM killer and under extreme conditions the limit may be breached."

The page does not say whether a memory.reclaim write changes memory.high, memory.max, or the PSI counters during the write. It also does not give a rate or a latency bound for the reclaim. Those are not read from the source and are not claimed here.

Plant text read beside it:

- `src/mishe_tauftauf/host_capacity.py` `observe()` reads memory.current, memory.high and memory.max, and memory.events. `decide()` is the only consumer. No code in `src/mishe_tauftauf/` writes to memory.reclaim (`grep memory.reclaim src/mishe_tauftauf/*.py` returns nothing).
- The controller's only actuators are the render-slot count (`slots`, `limited`, `cooldown`) and the optional-unit stop/start list. It never reclaims memory itself.

## What this plant already does

- The controller observes host memory and stops or starts optional units to relieve it. It does not ask the kernel to reclaim anything. Relief comes from shedding work, and the usage state (`current >= high * .9`) is read but never acted on by reclaim.
- The earlier critique review (e94cd57d) and transfer review (70c5462d) already covered PSI as a stall signal and the Senpai-style calibrated pressure target. This review does not repeat them.

## What is new here

The kernel exposes a reclaim trigger that is documented as not a pressure signal. That gives the plant a control knob it does not use, and it separates two things the controller currently merges:

1. **Headroom probe.** A small, bounded `memory.reclaim` write on the optional-work slice, followed by a read of `memory.current` and `memory.events` and of the PSI `some`/`full` lines, measures how much of the slice's usage is reclaimable without a stall. If the reclaim returns -EAGAIN or stalls PSI, the slice has less headroom than the usage ratio suggests. The probe answers the question the critique review left open ("usage near high without a stall"): it turns a usage plateau into a measured reclaim result rather than an overload by definition.
2. **Swappiness choice.** The swappiness argument with value `max` reclaims only anonymous memory, which is the component the anon-reading cell is about. A probe could measure anon-only reclaimability separately from file cache, using the same split `host_capacity_reading.py` already makes.

Prediction, to be tested and not assumed: on an idle optional slice, a 256M anon-only reclaim (swappiness=max) returns without -EAGAIN and without `full` PSI rising, and `memory.current` drops by roughly the requested amount. If the prediction fails (short reclaim, or PSI `full` rises during the write), the slice's headroom is below the usage ratio and the controller's current usage threshold is the wrong guard for that slice.

Limits. The kernel text says proactive reclaim is not a pressure signal, so the probe result cannot replace the PSI input; it only adds a measurement beside it. A write to a live slice is itself an action on the host. It must not run on the workload slice, and it must not run while the host state is CRITICAL. The kernel page does not say the write is free of side effects on memory.high throttling, so that must be observed, not assumed.

## Application

target: src/mishe_tauftauf/host_capacity.py

Proposal for health (owner of the host-capacity controller). Measure first; no reclaim write is added by field-review:

- Observe-only step: in `observe()`, record `memory.events` and PSI `some`/`full` before and after any future probe. No write. This is a read of existing files and needs no new permission.
- Probe step, only after the observe-only step has produced replay evidence: a single anon-only reclaim (`memory.reclaim` with swappiness=max, a small fixed amount) on the optional-work slice, run only in state NORMAL, once per cooldown window, with the result (bytes reclaimed, EAGAIN yes/no, PSI delta) written to the host-capacity row. The controller's decision does not read the probe result until a separate decision.
- Revert: the probe is removed by deleting the single write call; no state migration is needed because the row field is additive.

Test before any change: `tests/test_host_capacity.py` must pass unchanged for the observe-only step. The probe step needs a test that a CRITICAL or UNKNOWN observation never triggers a write, and a test that an EAGAIN result is recorded as a measurement, not as success.

Closure for `host-capacity-anon-reading-and-memory-stat`: this review does not close it. It adds a measurable reclaim headroom question to the decision reading that the closure requires. Health decides whether the probe is worth building; the plant's replay rows (`artifacts/signals-replay-20261101`) are the first place to see whether the usage ratio over-predicts overload on the optional slice.
