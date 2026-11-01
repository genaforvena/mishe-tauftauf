---
field: engine-policy-route-decision
angle: metric
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/engine_watch.py
---

# A decayed switch figure of merit, read from the proposal records, measures route churn

## What we read

- IETF, RFC 2439, "BGP Route Flap Damping", Villamizar et al., Standards Track, November 1998, https://www.rfc-editor.org/rfc/rfc2439.txt (fetched 2026-11-01, full text read).
- Points used, all from the RFC text:
  - Section 4: a figure of merit is kept per route. It is incremented on each state transition, and decayed exponentially between transitions. Quote: "The figure of merit is increased each time a route transitions from reachable to unreachable. The figure of merit is decayed at a rate proportional to its current value."
  - Section 4.2: configuration parameters are a cutoff (suppress above), a reuse threshold (use again below), a maximum hold-down time, and a decay half-life. The RFC recommends that unreachable state decay more slowly, or not at all: "Decaying at a slower rate is recommended."
  - The sawtooth behaviour: at a constant flap rate the figure of merit converges, and a ceiling bounds how long a route can stay suppressed. The RFC's worked example uses cutoff 1.5 and reuse 0.75 with a half-life, and says routes "would be suppressed after being declared unreachable 2-3 times".
- Limits of this reading: the RFC gives a mechanism and a parameter form, not a recommended value for any given system. Its numbers are for BGP reachability, not for quota windows or model selection. It also describes suppression, which this plant should not copy: we want the measurement, not the damping.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` `decide()` has a hysteresis hold. `min_hold_ms` (default 600 000 ms, lines ~304-326) sets `next_reconsider_ms = now + min_hold_ms` on a stay or hold (lines ~448, ~493, ~557). It is a hard time gate, not a measure of how often the route has moved.
- `src/mishe_tauftauf/engine_watch.py` `_maybe_propose()` (lines ~197-218) writes one file per switch: `engine/proposals/<generation>-<role>.json`. It writes only when the decision is `select` and the selected route differs from `_load_current()`. Each file carries `observed_ms`, `observed_at`, the decision and the prior current route. Nothing in `engine_watch.py` deletes these files (no `unlink` or `remove` outside the atomic write temp file).
- So the plant already writes a switch record, one timestamped row per switch. No code reads the records back as a rate. `check()` in the same file reports errors, not switch counts.
- The live site has no such history: `.mishe-tauftauf/engine/proposals/` is empty (0 entries at 2026-11-01 11:4x UTC), and there is no `engine/policy.json`. The existing transfer review (201c8c84) has already recorded that the live tree has one role.

## What is new here

The RFC's figure of merit is a metric the plant can compute from records it already writes, with no new persistence. Per role, on each switch, add a penalty of 1 and decay the running total by a half-life H:

    F(t) = sum over proposals i with observed_ms_i <= t of 2^(-(t - observed_ms_i) / H)

Here H is a plant parameter. A reasonable starting point is a multiple of `min_hold_ms`, for example H = 3 × 600 000 ms = 30 min. That value is a choice to measure, not a number from the RFC.

What this metric would answer that the current code cannot: whether one role is switching repeatedly inside a few holds. The hold stops a second switch for `min_hold_ms`. It does not show whether switches are still happening at a steady, faster-than-intended rate across holds, because each hold resets the gate and nothing counts the switches. A decayed count makes that visible as one number per role.

The proposed change is measurement only, in `engine_watch.check()` or as a read-only helper beside `_maybe_propose()`:

1. Read `engine/proposals/*.json` for the role, using `observed_ms` only.
2. Compute F(now) with H = 3 × min_hold_ms.
3. Report F as a dashboard or `check()` line with its value and the number of proposals read. Do not block or alter a switch. No threshold is set in this review.

The threshold, if any, comes after a measured distribution exists. The RFC's cutoff and reuse values are a shape reference only.

## Live check

- Live switch count: UNKNOWN. `engine/proposals/` holds zero files on the live site, so the metric would read 0 with 0 proposals. That is an absent-evidence reading, not a measured zero. The metric must print the proposal count next to F so a zero from no data cannot pass as a calm role.
- Test to add with the change: a fixture with three proposals at 0, 60 000 and 120 000 ms, `min_hold_ms` 600 000. F at 120 000 ms must match the closed form (about 2.93 for H = 1 800 000 ms: 1 + 2^-(60/1800) + 2^-(120/1800)), and a single proposal must read 1 at its own time, decaying by half at one H later.

## Application

target: src/mishe_tauftauf/engine_watch.py

Add a read-only per-role switch figure of merit, F(now), computed from `engine/proposals/*.json` using `observed_ms` only, with H = 3 × `min_hold_ms`. Report F together with the proposal count it was computed from. Do not block, alter or re-route any switch. A zero with zero proposals is reported as no data (UNKNOWN), not as a calm role. Owner (genome) lands it with the closed-form test above, then checks the live reading against the proposal directory.

## Limits

- The proposal records are written only on a switch to a different route. A route change that the watcher never proposes (for example one made by an operator by hand) is not counted.
- The metric counts switches. It does not judge whether a switch was useful. The quality question is open and is not answered by this review.
- Written against the checkout at HEAD 5d0b9291. The running pin 91d5385 may differ; the owner should check the loaded bytes before any activation.
