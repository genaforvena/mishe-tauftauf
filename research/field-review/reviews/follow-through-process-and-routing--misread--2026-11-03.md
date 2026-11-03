---
field: follow-through-process-and-routing
angle: misread
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# The reminder is not discipline and the propositions are not a classifier: reading Lawrence 2005 against follow_through.py

## What we read

**Lawrence, Thomas B., Michael K. Mauws, Bruno Dyck & Robert F. Kleysen — "The politics of organizational learning: Integrating power into the 4I framework", *Academy of Management Review* 30(1): 180–191, 2005.** DOI 10.5465/AMR.2005.15281451. Publisher record: <https://journals.aom.org/doi/10.5465/amr.2005.15281451>. Full text read as open PDF at <https://presencial.moodle.ufsc.br/pluginfile.php/1698118/mod_resource/content/3/2005%20-%20The%20Politics%20of%20Organizational%20Learning.pdf> (100,019 bytes, 12 pages, CreationDate 2017-09-18T14:26:50Z via PDFium, retained /tmp/lawrence_2005_AMR.pdf), 2026-11-03.

Read in full with page-anchored extraction: abstract (p.180) mapping discipline→intuiting / influence→interpreting / force→integrating / domination→institutionalizing; Crossan et al. 1999 4I summary pp.181–183; episodic vs systemic power pp.182–184 (Clegg 1989; Foucault 1977; Giddens 1984; Hardy & Clegg 1996; Lawrence et al. 2001); Scott (2001) means distinction p.185; Propositions 1–4 and Table 1 / Fig.1 pp.185–189; discipline/identity and expertise discussion pp.188–189; domination as enabling not merely repressive p.188; pathological modes and exploitation/exploration tension pp.189–191; Conclusion. The 6I expansion (Wodnik et al. 2024) and the KM↔OL synthesis (Jurado-Zambrano et al. 2024) were not re-read here; their restatements of Lawrence are checked only against this primary text.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` models Crossan 4I + Lawrence politics:

- `LEARNING_PROCESSES` = intuiting/interpreting/integrating/institutionalizing (`Crossan et al. 1999 4I (Lawrence 2005 p.181)`).
- `POWER_MODES` = episodic/systemic (`Lawrence 2005 p.182-183`).
- `POWER_FORMS` = influence/force/domination/discipline (`Hardy 1996 via Lawrence p.182-183`).
- `PROCESS_POWER_MAP` = interpreting→influence, integrating→force, institutionalizing→domination, intuiting→discipline (`Fig.1/Table1 p.183; not exclusive p.184`).
- `ROUTING_POWER` = initial/changed→influence, escalation→force, reminder→discipline — explicitly labelled since 2026-11-01 as **plant analogy, not a Lawrence or 6I mapping**; docstring notes reminder restricts no option so not domination and that no caller reads the mapping yet.
- Helpers `follow_through_power_form(*, stage, process, kind)` and `follow_through_mode_for_form` are pure deterministic typers (stage→form, form→mode).
- Module header was extended 2026-11-03 (commit 8750426a) with Jurado et al. 2024 KM-cycle paragraph and ISO 9001:2015 §7.1.6 framing.

The 2026-11-01 recent (Wodnik REVISIT) corrected `reminder` from domination to discipline and the routing citation; the 2026-11-01 misread was discarded as 403 on the full text and verified only the abstract. This is the first misread that reads the primary text side-by-side with the plant's text.

## What is new here

1. **The four process→form pairs are accurate; the mode pairing that underwrites them was unverified and now verifies — with a nuance the plant elides.** Table 1 p.186 and text pp.183–185 pair interpreting→influence and integrating→force as **episodic**, and institutionalizing→domination and intuiting→discipline as **systemic**, via the Scott (2001) means split (costs/benefits vs restricting available actions) and Lawrence et al. (2001) naming influence/force (episodic) and domination/discipline (systemic). The plant's `PROCESS_POWER_MAP` and `POWER_MODES` docstring `episodic (influence, force) vs systemic (domination, discipline)` matches pp.182–183. Prior reviews flagged POWER_MODES as unverified; the full text confirms it.

2. **`reminder → discipline` is still mis-typed under Lawrence's own definition — the closer Lawrence fit is `influence`, not `discipline`.** The plant moved `reminder` correctly out of domination (domination = restricting available actions via material technologies/information systems/physical layout, p.188), but its new home is wrong on the episodic/systemic axis:
   - **Discipline in Lawrence is systemic, ongoing, identity-forming** — "an ongoing, systemic engagement … shaping … understanding of the costs and benefits … Important forms include socialization, compensation, training, and teamwork" (p.188, citing Foucault 1977; Knights & Wilmott 1989; Townley 1993), whose effect is "shaping the actual formation of members' *identities*" and providing "deep levels of substantive experience in a domain" (pp.188–189). A single reminder DM is not that. It is a discrete act, not a routinized system.
   - **Influence in Lawrence is episodic and does exactly what a reminder does** — "affecting the costs and benefits that organizational members associate with specific interpretations" (p.185) via "moral suasion, negotiation, persuasion, ingratiation, and exchange" (p.185). Propositions 1 and 4 both turn on costs/benefits, but Proposition 1's carrier is episodic influence and Proposition 4's is systemic discipline. A reminder re-presents a finding and nudges its cost/benefit without restricting options or socializing identity — the paper's description of influence (p.185) not discipline (p.188).
   - **Measured consequence:** the plant's deterministic `ROUTING_POWER["reminder"] == "discipline"` classifies a one-shot episodic nudge as systemic, which reverses the paper's episodic/systemic cut. If the plant wants a routing→power vocabulary, the Lawrentian mapping for a reminder is influence; keeping it as discipline should at minimum be documented as a weak, non-Lawrentian analogy.

3. **`follow_through_power_form` / `follow_through_mode_for_form` present propositions as a deterministic classifier, while Lawrence presents them as probabilistic "most likely" propositions with explicit fluidity.** The paper repeats the caveat: "any form of power might be evident in any of the four processes … one form … likely to be most effective" (p.184) and each proposition is phrased "will be best facilitated by" (Props. 1–4, pp.186–189). The helpers return a single form for a stage/process and `None` otherwise, reading as a typing rule. The source does not license a classifier; it licenses a hypothesis about which strategy is hypothesized to increase success odds.

4. **The plant's gloss of domination as "restricting response options" omits the paper's enabling qualifier, which matters for how the refusal path should be described.** Lawrence stresses "the term *domination* may have negative connotations … we in no way mean to imply that it is simply repressive; systems of domination can be enabling, productive, and enjoyable" (p.188), with manufacturing and information-system examples that *produce* a working practice, not merely block alternatives. The module's docstring correctly notes the refusal as the domination case but frames domination only restrictively; the source frames it as infrastructure that makes a practice available without repeated intervention (p.184).

Limits the source states and this review keeps: conceptual note, no measured effect; Propositions are not tested in the paper; typology is drawn from Hardy 1996 / Clegg 1989 / Scott 2001 / Lawrence et al. 2001 and is "relatively exhaustive" by assertion, not by test (p.185); the 4I itself is Crossan et al. 1999; nothing in the note studies routing, reminders, or software follow-through. Nothing here shows that re-typing a reminder changes learning.

Distinction from the two prior recents: the 2026-11-01 recent (Wodnik/6I) fixed the routing citation and moved reminder out of domination; the 2026-11-03 recent (Jurado/KM↔OL) paired 4I with the KM cycle and named people/process/culture enablers without touching power forms. This misread does not revisit either analysis. It reads Lawrence directly, confirms the four `PROCESS_POWER_MAP` pairs and the episodic/systemic split, but shows the *current* `reminder→discipline` still misapplies Lawrence's systemic definition and that the deterministic helpers overstate probabilistic propositions.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

1. In `ROUTING_POWER`'s docstring, correct `reminder` from `discipline` to `influence` — or, if the plant wishes to keep `reminder→discipline` as its own convention, add one sentence that under Lawrence discipline is systemic/ongoing/identity-forming (p.188) while a reminder DM is an episodic influence act (p.185), so the mapping is a plant convention not a Lawrence typing. Keep the domination caveat (restricted options) that the prior review added.

2. In the module header and/or `follow_through_power_form` / `follow_through_mode_for_form` docstrings, soften deterministic typing to Lawrentian propositions: note Props. 1–4 are "will be best facilitated by" hypotheses (pp.186–189) and that "any form may appear in any process" (p.184), so the helpers are heuristic detectors, not a classifier.

3. In the `_outcome_refusal_lines` / domination note, add the paper's enabling qualifier (p.188: domination can be enabling/productive) so the refusal-as-domination note does not read as purely repressive.

Revertible by `git revert`. Verification: `python3 research/field-review/field_review.py check` must stay PASS; `python3 -m py_compile src/mishe_tauftauf/follow_through.py` must pass. No live effect expected from docstrings; any re-typing of `ROUTING_POWER["reminder"]` should be exercised through `follow_through_power_form(stage="reminder")` and `follow_through_mode_for_form` in a throwaway check (reminder→influence→episodic vs current discipline→systemic).
