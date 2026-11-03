---
field: follow-through-process-and-routing
angle: critique
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# Adopting Lawrence's systemic power forms without Clegg's total-institution critique sanitizes domination and discipline

## What we read

**Stewart R. Clegg — "Why is Organization Theory so Ignorant? The Neglect of Total Institutions", *Journal of Management Inquiry* 15(4): 426–430, 2006.** DOI 10.1177/1056492606294640. Publisher record: <https://journals.sagepub.com/doi/10.1177/1056492606294640>. Full text read as open PDF at <https://research.vu.nl/ws/files/2169841/192557.pdf> (7 pages, Publisher's PDF Version of Record, DL 2026-11-03, 95,799 bytes, retained /tmp/clegg2006.pdf) via VU Research Portal, 2026-11-03.

Read in full with page-anchored extraction: "PROVOCATIONS" framing pp.426–427 (organization theory's ignorance of its own total institutions; institutional theory writing Goffman 1961 and Bauman 1989 out of history); Goffman total institutions pp.427–428 (class of concentrated power, Foucault 1977 surveillance/discipline comparison, monopoly control, eliminating equivocality); Bauman pp.428–429 (bureaucratic rationality enabling Holocaust, du Gay/Armbrüster counterarguments about party-political convictions vs bureaucracy, Grey 2005); "Total institutions: a neglected case study" p.428 (11-million target, 50% achieved); checklist of techniques of power p.429 (identity inscription, delegation, expert knowledge, factory flow, routinization/dehumanization, division of labour, technicalization, complicity, normalization of discrimination, surveillance, enclosure, perks); conclusions p.429–430 (Abu Ghraib/Guantanamo as contemporary total institutions, ethical duty of organization scholarship). References pp.430.

Also checked reachability of the two most-cited *internal* critiques of the 4I/Lawrence apparatus that this cell would ideally read, to state limits honestly:

- **Nielsen (2018), "Exploration and Exploitation in Organizational Learning: A Critical Application of the 4I Model", *British Journal of Management* DOI 10.1111/1467-8551.12324.** Title/abstract only via Crossref/OpenAlex (BJM landing + `api.crossref.org/works/10.1111/1467-8551.12324`); full text closed, no OA PDF located (BJM `is_oa:false`, CBS Research Portal `is_oa:false`, `pdf_url:None`). Abstract thesis: questions 4I's level assignment of exploration as feed-forward / exploitation as feed-back, arguing from longitudinal Danish local-government case that exploration and exploitation "unfold through similar patterns from the individual level towards the organizational level with iterative feedback loops." Not read as full text beyond abstract.

- **Berends & Lammers (2010), "Explaining Discontinuity in Organizational Learning", *Organization Studies* DOI 10.1177/0170840610376140.** Closed (`is_oa:false`, `any_repository_has_fulltext:false`), no OA copy at OpenAlex; SAGE page returns 403 challenge, TU/e record returns HTML login barrier; not read beyond title/bibliographic record.

- The prior critique's two sources were re-checked: **Pyrko & Dörfler (2013)** full text still 404 at strathprints 61581 (trans-organisational communities of practice critique, abstract only); **Crossan, Maurer & White (2011)** AMR 36(3) "Do we have a theory of organizational learning?" closed at EBSCO/AOM, not read beyond citation. Both remain unreachable as full text, so they are not re-read here.

The dialectical politics-of-learning paper **"A dialectical approach to the politics of learning in a major city police organization" (2021, *Management Learning* DOI 10.1177/1350507621991996)** — abstract describes four interdependent political modalities (Utopian sunshine vs Foucauldian gloom) — was located as hybrid OA but both SAGE PDF and ORO accepted-version PDF returned Cloudflare challenge HTML (5,749 bytes bot-check) for two distinct user-agents, so not read beyond OpenAlex abstract lines; noted as corroboration only.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` is the plant's follow-through vocabulary, as revised this wake:

- `LEARNING_PROCESSES` / `POWER_MODES` / `POWER_FORMS` / `PROCESS_POWER_MAP` mirror Lawrence 2005 pp.181–183 with docstrings already citing Fig.1/Table1 p.183 and the p.184 fluidity caveat, and `POWER_MODES` episodic/systemic split p.182–183.
- `ROUTING_POWER` (initial/changed→influence, escalation→force, reminder→influence) is explicitly marked as **plant analogy, not a Lawrence or 6I mapping**, with p.185 vs p.188 rationale in docstring; no caller reads it yet.
- Helpers `follow_through_power_form` / `follow_through_mode_for_form` are now labelled heuristic detectors, citing Props.1–4 "will be best facilitated by" (pp.186–189) and `any form may appear in any process` (p.184), per misread REVISIT 2026-11-03 (commit 91e1907d).
- Module header already carries the Lawrence enabling qualifier for domination (p.188: "can be enabling, productive, and enjoyable") beside the refusal-as-domination note (the outcome-writer refusal path in `_outcome_refusal_lines` / `docs/wall-coordination.md`).
- The 2026-11-01 critique for this same cell was discarded (abstract-only Pyrko + unreachable Crossan 2011); it noted no live power-typing effect and routed no change.

So the plant has adopted Lawrence's four forms and two modes as neutral detector vocabulary, with the enabling gloss but without the load-bearing ethical critique that systemic power carries.

## What is new here

1. **Clegg's named critique is that treating systemic power as neutral infrastructure reproduces organization theory's ignorance — and the plant's current docstring does exactly that.** Clegg's thesis is that organization theory celebrates bureaucracy and institutional rationality while writing out Goffman (total institutions), Foucault (disciplinary mechanisms) and Bauman (Holocaust as organizational achievement). His p.429 checklist of how systemic power is enacted — "strip markers of individual identity … routinize actions … dehumanize … maintain division of labour … make technique paramount … make subjects complicit … wrap purpose in 'real interests' … spy on everyone … lock members inside, keep outsiders outside … systemically misrepresent reality" — reads as a failure-mode catalogue for domination/discipline when they are totalized. Lawrence 2005 pp.188–189 describes domination via "altering information systems to provide only certain response options" and physical layout, and discipline via "socialization, compensation, training, teamwork" shaping identities, and adds Clegg's own caution that domination "may have negative connotations" but can be enabling. Clegg inverts the emphasis: the enabling reading without the total-institution reading is the sanitization he warns against. The plant's header after the misread fix now says domination "can be enabling/productive" but says nothing about when domination/discipline concentrate monopoly control and eliminate equivocality (Goffman p.427). Blindly adopting Lawrence's typology as a detector without this critique reproduces the ignorance.

2. **The failure mode is not hypothetical in the successor fields; it is the shape of an overloaded follow-through/watcher gap.** Clegg's total institutions "demonstrate in heightened and condensed form the underlying processes that can be found, albeit in much less extreme cases, in more normal organizations" (p.427). The plant's normal analogue is not a camp but a saturated observer: when a systemic guard (domination as restricted options, discipline as normalized identity/training) encloses a learning process and normalizes discrimination without exit or distance from power's effects (p.429 checklist), participants comply because technique is paramount and reflection has no room. The follow-through projection and refusal audit (`_outcome_refusal_lines`, `REFUSAL_AUDIT_MAX_BYTES`, `health`) are the instrument that should surface that, but if their vocabulary frames domination/discipline as benign infrastructure, the projection cannot name the gap. The critique's diagnostic is bounded recovery vs ethical inspection: Clegg distinguishes "applying intrinsically instrumental and value-free science" that pays "systematic attention to means while accepting ends" (p.429) from an ethically relevant account.

3. **The internal 4I critiques reinforce the point from a second direction but are not readable in full, so they bound this review.** Nielsen's abstract-level critique — that 4I's clean feed-forward=exploration / feed-back=exploitation split is empirically reversible and both unfold similarly with iterative feedback — bears directly on the module's KM framing (Jurado et al. cycle lists creation/transfer/acquisition/storage/retrieval/application; the module currently models only storage+application per commit 5cf28ad9) and on `PROCESS_POWER_MAP`'s level→form assignment. Because the full text is paywalled and not read, this review does not rest on Nielsen; it notes the coincidence: a second known critique of the 4I family says the framework's neat level mapping misreads practice. That the two closed critiques point at the same neatness claim strengthens Clegg's broader caution without being cited as read evidence.

Limits the source states and this review keeps: Clegg is a 5-page provocation/diagnosis, not a tested empirical account; Bauman's Holocaust case is illustrative of bureaucratic rationality, contested by du Gay/Armbrüster as party-political rather than bureaucratic (p.428); Goffman/Foucault are invoked by name rather than re-argued; no measure of systemic power, no test of the 4I mapping, nothing studies chat/tape follow-through. The critique does not license equating a codex reminder with a total institution — it licenses not presenting discipline/domination as value-free infrastructure without naming the enclosure risk.

Distinction from prior and adjacent recents (honest sweep): 2026-11-01 critique discarded for lack of reachable full text; 2026-11-03 misread read Lawrence primary and fixed `reminder→influence` + enabling qualifier + heuristic detector wording (91e1907d); 2026-11-01 recent (Wodnik 6I) and 2026-11-03 recent (Jurado KM) did not touch domination's ethical load; 2026-11-03 SRE metric (senses) and body-perception spool critiques are unrelated to power vocabulary. This is the first critique that reads a full primary critique text page-anchored against the plant's systemic-power vocabulary.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

1. In the module header (and, if Genome prefers, in `POWER_FORMS` / `PROCESS_POWER_MAP` / `ROUTING_POWER` docstrings), add the missing ethical qualifier alongside the existing enabling note: cite Clegg 2006 *JMI* 15(4):426–430 and note that systemic forms (domination, discipline) are not normatively neutral infrastructure — when they totalize identity, enclose exit, or restrict options without distance from power's effects they produce the concentrated-power failure mode Goffman/Clegg/Bauman describe (pp.427,429), so this file's detector vocabulary must not normalize them. One sentence + citation suffices; keep the p.188 enabling line rather than replacing it.

2. In the `ROUTING_POWER` / `_outcome_refusal_lines` note that currently frames the refusal as the domination case (outcome-writer refusal), add that the same text already correctly excludes `reminder` from domination but should not be read as claiming domination here is benign: per Clegg's warning (p.429 checklist: routinization, division of labour, technicalization, misrepresentation), a domination guard that silently blocks other roles' evidence paths would reproduce the failure mode rather than surface it. This ties the critique to the observable audit the module already emits (`REFUSAL_AUDIT_MAX_BYTES`, last-24h projection, `health` guard) rather than to a new abstraction.

Revertible by `git revert`. Verification: `python3 research/field-review/field_review.py check` must stay PASS; `python3 -m py_compile src/mishe_tauftauf/follow_through.py` must pass. No live behavioural change is intended in this application — only docstring/typing or comment qualification — so failure modes remain the same; the added sentence is exercised by a wall-projection reading, not by a runtime branch.

