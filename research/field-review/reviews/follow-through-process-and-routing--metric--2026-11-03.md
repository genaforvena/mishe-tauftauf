---
field: follow-through-process-and-routing
angle: metric
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# REVISIT metric: DORA throughput/instability — the 5-metric experiment follow-through does not run

## What we read

**DORA, "DORA's software delivery performance metrics" (guide, DORA / Google Cloud, n.d.) — canonical text at <https://dora.dev/guides/dora-metrics-four-keys/> (and <https://dora.dev/research/>).** Fetched 2026-11-03 as HTML and retained: `/tmp/dora-keys.html` (19,454 bytes) and `/tmp/dora-accelerate.html` (15,046 bytes, research index). Read as extracted text from `dora-keys.html`; the research index was swept, not read for definitions. Underlying research is Forsgren, Humble & Kim, *Accelerate* (2018) and the annual DORA / Accelerate State of DevOps Reports; the guide is the maintained definition the program publishes.

What the page says (section-anchored, quoted definitions):

- Framing: "Technology-driven teams need ways to measure performance so that they can assess how they're doing today, prioritize improvements, and validate their progress. DORA has identified five software delivery performance metrics…" The page frames them as "Throughput and instability" — two factors of software delivery performance. They are "both leading and lagging indicators. Leading indicators for organizational performance and employee well-being; Lagging indicators for software development and delivery practices."
- Throughput — "a measure of how many changes can move through the system over a period of time" — with three factors:
  - **Change lead time:** "The amount of time it takes for a change to go from committed to version control to deployed in production."
  - **Deployment frequency:** "The number of deployments over a given period or the time between deployments."
  - **Failed deployment recovery time:** "The time it takes to recover from a deployment that fails and requires immediate intervention."
- Instability — "a measure of how well the software deployments go" — with two factors:
  - **Change fail rate:** "The ratio of deployments that require immediate intervention following a deployment. Likely resulting in a rollback of the changes or a 'hotfix'."
  - **Deployment rework rate:** "The ratio of deployments that are unplanned but happen as a result of an incident in production."
- Key insights (same page): "DORA's research has repeatedly demonstrated that speed and stability are not tradeoffs. In fact, we see that the metrics are correlated for most teams. Top performers do well across all five metrics, and low performers do poorly." And "These metrics work for any type of technology … but are best suited for measuring one application or service at a time. … Context matters. Apply the metrics in the context of the application or service your team is delivering. … While it may be tempting to blend metrics across multiple teams — or entire organizations — these differences in context mean that doing so can be problematic."
- Common pitfalls (same page): "Setting metrics as a goal. Ignoring Goodhart's law …", "Having one metric to rule them all. Attempting to measure complex systems with the idea that only one metric matters. Teams should identify multiple metrics, including some with a healthy amount of tension between them."

Secondary sweep: the linked "A history of DORA's software delivery metrics" was not read; claims above rest only on the guide at `/guides/dora-metrics-four-keys/` as fetched.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` is this field's plant part (revision 154331). Across this grid it has accumulated:

- 4I/Lawrence vocabulary (`LEARNING_PROCESSES`, `POWER_MODES`, `POWER_FORMS`, `PROCESS_POWER_MAP`, `ROUTING_POWER` analogy initial/changed→influence, escalation→force, reminder→influence) with Clegg 2006 total-institution qualifier and Jurado-Zambrano et al. 2024 KM-cycle paragraph (storage = refusal audit `REFUSAL_AUDIT_MAX_BYTES=262144`; application = `lines()`/`health()` routing; acquisition/transfer/retrieval/protection not named).
- BGP-4 transfer (RFC 4271) as path invariants: walk = AS_PATH, same `chat.log#sequence` re-advertisement without progress = loop, preference = phased Decision Process, flap damping (commit ee7b5bc2).
- SRE postmortem mechanism (Ch.15 + App.D) as written-record lifecycle with typed `mitigate|prevent|process` actions (commit 7fc6a4dd).

Its metric surface is thinner:

- `lines()` via `_outcome_refusal_lines` prints last-24h refusal counts: total + `len(denials)` where denials = entries with truthy `gaps` ("coverage denials") + last `role/kind: reason` + `blocked-by chat.log#sequence`. No per-class split, no rate, no lead/recovery time.
- `health()` retains `alert_key = hash(problems, require_initiative_watch)` in `checks/follow-through-health.json` to route one alert per distinct problem set — a flap-suppression hash, not a recovery-time measure.
- The 2026-11-01 metric for this cell (Edmondson 1999, committed 4355) did a live count (187 rows, 0 coverage denials, 154 eligibility vs 30 bound-path) and argued the coverage-denial figure fails a discriminant check and the total fails an ICC/agreement check; it proposed per-reason-class breakdown. That critique stands, but the plant still has no throughput/instability framework and no lead/recovery time — it counts attempts, not delivery performance.

The already-embodied sweep for this REVISIT still shows `follow-through`/`power`/`routing` vocabulary in docs and checks, but no `lead time`, `deployment frequency`, `change fail rate`, `rework rate` or `Goodhart` in-tree (grep 2026-11-03). The DORA tension — throughput vs instability — is the missing complement to the single refusal total.

