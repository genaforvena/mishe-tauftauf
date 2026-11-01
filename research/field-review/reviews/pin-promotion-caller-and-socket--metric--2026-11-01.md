---
field: pin-promotion-caller-and-socket
angle: metric
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/plant.py
---

## What we read

Primary source, read this turn:

- Betsy Beyer, Chris Jones, Jennifer Petoff, Niall Murphy (eds.), "The Site Reliability Workbook", chapter "Canarying Releases", Google SRE. Read at https://sre.google/workbook/canarying-releases/ (fetched this turn; the passages below were extracted from the chapter text, so quotes are the fetched extraction, not a full-text dump, and section headings are as the chapter prints them).

Passages used:

- "Metrics Should Be Representative and Attributable": "The source of changes in the observed metrics should be clearly attributable to the change we are canarying." and "If a metric can change wildly with no impact to service, it's unlikely to make a good canary metric."
- "Metrics Should Indicate Problems": "We need to correctly define a notion of acceptable behavior for each of the metrics." and the trade-off: "If the idea of acceptable behavior is overly strict, we will get lots of false positives" against "we will be more likely to let a bad canary deployment go undetected."
- "Before/After Evaluation Is Risky": the chapter says time "is one of the biggest sources of change in observed metrics", which is why it compares a canary against a concurrent baseline instead of against the same thing earlier.
- "Choosing a Canary Population and Duration": the population "should be sizeable and last long enough to be representative", and "we do not need a terribly large canary population in order to detect key critical conditions" when requests are homogeneous.

Plant text read beside it, this turn:

- `src/mishe_tauftauf/plant.py:592-595`: the `--runtime-source` branch calls `owns_session(home, session)` and raises `selecting runtime source requires an existing owned session` on a false answer. The refusal carries no caller slice and no socket it read.
- `.mishe-tauftauf/artifacts/pin-promotion-57cff89-20261101T125443Z.log` (site artifact, read here): `PROMOTE-BEGIN new`, then the traceback ending in that `ValueError`, then `PROMOTE-END rc=1` and `PROMOTION-FAILED phase=promote-new`. A single attempt shows the outcome, but not which caller or which socket produced it.
- The same artifact directory holds at least three further 20261101 attempts on `57cff89` (T125443Z, T132849Z, T134030Z). Their verdicts were not read in this turn, so no rate is claimed from them.

## What this plant already does

The plant already records attempts. Each promotion run writes a per-attempt log with `PROMOTE-BEGIN`, `PROMOTE-END rc=` and a `PROMOTION-FAILED phase=` line, so the verdict of each attempt is already observable. The critique and misread reviews for this field fix the gate's logic: the promotion caller should present its socket, and the gate should check that socket's owner record. Neither review says how the gate's outcome is counted against the caller that produced it.

## What is new here

The chapter's point, applied to this gate: a promotion attempt is a small canary, and a metric on it is only useful if the change in the metric can be attributed to the thing being changed. Here the thing changed is the caller (lifeline slice versus plant slice, ambient socket versus explicit socket). Wall time is not the thing changed, so a before/after rate across the day mixes the caller effect with every other drift in the plant.

So the metric should split on the caller, not on time:

- Attempt record: `(caller_slice, socket_source in {ambient, explicit}, owner_read, verdict)`, where `verdict` is either `reached-loaded-bytes` or `refused:<reason>`.
- Headline metric: for each caller class, the count of attempts that reach the loaded-bytes check, over the same window. The field's closure is the cell `non-lifeline caller, explicit site socket` reaching `reached-loaded-bytes`, and the cell `non-lifeline caller, foreign TMUX_TMPDIR` returning the same answer as a caller without one.
- Acceptable behaviour is stated before the attempt, as the chapter asks: a refusal is acceptable only when `owner_read` differs from the home of the socket it was given. A refusal where the owner matches is a false positive of the gate, and it is counted as one.

The chapter's trade-off shows up here in a concrete way. A gate that is too strict refuses real owners (false positives, the 12:54 failure is a candidate, though its cause is not yet established). A gate that is too loose lets a wrong server through unrefused. The attempt record has both as countable cells, so neither failure hides inside a single pass rate.

Limit, stated and not assumed away: the chapter says a small canary must rely on the clearest indicator of a problem. Here attempts are few (a handful per day, all from one operator-driven path), so the metric can show closure, a single cell going from zero to one reached-verdict, but it cannot detect a change in a rate. The chapter's "homogeneous traffic needs less volume" does not rescue this; attempts are not homogeneous, they differ by caller and socket. So the metric is a closure counter, not a rate, and it should be read that way.

Hypothesis, with mechanism: the caller-attributed record would show whether the 12:54 refusal is a caller effect (ambient `TMUX_TMPDIR` or lifeline slice) or a real absence of an owned session. Not yet reproduced; inferred from the log and the code.

## Application

target: src/mishe_tauftauf/plant.py

Proposal for health (owner of promotion and the runtime-source branch). This is the metric's emission point, not a change to the gate's logic (which the critique and misread reviews cover):

- At the `--runtime-source` branch (`plant.py:593-594`), the refusal line names the caller slice (from `current_cgroup_slice()`), the socket that was consulted and its owner read, and whether the socket came from the explicit argument or the ambient environment. Emit it as a single key=value line, for example `PROMOTION-GATE caller_slice=... socket_source=... owner_read=... verdict=refused`, so the attempt log can be parsed without a traceback.
- On a pass, emit `verdict=reached-loaded-bytes` with the same fields, before the loaded-bytes check runs.
- Do not change `owns_session`, `session_owner`, or `owned_socket`; they are shared kernel with pinned tests.

Test before landing: a unit test in `tests/test_plant.py` that drives the gate with a foreign `TMUX_TMPDIR` and asserts the emitted line names `socket_source=ambient` and the owner it read. A second test with a matching owner asserts `verdict=reached-loaded-bytes`. The first fails on the current gate, which emits only a traceback.

Closure, per field `pin-promotion-caller-and-socket`: after the change lands and is loaded, a promotion from a non-lifeline caller with the explicit site socket produces at least one `verdict=reached-loaded-bytes` line in its attempt log, and the same caller with a foreign `TMUX_TMPDIR` produces a line with `socket_source=ambient` and the same verdict.

Detector: the per-attempt log `.mishe-tauftauf/artifacts/pin-promotion-*.log`, read after each attempt. Detection interval: each promotion attempt, and the next scheduled pin check. Routing: health, with genome informed at the push step. Field-review reads the lines after each landed attempt and reports the cell counts on its wall; it does not compute a rate.
