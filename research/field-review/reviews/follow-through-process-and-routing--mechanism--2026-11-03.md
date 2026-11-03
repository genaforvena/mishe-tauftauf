---
field: follow-through-process-and-routing
angle: mechanism
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# REVISIT mechanism: SRE blameless postmortem with typed action items as the operational mechanism follow-through lacks

## What we read

**Beyer, Jones & Petoff (eds.), *Site Reliability Engineering: How Google Runs Production Systems*, O'Reilly, 2016 — Chapter 15 "Postmortem Culture: Learning from Failure" (Lunney & Lueder) and Appendix D "Example Postmortem" (Shakespeare Sonnet++ incident #465).** Canonical open text at <https://sre.google/sre-book/postmortem-culture/> (Chapter 15) and <https://sre.google/sre-book/example-postmortem/> (Appendix D). Fetched 2026-11-03 as HTML and retained: `/tmp/postmortem-culture-20261103.html` (55,113 bytes) and `/tmp/example-postmortem-20261103.html` (49,719 bytes). Read as extracted text.

What the pages say (section-anchored, not paraphrase of a summary):

- Definition (Ch.15 "The postmortem concept"): "A postmortem is a written record of an incident, its impact, the actions taken to mitigate or resolve it, the root cause(s), and the follow-up actions to prevent the incident from recurring."
- Primary goals: "ensure that the incident is documented, that all contributing root cause(s) are well understood, and, especially, that effective preventive actions are put in place to reduce the likelihood and/or impact of recurrence."
- Triggers — five objective criteria plus discretionary: user-visible downtime/degradation beyond threshold; data loss of any kind; on-call intervention (rollback, reroute); resolution time above threshold; monitoring failure (manual discovery). "Any stakeholder may request a postmortem for an event." Criteria must be defined before the incident.
- Blameless tenet (Ch.15 "Blameless postmortems"): must "focus on identifying the contributing causes … without indicting any individual or team," assume "everyone … had good intentions and did the right thing with the information they had." Origin stated as healthcare/avionics. Prescription: "You can't 'fix' people, but you can fix systems and processes." Should still "call out where and how services can be improved" (two examples given).
- Example Postmortem structure (App.D incident #465, 2015-10-21, authors jennifer/martym/agoogler, Status Complete/action items in progress): fields Summary, Impact (1.21B queries), Root Causes (cascading failure + resource leak on unknown term), Trigger (latent bug + load surge), Resolution, Detection (Borgmon HTTP 500s), Action Items table, Lessons Learned (What went well / What went wrong / Where we got lucky), Timeline (UTC minute-anchored).
- Action Items mechanism (App.D table, 9 rows): columns Action Item | Type | Owner | Bug | Status. Types are `mitigate`, `prevent`, `process`, `other` — e.g., "Update playbook … mitigate … DONE", "Use flux capacitor … prevent … TODO Bug 5554823", "Schedule DiRT test … process … TODO". The type distinguishes whether the fix reduces impact now, removes the cause, or changes the process that allowed it.

Secondary sweep: the SRE book's Ch.14 "Managing Incidents" and Ch.16 "Tracking Outages" were not read; claims above rest only on Ch.15 + App.D as fetched.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` is the plant's follow-through vocabulary and guard, revised across this grid:

- 4I/Lawrence/Memory: `LEARNING_PROCESSES` / `POWER_MODES` / `POWER_FORMS` / `PROCESS_POWER_MAP` (Lawrence 2005 pp.181–183) and `ROUTING_POWER` plant analogy (initial/changed→influence, escalation→force, reminder→influence), with Clegg 2006 total-institution qualifier and Jurado-Zambrano et al. 2024 KM-cycle paragraph (storage+application implemented, acquisition/transfer/retrieval/protection not named). Helpers `follow_through_power_form` / `follow_through_mode_for_form` are heuristic detectors; no caller reads `ROUTING_POWER` yet.
- Projection `lines()` aggregates `_outcome_refusal_lines` (last-24h refusal audit, `REFUSAL_AUDIT_MAX_BYTES=262144`, total + coverage-denial count + last role/kind/reason + `blocked-by chat.log#sequence`) with three producer-freshness snapshots (substrate-watch 120s, initiative 180s, initiative-watch 180s).
- Guard `health()` checks each producer's `checked_at`/`generated_at` age, collects `problems`, retains `alert_key = hash(problems, require_initiative_watch)` in `checks/follow-through-health.json` and routes one alert per distinct problem set.

What it does not have is an SRE-style operational mechanism: no trigger that says "this refusal/freshness failure warrants a written record," no written-record template (impact/root cause/resolution), no typed preventive actions with owner/status, no lessons taxonomy. The 2026-11-01 mechanism for this cell (Ashby 1956 ch.11 requisite variety, applied) proposed a regulator table `reason-prefix → (remedy, owner)` and `unmapped N` count — a variety bound on routing, not a postmortem lifecycle. The 2026-11-01 and 2026-11-03 transfer/recent/critique cells propose per-class refusal breakdown (Hirschman), BGP path-vector loop pruning (RFC 4271), KM storage/application wording, and the Clegg enabling qualifier. None proposes a postmortem record with typed action items.

The already-embodied sweep for this field shows `follow-through`, `power` and `routing` vocabulary in docs and checks, but no `postmortem`, `blameless`, `mitigate`/`prevent`/`process` action typing, or `What went well/wrong/lucky` lessons in-tree (grep 2026-11-03).

## What is new here

1. **Operational mechanism, not philosophy.** SRE names a concrete, implementable loop: (a) objective triggers + stakeholder discretion decide when to write; (b) a fixed written record (Summary/Impact/Root Causes/Trigger/Resolution/Detection/Action Items/Lessons/Timeline) makes recurrence checkable; (c) action items are typed `mitigate|prevent|process` with an owner and a bug/status so closure is not "someone looked." This is a mechanism in the angle's sense: a file, a table and a status column, exercised by a reader of the next incident.

2. **Transfer to follow-through.** The plant's follow-through already creates the incident (a refusal row with `role/kind/reason/gaps→chat.log#sequence`; a freshness `UNKNOWN` with `problems` + `alert_key`). The gap is that it stops at a count + last reason + one-alert-per-episode hash. An SRE transfer would: on each `health() UNKNOWN` episode or each refusal class that exceeds a threshold, require a record under `artifacts/` (or `dms/`) with at least Summary/Impact/Root Cause(s)/Action Items typed mitigate/prevent/process and owned, plus Lessons triad and Timeline anchored to `chat.log#sequence` timestamps. The dashboard already has the audit tail to populate Impact ("N refusals of class X in 24h"); the guard already has `problems` to populate Root Causes; what is missing is the typed preventive action and its disposition.

3. **Distinction from Ashby.** Ashby bounds what a regulator cannot do with one response for six disturbances (`V(D)−V(R)`); SRE prescribes what a recorder must do so the next disturbance is less likely. Ashby would add `unmapped N` to the projection; SRE would add a postmortem artifact with `prevent TODO Bug …` that `unmapped` can point at. Both leave the writer's refusal rule unchanged; SRE adds the follow-through that closes the loop the doctrine requires ("no fix without continuing observation").

4. **Limits the source states and this review keeps.** Ch.15 is a practice report from Google's SRE culture, not a controlled study; it asserts blamelessness reduces hiding but measures no effect. The Shakespeare incident is a pedagogical, partially fictionalized example (2015, sonnet in a DeLorean's glove compartment, Borgmon, flux capacitor). Types mitigate/prevent/process are Google conventions, not a tested taxonomy for wall/chat follow-through. Blamelessness assumes systemic causation; the plant's refusals are rule-enforced denials (eligible-file check), not on-call mistakes, so "no blame" maps to "fix the rule's documentation/path, not the author." Nothing here shows that adding a template makes findings close faster; that is the experiment.

