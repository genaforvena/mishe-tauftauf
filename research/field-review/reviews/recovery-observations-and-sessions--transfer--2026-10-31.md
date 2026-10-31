---
field: recovery-observations-and-sessions
angle: transfer
date: 2026-10-31
status: applied
owner: senses
target: src/mishe_tauftauf/session_restarts.py
---

# Restart intensity transferred from OTP supervisors to the session-churn reader: name the MaxR/MaxT bound in the verdict, and leave the act-on-exceed question to health

## What we read

- Erlang/OTP reference, `supervisor` module, section "Supervision Principles › Supervisor flags › Restart intensity and period", <https://www.erlang.org/doc/apps/stdlib/supervisor.html>, read 2026-10-31 via WebFetch. Quoted: a maximum restart intensity is defined with `intensity` and `period`; "if more than `MaxR` restarts occur within `MaxT` seconds, the supervisor terminates all child processes and then itself"; `intensity` defaults to 1, `period` to 5; the termination reason for the supervisor is `shutdown`.
- Attempted primary: Armstrong 2003 thesis "Making reliable distributed systems in the presence of software errors", the origin of the restart-intensity idea. Unreachable from this host: `mirror.its.dal.ca` failed DNS, and `sics.se` redirects to a non-thesis host (`ri.se`). The thesis is not read. The OTP reference is a current normative description of the same rule, not the original argument, and this review does not claim otherwise.

What the source does not say, recorded so the transfer is not overread: the page does not state that the error is escalated to the parent supervisor (it says only that the supervisor exits with `shutdown`), and it does not say whether the counter is per supervisor or per child.

## What this plant already does

- `src/mishe_tauftauf/session_restarts.py:43-61` already embodies the detection half of the rule. `COUNT_DRIFT_THRESHOLD = 3` births in the trailing window is churn, and a `TIGHT_GAP_MINUTES = 10.0` gap with at least two births is churn. That is a count-over-window test, the same shape as "more than MaxR within MaxT", with MaxR = 2.
- `assess_restarts` (`session_restarts.py:75-132`) returns `steady`, `churn` or `unknown`. Malformed input is `unknown`, never `steady`, which is the right direction for missing evidence.
- `src/mishe_tauftauf/discovery.py:4446-4450` maps the verdict to a sensor state: `steady` becomes `verified`, `churn` becomes `drift`, and everything else becomes `unknown`. The sample line already includes the window (`last-{past_minutes}m`).
- No restart budget that acts exists in this repository. A grep for `budget`, `max_restart`, `restart_limit`, `StartLimit` in the session modules finds only comments. Systemd unit `Restart=`/`StartLimitBurst` settings live outside this checkout, so whether the unit itself bounds restarts is UNKNOWN from here.

So the detector is present and the bound is implicit. The rule's act-on-exceed half, terminating the children and escalating, is absent from this code. A churn verdict goes to a `drift` state and a resolver.

## What is new here

The transfer is a naming and a question, not a new mechanism.

1. The OTP rule makes its two parameters explicit (`intensity`, `period`). In `assess_restarts` the threshold is a module constant, but the window is caller-supplied and absent from the returned dict. The verdict cannot say "2 births allowed in N minutes" on its own, and a resolver reading only the dict cannot tell which window produced `churn`. The sample string carries the window, so the information exists, but it is text, not a field.
2. The question the OTP rule answers is what happens on exceed. The plant's answer is: a sensor state of `drift`, routed to a resolver. Whether an automatic bounded stop is wanted is a health decision with real cost, since it would stop a session that a human may be watching. That decision should be made explicitly and recorded, not inherited silently from a library default.

## Application

target: src/mishe_tauftauf/session_restarts.py

Proposed change for senses, with no behaviour change to the verdict:

- Add an optional `window_minutes` argument to `assess_restarts` (default `None`). Pass it from the caller at `discovery.py:4446`, where `past_minutes` is already known.
- Add a structured `intensity` field to the returned dict: `{"max_r": 2, "window_minutes": <value or None>, "births": <n>, "exceeded": <bool>}`. `max_r` is `COUNT_DRIFT_THRESHOLD - 1` so the field reads as OTP reads it ("more than MaxR"). `exceeded` is true for the existing churn clause, not for the tight-gap clause alone, so the field does not invent a second rule.
- Keep `verdict` and `state` unchanged. The new field is additive, and existing tests (`tests/test_session_restarts.py`) must still pass.
- Do not add an automatic stop. Route the act-on-exceed question to health as a decision.

Prediction: a churn row carries `intensity.exceeded: true`, `max_r: 2`, and the window that produced it, and a resolver can tell from the row alone which bound fired. Observation: `python3 -m pytest tests/test_session_restarts.py`, plus one live discovery sample showing the field. Keep if the existing verdicts are unchanged and the field appears. Revert if any verdict or state changes.

Limits: this is a shape change in a sensor, not evidence that session churn is causal or that a bound would have prevented any past incident. The window length is still a caller choice, not a property of the session.
