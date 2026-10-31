---
field: recovery-observations-and-sessions
angle: metric
date: 2026-10-31
status: applied
owner: senses
target: src/mishe_tauftauf/session_restarts.py
---

# Session churn is counted as births, but the plant has no measure of how long a session was absent: add absent time per window as a quality-of-service reading

## What we read

- Brown and Patterson, "Towards Availability Benchmarks: A Case Study of Software RAID Systems", USENIX ATC 2000, pp. 263–276, introduction and section 2.1 (availability metrics), <https://static.usenix.org/event/usenix2000/general/full_papers/brown/brown_html/index.html>, fetched 2026-10-31 with curl as HTML. Quoted: availability "must not be defined at a single point in time or as a simple average over all time. It must instead be examined as a function of the system's quality of service over time." Their example: a system that "refuses requests for two seconds out of every minute" and one "down for one whole day every month" have "approximately the same average uptime", and a benchmark must tell them apart. They propose measuring "the variations in system quality of service metrics over time", with the metric chosen per system type.
- Not read: the full PDF and section 2.3 (how the paper graphs and summarises these measurements), and the companion technical report on maintainability. The 2000 paper is old; this metric angle does not claim a current consensus on it.

## What this plant already does

- `src/mishe_tauftauf/session_restarts.py` `assess_restarts` (lines 75–132) measures cadence only: it counts scope births (`Started <session>-session.scope`), the gaps between births, and the count of gaps under 30 minutes (`under_30m`). The verdict is `churn` on three or more births in the window, or two births with a gap under `TIGHT_GAP_MINUTES = 10.0`.
- The additive `intensity` field landed in commit `8af3cc60` (see the transfer review of 2026-10-31) names the count bound. It does not measure time.
- `discovery.py:4391–4449` (`sense.host.session-restarts`) reads only the `Started` lines from the journal (`--grep "^Started {session}-session\.scope"`). It never queries a stop line, so it cannot say how long the session was down between two births.
- A grep of `src/mishe_tauftauf` and `checks/` for `mttr`, `time.to.recover`, `time.to.repair`, `availability` and `quality.of.service` finds no outage-duration or QoS-over-time measure for sessions. The matches are unrelated uses of the word "availability" (seed availability, publication gate).

So the plant measures how often a session is born and reports it as churn. It does not measure the outage that a restart implies. A restart that is followed by a three-second return and a restart that is followed by a six-hour absence both count as one birth.

## What is new here

Brown and Patterson's point, transferred to this sensor: the count and the average are the wrong unit for a recovery boundary. What a resolver needs is the absent time over a window, and that is a function of the session's availability over time, not a single birth count.

Proposed metric, named so it can be checked:

- `absent_minutes` for the window: the sum over each restart of the gap from the previous session stop to the next birth. With no stop in the window, the time from the window start to the first birth is not counted, since the session was not observed to stop.
- `absent_fraction` = `absent_minutes / past_minutes`, reported next to `intensity` in the same mapping.

This is a second quality-of-service reading next to the count, not a new verdict. The verdict and state stay unchanged.

## Application

target: src/mishe_tauftauf/session_restarts.py

Proposed change for senses (owner), no change to verdict or state:

1. First, establish the stop message on the live journal before coding: a bounded query such as `journalctl --boot=<boot> --grep "Stopped <session>-session\.scope|Deactivated successfully" --since <window>`. Live query on 2026-10-31 timed out at 20 s with no output, so the message text is UNKNOWN here. Do not guess it in the regex.
2. Once the stop text is confirmed, extend the acquisition in `discovery.py` with a second grep for the stop line, and pass the stop stamps to `assess_restarts` as an optional argument (default `None`, so existing callers and tests are unchanged).
3. Add `absent_minutes` and `absent_fraction` to the `intensity` mapping only when stop stamps are supplied. When stop stamps are absent, leave the fields out. Do not report 0, since 0 would read as steady availability.

Prediction: during a window with one known restart (for example, a deliberate `systemctl restart` of a session in a test environment), `absent_minutes` should be within a few seconds of the measured outage.

Observation: `python3 -m pytest tests/test_session_restarts.py`, plus one live discovery sample showing the fields after a real restart.

Keep if the existing tests pass, the verdict and state are unchanged on the same input, and the absent time matches the observed outage. Revert the second grep and the new fields if the stop line is absent or the fields disagree with the journal timestamps.

## Limits

- This measures absence of the session scope, not user-visible quality. A session can be down while the plant's service is still answering through another path, and the reverse is also possible. The metric names the session's absence and says nothing about the plant's service quality until a QoS metric for the plant is defined.
- The 2000 paper's point is that the QoS metric must be chosen per system. Choosing absence of the session scope is a plant decision and should be recorded as one, not inherited from the RAID benchmark.
- A single outage counts once. The metric does not show whether restarts are frequent and brief, which the existing gap list already shows.
- Journal retention can truncate the window. A window that starts after the first stop would under-count absence, so the sample must report the truncation (as `coverage` already does for acquisition) rather than report a smaller number as complete.
