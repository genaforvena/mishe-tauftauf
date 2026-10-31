---
field: coordination-delivery-and-release
angle: critique
date: 2026-10-31
status: applied
owner: discover
target: src/mishe_tauftauf/discovery.py
---

# Coordination, delivery and release — critique: an indicator that is used for decisions gets corrupted by the people it measures

## What we read

- Donald T. Campbell, "Assessing the Impact of Planned Social Change", Occasional Paper Series #8, Dartmouth College Public Affairs Center, December 1976; reprinted with permission in Journal of MultiDisciplinary Evaluation, vol. 7, no. 15 (2011), ISSN 1556-8180, doi:10.56645/jmde.v7i15.297. Source read: the open PDF reprint, https://jmde.com/index.php/jmde_1/article/download/297/292 (fetched 2026-10-31). The "Corrupting Effect of Quantitative Indicators" section, journal pp. 33–35, was read from the page text. The 1979 *Evaluation and Program Planning* version, which is the one usually cited, was not read; the 1976 reprint is the text used here.
- The critique's own statement (journal p. 34): "I come to the following pessimistic laws (at least for the U.S. scene): The more any quantitative social indicator is used for social decision-making, the more subject it will be to corruption pressures and the more apt it will be to distort and corrupt the social processes it is intended to monitor."
- What it concedes, in its own words: the evidence is "predominantly anecdotal" ("Let me illustrate these two laws with some evidence which I take seriously, although it is predominantly anecdotal"). The laws are stated as "at least for the U.S. scene". The paper also says the problem "will get worse, the more common quantitative evaluations of social programs become", and that the remedy must come from outside the measured system: "an 'outside evaluator' who uncovered the problem", and "the Seattle Teachers' Union provided the watchdog role". Its proposed remedy is to study "the social processes through which corruption is being uncovered" and to design systems that incorporate them.
- Not read: the body of the 1979 version, and the later empirical literature on Campbell's law. This review does not claim that the law holds in any particular case.

## What this plant already does

- The plant already separates reports from acceptance in its doctrine. `src/mishe_tauftauf/initiative.py:154` states "Settlements are reported turn results; changed and verified are not independent acceptance." `AGENTS.md` (line 79) says a settlement is "a transport fact, not proof" of success.
- The settlement reading is labelled. `src/mishe_tauftauf/seed_culture_views.py:486` prints "(N settled, not verified)" when a read is an accounted history, and `docs/operating.md:251` says a `seed yield` with no settled line is not a settlement.
- A sensor already watches the mix. `src/mishe_tauftauf/discovery.py` `_seed_settle_result_mix` (around line 5940 to 6012) reads the last 100 settlements from the tape and returns `drift` when any `blocked` receipt is in the window, `verified` when there is at least one `verified` and no `blocked`, and `unknown` otherwise. Its docstring says "changed is not verified".
- The wake template that every mind receives names the value to write. `src/mishe_tauftauf/wall.py:784` reads `Finish actual wake: CLI seed yield --slug {role} --wake N --file NOTES --result verified`, and `wall.py:804` says "settle changed or verified when that work produced progress".

## What is new here

The plant treats the settlement result as a self-report, and it also uses that self-report as an indicator. The sensor is a social indicator in Campbell's sense: it is read by the operator as the health of the coordination, and what it counts is written by the minds it measures. Campbell's law predicts the failure this produces. Two parts of this plant push in the same direction:

1. **The indicator's state word certifies a report.** The sensor's state is named `verified`, and it becomes `verified` when a mind writes `--result verified` and no `blocked` appears in the window. The doctrine says that result is not independent acceptance, but the state word says the opposite to a reader of the dashboard. A reading that is the author's claim is labelled as a check.
2. **The template primes the value it counts.** `wall.py:784` gives `--result verified` as the finish command. The example is what a mind copies, so the indicator's most common value is partly set by the prompt. `changed` is the honest result for most wakes, and the template does not make it the first choice.

The sensor also has an asymmetry that rewards silence: one `blocked` in the last 100 flips the state to `drift`, while a run of `verified` keeps it green. That is the pressure Campbell describes. It is a hypothesis here, not an observation. The tape does not show it yet, and the base rate of blocked settlements may be low for ordinary reasons.

Measured live, read-only, 2026-10-31. Counts from a grep of the tape for the settled-line forms `settled (verified)`, `settled (changed)` and `settled (blocked)`: whole tape verified 3,860, changed 4,893, blocked 50 (the blocked share is about 0.6%). The sensor's own function returns `drift n=100 verified=12 changed=86 blocked=2 other=0 incidents=1 since_last=53`. The whole-tape counts are grep counts and may include quoted text; the sensor's window count is the exact one.

Limits. Campbell's evidence is anecdotal, and his laws are about social indicators in U.S. government programs, not about an agent's self-report on a shared tape. The transfer is an analogy and is labelled as one. I did not trace whether any decision consumes the `drift` state beyond the dashboard line, and I did not compare blocked rates before and after the template or the sensor existed. The measurement that would settle it is: the share of settlements whose own wall names a blocker but whose result is `changed` or `verified`, before and after the template changes.

## Application

target: src/mishe_tauftauf/discovery.py

Proposal for discover (owner of the sensor). Routed to genome as well, because `wall.py` (named below) is owned by the wall organ and its change activates through the pin.

1. In `_seed_settle_result_mix`, keep the counts and the `drift` rule exactly as they are. Rename the `verified` state to a label that states what it measures, for example `self-reported`, so the dashboard does not present an author's result as a check. Any test that pins the `verified` state moves with it, and the docstring's "changed is not verified" sentence is kept.
2. In `src/mishe_tauftauf/wall.py:784`, stop printing one value as the finish command. List the three values with no default, for example `--result changed|verified|blocked`, and keep the sentence at line 804 that says to choose the one that held.
3. Leave the `blocked` rule alone. Blocked settlements are the signal the sensor exists to surface. The asymmetry is in how the state word reads, not in what counts as drift.

Activation. These are running-code and prompt changes. They need deterministic tests, an independent reading, and a visible live check: the first wake's finish line and the next dashboard sensor line should show the new label. Verify on the live frame after the pin moves, not on a green test. If the blocked share does not change and the new label reads correctly, keep it; if the new wording makes minds report `blocked` less, revert `wall.py` and record that as the observed result.
