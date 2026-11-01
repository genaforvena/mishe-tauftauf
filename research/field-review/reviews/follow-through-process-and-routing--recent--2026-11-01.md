---
field: follow-through-process-and-routing
angle: recent
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# Power forms for routing stages: a 2024 extension shows the reminder mapping is unsourced and the refusal path is the real domination case

## What we read

**Wodnik, Andiappan, Di Ruggiero, Lavery — "The 6I model: an expanded 4I framework to conceptualise interorganisational learning in the global health sector", BMJ Open 14(5): e083830, May 2024.** DOI 10.1136/bmjopen-2023-083830. Open-access record: <https://bmjopen.bmj.com/content/14/5/e083830> (publisher returned HTTP 403 to our fetch; full text was read from the Europe PMC open-access XML, PMCID PMC11141175, <https://europepmc.org/article/PMC/PMC11141175>, 4,079 words, extracted to the scratchpad).

Read sections: Introduction (power and the 4I politics, paragraphs 21–30); Expansion 1, Rationales 1–3 (episodic influence best for sharing across partners); Expansion 2, Rationales 4–6b (episodic influence for feed-forward to a coordinating body; systemic domination for feedback institutionalisation); Discussion; Conclusion. The Lawrence et al. 2005 original was not read directly; its mapping is read here only as the 6I paper restates it. The code's "Lawrence p.182–183 / p.184" page citations are therefore not verified by this review.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` (committed in df5e1b6b) types two things:

- `PROCESS_POWER_MAP`: interpreting→influence, integrating→force, institutionalizing→domination, intuiting→discipline. The 6I paper restates exactly this mapping (paragraphs 27–30), with the same "most effective, not exclusive" qualifier. This part is consistent with its source.
- `ROUTING_POWER`: initial and changed→influence, escalation→force, reminder→domination. The module docstring attributes this to Lawrence p.182–183 and calls it "routing stage→power form".

Callers: `follow_through_power_form` and `follow_through_mode_for_form` have no caller anywhere in `src/`, `tests/` or `checks/` (grep, 2026-11-01). The routing mapping is vocabulary without a consumer. The live refusal path it is meant to type is `_outcome_refusal_lines` in the same module; the dashboard reports 184 outcome-writer refusals in the last 24h.

## What is new here

1. **Lawrence's power forms do not produce a routing mapping.** Neither the 6I paper nor (by its own description) Lawrence 2005 has routing stages. The plant's `ROUTING_POWER` is therefore an analogy the plant made, but the docstring cites Lawrence for it. A reader would take the citation as support that the source does not give.

2. **The 2024 paper defines domination by what it does, and a reminder does not do that.** The paper says domination "is most influential in the institutionalisation process … in part because change … must be overcome through the restriction of available behaviours", and gives the example of "altering information systems to provide only certain response options" (Rationale 6b, paragraphs 30 and 99). A reminder re-presents a finding and leaves every response option open. By the paper's definition it is not domination. The closer fit in the source is discipline ("training, socialisation, or team-based work", paragraph 30), or simply influence, which "shap[es] the costs and benefits of certain behaviours" (paragraph 27). This is an interpretive move, and the source does not classify reminders at all.

3. **The source's own domination case is the refusal path.** Restricting the options a writer may take is exactly what the outcome-writer refusal does: it returns a fixed response (an "eligible single file" rule) and removes the others. The module already has a detector for this, but it does not type it. If the plant wants the power vocabulary to carry any information, the refusal is where domination is observable. A reminder is not.

4. **Institutionalisation is measured by change to routine, not by delivery.** The 6I paper says ideas become institutionalised through "directives, manuals, strategies and routine practices" (paragraph 88), and that not every structure is equally effective at it (paragraph 99). This agrees with the plant's existing settlement rule (a transport fact is not proof of a patch). A reminder count cannot show institutionalisation; a changed artifact or a closed stage can.

Limits the source states and this review keeps: the 6I paper is conceptual, with no measured effect of any power form on learning. Its mappings are propositions ("will be best facilitated by"), not findings. Its evidence is a narrative review and a global-health case frame. No routing or follow-through system is studied in it. Nothing here shows that the plant's routing actually produces any power effect.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (the owner decides; this review does not edit the module):

1. Change the `ROUTING_POWER` docstring so it states the mapping is a plant analogy and not a Lawrence (2005) mapping. Remove the Lawrence page citations from that dict.
2. Re-type `reminder` from `domination` to `discipline`, or drop the reminder entry. Record in the docstring that the 2024 source does not classify reminders, and that domination requires restricted response options.
3. Type the refusal path as domination in `_outcome_refusal_lines` (or a helper it calls), since it restricts options as the source describes. Keep the output as a report line, not a gate.
4. Add a test that exercises the mapping through a real caller, because the current functions have none. A table-only test would not show anything. Until a caller exists, the functions stay vocabulary and the docstring should say so.

Detection interval and check: after any change, run the field-review contract check and the module's tests. Live effect is not expected from a docstring change. The refusal typing would be observable on the dashboard as a power-form label on the refusal count, and that label should be checked against the next day's refusal lines.
