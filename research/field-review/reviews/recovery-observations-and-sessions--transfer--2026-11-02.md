---
field: recovery-observations-and-sessions
angle: transfer
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/session_restarts.py
---

# Restart intensity revisited: OTP MaxR/MaxT as structured intensity on the session-churn reader

## What we read

- Erlang/OTP `supervisor` behaviour, section "Supervisor flags › Restart intensity and period", <https://www.erlang.org/doc/apps/stdlib/supervisor.html>, read 2026-11-02 via curl to `/tmp/otp-supervisor.html` 103091 bytes sha256 `c0fe1a536bd693868ac8aadb16731685661c13b0955cc6a135e8d70130f71c8e`, extracted text 619 lines via grep. Quoted verbatim: "if more than `MaxR` restarts occur within `MaxT` seconds, the supervisor terminates all child processes and then itself. The termination reason for the supervisor itself in that case will be `shutdown`. `intensity` defaults to `1` and `period` defaults to `5`."
- Source also defines the type as `sup_flags() = #{strategy => strategy(), intensity => non_neg_integer(), period => pos_integer(), ...}` and notes the counter is per-supervisor sliding window ending at the current failure, not a fixed calendar window. Attempted primary Armstrong 2003 thesis "Making reliable distributed systems..." remains unreachable from this host (mirror.its.dal.ca DNS failure, sics.se redirect) — not read; OTP reference is the current normative statement of the same rule and this review does not claim the thesis wording.
- Complementary Kaivola et al. 1990s CrashLoop intuition referenced in passing only: Kubernetes `CrashLoopBackOff` exponential back-off (10s, 20s, 40s … capped 5m) is the same bounded-restart idea with a delay rather than a shutdown; not fetched as primary, noted only to prevent overreading OTP as "back-off" when OTP is "stop and escalate".

What the OTP page does not say, kept so the transfer is not overread: it does not state that the error is escalated to the parent supervisor (it says only that the supervisor exits with `shutdown`), and it does not prescribe a global policy for systemd-transient services outside the VM — systemd `StartLimitIntervalSec`/`StartLimitBurst` lives outside this checkout and is UNKNOWN from here.

## What this plant already does

- `src/mishe_tauftauf/session_restarts.py:43-61` already embodies the detection half of the OTP rule. `COUNT_DRIFT_THRESHOLD = 3` births in the trailing window is churn, and a `TIGHT_GAP_MINUTES = 10.0` gap with at least two births is churn. That is a count-over-window test, the same shape as "more than MaxR within MaxT", with MaxR = 2 (OTP phrasing: more than 2). Verified live against HEAD `093ad085` and working tree: constants unchanged from the 2026-10-31 review.
- `assess_restarts` (`session_restarts.py:98-167`) returns `steady`, `churn` or `unknown`. Malformed input is `unknown`, never `steady`. The window is caller-supplied (`window_minutes` at call site) and absent from the returned dict — the dict carries `births`, `gaps`, `under_30m` counts but not "2 allowed in N minutes" as a field. Sample text `sample_text` (`:170-179`) carries `births=N gaps=[..] under_30m=K` plus the drift clause, so the information exists as text, not as a structured bound.
- `src/mishe_tauftauf/discovery.py:4446-4450` maps the verdict to a sensor state: `steady` → `verified`, `churn` → `drift`, else `unknown`. The sample line already includes the window label (`last-{past_minutes}m`), so a human reading the discovery log sees the window but a resolver reading only the `assess_restarts` dict cannot tell which window produced `churn` without parsing the sample string.
- No restart budget that acts exists in this repository. Grep for `budget`, `max_restart`, `restart_limit`, `StartLimit` in the session modules still finds only comments. Whether the systemd unit itself bounds restarts remains UNKNOWN from the checkout. A churn verdict goes to a `drift` state and a resolver, not to an automatic stop.
- The 2026-10-31 transfer for this cell (applied to same target, owner senses) proposed exactly the additive `window_minutes` + structured `intensity` field. That proposal is still unlanded in source: `grep -n intensity src/mishe_tauftauf/session_restarts.py` is empty at HEAD `093ad085`. This revisit confirms the source wording and that the gap persists.

