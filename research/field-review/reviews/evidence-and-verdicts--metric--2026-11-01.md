---
field: evidence-and-verdicts
angle: metric
date: 2026-11-01
status: discarded
owner: field-review
---

## What we read

Target primary source: Brown, L. D., Cai, T. T. and DasGupta, A., "Interval Estimation for a Binomial Proportion", *Statistical Science* 16(2), 2001, pp. 101–133 — <https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1015346320.full>. The chosen metric was the Wald interval's behaviour at zero observed events, which is the zero-count defect this plant's `latest_bad=0` is exposed to.

Not read. Every route to the primary text was blocked: the Project Euclid PDF (`https://projecteuclid.org/download/pdf_1/euclid.ss/1009213286`) and the University of Pennsylvania repository bitstream both returned a bot-protection HTML page, not the paper. The HTML landing page for the Penn handle was fetched and contains the title only.

Secondary material seen in search results and not treated as a reading: a summary of the rule of three as a large-n approximation to the Clopper–Pearson bound (`https://www.statology.org/a-concise-guide-to-the-statistical-rule-of-three/`), and a note that the rule runs low for n below about 50 (`https://qualitysafety.bmj.com/content/22/12/1042`). Nothing here is claimed about what Brown, Cai and DasGupta state.

## What this plant already does

A grep of `src/mishe_tauftauf/` and `checks/` for `wilson`, `clopper`, `rule of three`, `binomial`, `wald`, `agresti` and `zero.count` finds no binomial interval. A zero count is published as a count (`latest_bad=0` beside `all_bad=195` and `bound=3270` in `src/mishe_tauftauf/discovery.py`, `_ledger_evidence_binding`), and the earlier 2026-10-22 metric review already names the missing denominator. So the plant has no interval that a zero would otherwise be read against, and the gap that a Wald-versus-Wilson reading would address is real in principle.

## What is new here

Nothing was established. The candidate change was to publish an upper confidence bound beside any zero violation count. That change needs the primary paper's stated limits (the Wald interval's coverage at p̂ = 0, and which alternative is recommended for small n) before it is judged. Without the paper, the proposal would rest on a secondary summary, which this role does not accept as a reading.

## Discard

Primary source unreachable: the Brown–Cai–DasGupta text is behind a bot gate on both the publisher and repository routes, so the metric cannot be read and no upper-bound change is proposed on that basis. Retry on a changed input only, such as a reachable copy or a library-cited passage; the candidate is recorded here for that trigger.
