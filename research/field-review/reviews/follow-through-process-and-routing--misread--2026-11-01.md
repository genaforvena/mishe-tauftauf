---
field: follow-through-process-and-routing
angle: misread
date: 2026-11-01
status: discarded
owner: genome
---

# The routing-stage power mapping is still unsourced; the primary Lawrence et al. (2005) text was not reachable, so this cell adds no new change

## What we read

**Lawrence, Mauws, Dyck & Kleysen (2005), "The politics of organizational learning: Integrating power into the 4I framework", Academy of Management Review 30(1), 180–191, DOI 10.5465/AMR.2005.15281451.** Publisher page: <https://doi.org/10.5465/AMR.2005.15281451> (redirects to journals.aom.org). The publisher returned HTTP 403 to the fetch, so the full text was not read. What was read is the abstract as shown on the Athabasca University repository record, <https://pure.athabascau.ca/en/publications/the-politics-of-organizational-learning-integrating-power-into-th/>, which carries the abstract's pairing of the four learning processes with four power forms. Secondary search snippets (student-facing copies, a flashcard site) were seen and not relied on. Episodic/systemic power and any routing stages were not confirmed in any reachable text.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` carries `PROCESS_POWER_MAP` (interpreting→influence, integrating→force, institutionalizing→domination, intuiting→discipline), `POWER_MODES` (episodic, systemic) and `ROUTING_POWER` (initial and changed→influence, escalation→force, reminder→domination). Its docstring attributes the routing mapping to Lawrence p.182–183. The recent review of this cell (`follow-through-process-and-routing--recent--2026-11-01.md`, applied to genome, commit f0aca9b0) already proposed: label `ROUTING_POWER` as a plant analogy, drop the Lawrence page citations from it, re-type `reminder`, type the refusal path as domination, and add a caller-level test. Commit 47b495da (genome) records the reminder-as-discipline and analogy wording.

## What is new here

1. The abstract-level reading agrees with the recent review on the core point: the source pairs four learning processes with four power forms, and says nothing in the abstract about routing stages, feedforward/feedback as routing, or reminders. The `ROUTING_POWER` citation therefore remains unsupported as far as any reachable text shows. This is a confirmation, not a new finding.
2. The four process→power pairings in `PROCESS_POWER_MAP` match the abstract. That part of the module is not misread.
3. The `POWER_MODES` episodic/systemic split could not be checked against any reachable text. The module's comment is the only statement of it in the plant, so it is unverified rather than wrong.
4. No new change beyond the recent review's four proposals is supported by what was read. Re-proposing them would duplicate a routed request.

Limits: the abstract does not describe the power forms in operational detail, so no reading here can say whether the plant's readings of discipline or influence are faithful. The full text would be needed for that, and it was not reachable.

## Discard

The cell adds no change beyond the already-routed recent review: the primary full text was 403 and the abstract confirms only the process→power pairings, so the routing citation stays unverified and genome holds the proposal.