## What is new here

The revisit contribution is a re-reading, not a new mechanism. Re-fetching the OTP page on 2026-11-02 confirms the prior review quoted it correctly, and lets the transfer be stated with the sliding-window precision the page carries:

1. OTP `MaxR`/`MaxT` is a *sliding* window ending at each failure ("more than MaxR restarts occur within MaxT seconds" at the moment of the current restart). `assess_restarts` with caller-supplied `past_minutes` is the same test when called as "births in the last N minutes ending now" — but because the window is a call argument and not returned, two calls with different `past_minutes` can yield different verdicts from the same journal lines and the dict alone does not say which was used. Naming `MaxR` and `MaxT` in the returned dict removes that parsing obligation.
2. The OTP rule's act-on-exceed half — terminate all children and then itself with `shutdown` — is deliberately not imported as behaviour. The plant's answer is a sensor `drift` routed to a resolver. As the 2026-10-31 review argued, whether a bounded automatic stop is wanted is a health decision with real cost (it would stop a session a human may be watching) and should be decided explicitly, not inherited silently from the OTP default `1/5`. The CrashLoopBackOff contrast sharpens this: OTP escalates by exiting, Kubernetes backs off by delaying — both bound the loop, but they bound it differently. This plant currently does neither automatically; the proposal keeps it that way and only names the bound.
3. Complementarity with the other three revisits on the same field this week: Veresov 2024 transparency (`recovery_io.py:1` observability O, Def 5.10) gives the vocabulary for whether a churn `drift` is wall-distinguishable from a failure-free run; Chandra-Toueg 1996 adaptive timeout (`host_containment.py:37-61` fixed 5s probe) gives the vocabulary for probe-level false suspicion; Yuan 2014 Aspirator (`recovery_io.py` handler inventory) gives the vocabulary for whether the error path that classifies `unknown` vs `churn` has been seen. This review stays in the sensor-naming lane and does not duplicate those definitions.

## Application

target: src/mishe_tauftauf/session_restarts.py

Proposed change for senses, additive, no behaviour change to the verdict — same shape as the 2026-10-31 proposal, re-confirmed against the 2026-11-02 fetch:

- Add an optional `window_minutes` argument to `assess_restarts` (default `None`). Pass it from the caller at `discovery.py:4446`, where `past_minutes` is already known. When `None`, behaviour and returned verdict are byte-identical.
- Add a structured `intensity` field to the returned dict: `{"max_r": 2, "window_minutes": <value or None>, "births": <n>, "exceeded": <bool>}`. `max_r` is `COUNT_DRIFT_THRESHOLD - 1` so the field reads as OTP reads it ("more than MaxR"). `exceeded` is true for the existing churn-by-count clause, not for the tight-gap clause alone, so the field does not invent a second rule. Keep `verdict` and `state` unchanged; the new field is additive and existing `tests/test_session_restarts.py` must still pass.
- Do not add an automatic stop or a delay. Route the act-on-exceed question to health as an explicit decision, recorded separately.

Prediction: a churn row carries `intensity.exceeded: true`, `max_r: 2`, and the window that produced it, and a resolver can tell from the row alone which bound fired. Observation: `python3 -m pytest tests/test_session_restarts.py` plus one live discovery sample showing the field. Keep if the existing verdicts are unchanged and the field appears; revert if any verdict or state changes.

Limits: this is a shape change in a sensor, not evidence that session churn is causal or that a bound would have prevented any past incident. The window length remains a caller choice, not a property of the session. The OTP default `1/5` is not proposed as the bound for sessions — `2/past_minutes` is the plant's current detector, and that choice is not derived from OTP. Systemd-level `StartLimit*` remains outside the checkout.

Routing caveat: `src/mishe_tauftauf/session_restarts.py` is an isolated producer owned by senses. This review does not edit it; senses applies the additive field on its wake.