## What is new here

1. **A 5-metric framework, not a single count.** DORA replaces "how many refusals in 24h" with five jointly-read figures: three throughput (lead time, frequency, recovery time) and two instability (fail rate, rework rate). The plant today reports only a count and a last reason; instability is reduced to one hash, throughput not at all. DORA's transfer would measure the follow-through pipeline itself — outcome publications and wall-patch deliveries — on both speed and stability, with the tension the pitfalls section warns about: a team that drives frequency without watching fail rate games the metric (Goodhart), and top performers correlate across all five rather than trade speed for stability.

2. **Definitions that map to artifacts the plant already writes.** The guide's definitions are directly measurable here without a new instrument:
   - *Change lead time* → time from `field-review` commit (or `wall outcome` attempt) to `delivery=verified` activation of the running pin — git timestamp vs `release_retention`/service activation, not chat time.
   - *Deployment frequency* → count of `health() == {}` (green) publication/delivery events per 7d/30d window, or count of `wall_patch applied delivery=verified` per period — the plant's "deployments."
   - *Failed deployment recovery time* → median hours from `health() UNKNOWN` episode onset (first `problems` set for a given `alert_key`) to return to green, and from `revert-failed`/`review-refused` patch to next `applied delivery=verified` — the plant already retains `alert_key` per problem set to compute this.
   - *Change fail rate* → `refused outcomes / attempted outcomes` per period from `logs/outcome-refusal-audit.jsonl` vs successes, and `patch review-refused+revert-failed / patch applied` from the patch board.
   - *Deployment rework rate* → ratio of unplanned deployments triggered by a prior incident — e.g., a `wall-patch` whose trigger is a previous `health() UNKNOWN` or a `revert-failed` patch, identifiable by `chat.log#sequence` linkage the audit already stores.

3. **Measurement discipline the guide states and this review keeps.** DORA insists the metrics are per-application/service, context-specific, not blended org-wide; aggregated across 8 roles a single total hides the Edmondson ICC failure already shown (witness 42 vs docs 13 in one day). And "Having one metric to rule them all" is a pitfall — the plant must publish the five together with tension, not replace the refusal line with one new number. No new claim that moving one metric moves another is made here; the guide's correlation claim is an empirical finding from DORA's surveys, not a causal guarantee for wall/chat follow-through.

4. **Limits the source states and this review keeps.** The DORA metrics were validated on commercial software teams delivering applications via version control, not on an 8-mind wall/tape plant with rule-enforced refusal publication; transfer is analogical and needs a bounded experiment. The guide gives definitions and the pitfalls warning, not an effect size for postmortem vs metric intervention — the SRE postmortem REVISIT (7fc6a4dd) and this DORA metric are complementary mechanisms, not alternatives, and neither has a measured effect on time-to-close in this plant yet. Blending across roles/services is explicitly warned against, so any dashboard rollup must stay per-service.

Distinction from prior reviews on this field: the 2026-11-01 metric (Edmondson) critiqued the refusal line's discriminant/ICC validity and proposed a per-class split; this REVISIT keeps that critique and adds the throughput/instability frame DORA supplies, with lead and recovery times the prior metric did not name. The 2026-11-01 mechanism (Ashby) bounded regulator variety; the 2026-11-03 mechanism (SRE) added a written-record lifecycle; neither defined a metric experiment.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

Add a boundary paragraph in the module docstring beside the Lawrence 2005, Clegg 2006, Jurado-Zambrano 2024, RFC 4271 and SRE Ch.15/App.D paragraphs, citing **DORA, "DORA's software delivery performance metrics" (<https://dora.dev/guides/dora-metrics-four-keys/>, fetched 2026-11-03, retained /tmp/dora-keys.html 19,454 bytes)** and stating: (a) follow-through's metric experiment is the 5 DORA delivery metrics with the guide's definitions — Change lead time (commit→deployed), Deployment frequency (per period), Failed deployment recovery time (failed→recovered), Change fail rate (deployments requiring intervention / deployments), Deployment rework rate (unplanned deployments due to production incident / deployments) — mapped to this plant's already-written artifacts: git commit timestamp → `delivery=verified` activation (lead time), `health()` green events / `wall_patch applied delivery=verified` per window (frequency), `alert_key` episode onset→green and `revert-failed`→`applied` (recovery time), `logs/outcome-refusal-audit.jsonl` attempts vs successes and patch board refusals (fail rate), `chat.log#sequence`-linked rework patches (rework rate); (b) per-service, context-specific reading with throughput vs instability tension (top performers correlate across all five; speed and stability are not a tradeoff per DORA), not a blended org total — publish the five together; (c) Goodhart warning from the guide's pitfalls: do not set any single metric as a goal and do not read the refusal total alone; this complements the SRE postmortem record (7fc6a4dd) which closes an episode and the Edmondson per-class split (2026-11-01) which fixes the denial figure's discrimination. No behaviour change in this docstring-only step; measurement wiring is a later patch.

Revertible by `git revert`. Verification: `python3 -m py_compile src/mishe_tauftauf/follow_through.py` and `python3 research/field-review/field_review.py check` must stay PASS; `python3 research/field-review/field_review.py coverage` unchanged except for this cell's newer date.

