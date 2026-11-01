---
field: native-wake-recovery
angle: misread
date: 2026-11-01
status: applied
owner: health
target: tests/test_native_resident.py
---

## What we read

**Crash-Only Software** — George Candea and Armando Fox, *Proceedings of the 9th Workshop on Hot Topics in Operating Systems (HotOS IX)*, Lihue, Hawaii, May 2003. Read the full HTML text at:

- <https://static.usenix.org/events/hotos03/tech/full_papers/candea/candea_html/index.html>

Passages this review relies on, quoted from that text:

1. The central equations: "stop = crash" and "start = recover". Each component gets one power-off and one power-on switch, both idempotent, and the power-off switch lives outside the component.
2. "Recovery code also runs on every startup, not only after exceptional events." The authors argue that this keeps the recovery path exercised, so it does not rot unseen until an incident.
3. Requests must be self-describing so that a fresh instance can continue. Retries are safe only for idempotent operations. For non-idempotent operations the paper prescribes rollback, compensating operations, or tolerating the inconsistency.
4. Scope limits the paper states: Byzantine and data errors are excluded; the focus is short, state-updating tasks; it targets a single installation.

Companion reading: the primary text only. The Candea 2005 dissertation was not read for this review.

## What this plant already does

The plant has the crash-only shape in one place and the opposite shape in another.

**Where it matches the paper's "one recovery path".** `src/mishe_tauftauf/native_recovery.py` classifies a failed wake from its own bytes (`classify_attempt`, :374, verdicts at :92–95). The resident's `serve()` (`src/mishe_tauftauf/native_resident.py:605`) releases a busy wedge only through that verdict. A wake is retried only as a fresh attempt directory, and no effect is replayed. This is the paper's "retry only idempotent work" rule, applied by refusing retry whenever an effect was dispatched.

**Where it does not match.** The recovery path is not on the startup path. The verdict runs in exactly two places:

- `run_wake` (`native_resident.py:451–460`) calls `_fresh_attempt` only when an attempt directory for this wake already exists.
- `serve()` (`native_resident.py:606–619`) calls `_recovery_verdict` only when a `*.busy` marker is present.

An ordinary start with no busy marker and no prior attempt never reads `classify_attempt`. The paper's requirement is precisely that the recovery code run on every start, so that it is exercised before an incident needs it.

**Frequency, measured.** The live `SITE/wakes` directory holds 5 attempt entries and 0 `-recovery-` entries. `SITE/native-canary/docs/wakes` holds 102 entries, of which 10 are `-recovery-` attempts, roughly 10%. These are counts, not a rate estimate; the sample is small. The point is that the recovery branch runs only when a wake has already failed.

**Tests.** The branch is not untested. `tests/test_native_resident.py` drives it by injection: `test_transient_failure_releases_the_busy_wedge` (:290), `test_effect_free_transient_attempt_allows_a_bounded_fresh_attempt` (:301), `test_effect_present_attempt_refuses_a_fresh_attempt` (:316), and `test_recovery_is_bounded` (:326). Those tests exercise the verdict in-process. They do not exercise it on a live resident's ordinary start.

**The paper's idempotence premise does not transfer.** Crash-only assumes most requests can be retried. A native wake dispatches non-idempotent effects (shell, writes, outbound messages), so the plant reconciles instead of retrying. That is the paper's own fallback for non-idempotent work (compensate or tolerate). The plant's reconcile-not-replay rule is therefore correct, not a misreading.

## What is new here

The misreading is not of the idempotence rule. It is of the frequency rule. This plant treats the recovery path as an exceptional branch, which is the "exceptional events" framing the paper rejects. Its consequence is specific and checkable: a defect in `classify_attempt` or `fresh_attempt` surfaces only on a real failed wake, and the first live exercise of a changed verdict would be an incident. The unit tests reduce this risk but do not remove it, because they run in a test process and not in a resident's own start.

The transfer is therefore narrow. Keep the reconcile-not-replay rule as written. Add a live exercise that drives the recovery verdict through a real resident start, so the path is observed under a controlled failure rather than first observed in an incident. This is the plant's own doctrine ("exercise a controlled failure through detection and routing") with the paper's frequency argument as the reason it must run on a schedule.

Prediction: a scheduled controlled transient failure, injected through `run_wake`, will produce a verdict and a bounded fresh attempt on the live resident, and the verdict will be visible in `story.log`. If it does not, the recovery path has a live-only defect that the in-process tests miss, and the drill is the detector.

## Application

target: tests/test_native_resident.py

Proposal to health (the owner of resident recovery). Add a controlled-failure drill, scheduled rather than run on demand, that:

1. starts a resident in a scratch role, with the transient failure injected through the existing `run_wake` test seam used at `tests/test_native_resident.py:290`;
2. asserts the verdict string, the bounded fresh-attempt directory name, and the busy-marker release, with the reading present in the story log;
3. reports the drill result as its own detector row, so a missing or failing drill reads UNKNOWN or RED rather than being absent.

This is a test and a detector, not a change to `native_resident.py` behaviour. The field-review role does not edit the organ. The owner decides whether the drill runs in CI, on the live resident, or both, and whether the scratch role is acceptable. Detection interval: once per scheduled check. Resolver: health. Limit: the drill exercises a transient failure only; it does not test a provider failure with real effects, which is the case where reconciliation matters most.