Distinction from prior reviews on this field: the 2026-11-01 mechanism (Ashby) is about regulator variety; this REVISIT is about postmortem record and typed closure. The 2026-11-03 critiques/recents/transfers (Clegg, Lawrence misread, Jurado KM, BGP) concern power-form typing and routing vocabulary, not incident closure mechanics.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

Add a boundary paragraph in the module docstring (beside the Lawrence 2005, Clegg 2006, Jurado-Zambrano 2024 and RFC 4271 paragraphs) citing Beyer et al. *Site Reliability Engineering* (2016) Chapter 15 "Postmortem Culture: Learning from Failure" (<https://sre.google/sre-book/postmortem-culture/>) and Appendix D "Example Postmortem" (<https://sre.google/sre-book/example-postmortem/>), stating: (a) follow-through's operational mechanism is a blameless postmortem: written record with Summary/Impact/Root Cause(s)/Trigger/Resolution/Detection, Action Items typed `mitigate|prevent|process` with Owner/Bug/Status (DONE/TODO) per App.D, Lessons Learned as What went well / What went wrong / Where we got lucky, and Timeline anchored to `chat.log#sequence`; (b) objective triggers are user-visible downtime, data loss, on-call intervention, resolution-time threshold, monitoring/manual-discovery failure, plus any stakeholder request (Ch.15), mapped here to `health() UNKNOWN` episodes and refusal-class thresholds; (c) this module currently projects counts/last-reason/problems and routes one-alert-per-episode but has no typed preventive-action artifact, so SRE closure (an `artifacts/` or `dms/` postmortem with typed action + owner) is the complementary mechanism to the Ashby `unmapped N` regulator already proposed. Keep the note that blamelessness means fixing guard/projection rules and paths, not authors.

Revertible by `git revert`. Verification: `python3 -m py_compile src/mishe_tauftauf/follow_through.py` and `python3 research/field-review/field_review.py check` must stay PASS; `python3 research/field-review/field_review.py coverage` unchanged except for this cell.

