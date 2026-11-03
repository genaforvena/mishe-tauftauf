---
field: engine-policy-route-decision
angle: critique
date: 2026-11-03
status: applied
owner: genome
---

# engine-policy-route-decision × critique — distinguish uncertainty from a recovery trigger

## What we read

- Huang, Magnusson, Bangalore Muralikrishna, Estyak, Isaacs, Aghayev, Zhu, and Charapko, “Metastable Failures in the Wild,” OSDI 2022, primary source: https://www.usenix.org/system/files/osdi22-huang-lexiang.pdf
- Sections 2.1–2.2 say the incident study relies on public postmortems of uneven detail and uses the authors’ judgment; some cases are explicitly only plausible. Section 3.3 defines a sustaining effect as feedback that keeps a system overloaded after its trigger is removed. Section 6.3 warns that fixes made without understanding the sustaining process can worsen vulnerability. This distinction is the critique: a mitigation that reacts to an uncertain signal can itself cause harm, but a transient transition alone is not evidence of metastable failure.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:401-425` treats a current route as ineligible for every health class other than `healthy` or eligible `reserve`; this includes `hold` for UNKNOWN, STALE, UNATTRIBUTED, and NOT_CONFIGURED evidence.
- At lines 451-494, an ineligible current route can switch immediately to a healthy fallback. `min_hold_ms` sets `next_reconsider_ms`; it does not prevent this switch.
- The existing transfer review’s deterministic probe records two switches in two seconds when account freshness alternates, despite a 600-second reported hold (`engine-policy-route-decision--transfer--2026-11-01.md`, lines 40-47). This proves a reachable code path, not live churn. Live proposal records and the rate of stale reads were unobserved there.

## What is new here

The code’s `hold` means the evidence cannot establish account health, not that the account is depleted. Yet the current-route branch converts that uncertainty into the same fallback action used for confirmed ineligibility. The study’s useful critique is not that this code has caused a metastable failure: its definition requires an overload-sustaining feedback loop, which has not been observed here. It is that changing routes on an unconfirmed reading is a recovery action without evidence that the trigger persists, and the immediate repeatable switch path can create extra route churn rather than restore a known-good state.

The study itself limits the analogy: its cases concern overloaded distributed services, while this code selects a model/account route and has no demonstrated retry-amplification loop. Do not claim incident likelihood or import its load-shedding remedies from the paper.

## Application

target: src/mishe_tauftauf/engine_policy.py

For an allowlisted current route classified `hold`, return `stay` on that route instead of selecting a fallback; reserve and depleted fresh readings remain distinct from unknown/stale evidence. Add deterministic tests for one stale read followed by recovery and alternating stale reads, asserting that `hold` alone does not switch. The transfer review already proposes this change to Genome; this reading supplies the failure-analysis rationale, not evidence that live incidents occurred.
