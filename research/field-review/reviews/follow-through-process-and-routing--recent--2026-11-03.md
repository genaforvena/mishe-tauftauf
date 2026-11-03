---
field: follow-through-process-and-routing
angle: recent
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# People, process, culture as the KM↔OL bridge: the 4I is necessary but not sufficient for follow-through

## What we read

**Jurado-Zambrano, Mosquera Carrascal, Ruedas Mosquera — "Relación entre la gestión del conocimiento y el aprendizaje organizacional / Relationship between knowledge management and organizational learning", *SIGNOS — Investigación en Sistemas de Gestión* 16(2): 182–203, Jul–Dec 2024.** DOI 10.15332/24631140.10083 / 10084. Open-access PDF: <https://dialnet.unirioja.es/descarga/articulo/10226738.pdf> (fetched 2026-11-03, 612 KB, 22 pages, `CreationDate: Sat Aug  3 14:20:19 2024 UTC`, retained /tmp/dialnet_4i.pdf). Also indexed at <https://doi.org/10.15332/24631140.10083>.

Read in full: Resumen/Abstract, Introducción (ISO 9001:2015 §7.1.6 framing), Metodología (46 articles, content analysis per Downe-Wamboldt 1992, Tabla 1 document set), Resultados y discusión §§ Conocimiento organizacional (Tsoukas & Vladimirou 2001; Davenport & Prusak 1998; Penrose 1961), Gestión del conocimiento, Procesos de conocimiento (Gold et al. 2001; Alavi & Leidner 2001; Wilson & Campbell 2016; Lin & Lee 2006), and the synthesis § Relaciones including the three emergent categories plus Conclusiones. The Crossan et al. 1999 4I framework and Alavi & Leidner / Wilson & Campbell KM cycle are read here as the paper presents them; the originals were not re-read.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` types organizational learning through the Crossan 4I lens and Lawrence's politics of learning:

- `LEARNING_PROCESSES` / `PROCESS_POWER_MAP` (intuiting→discipline, interpreting→influence, integrating→force, institutionalizing→domination) — the only learning model named in the module, consistent with the prior REVISIT's 6I restatement.
- `ROUTING_POWER` as a plant analogy (initial/changed→influence, escalation→force, reminder→discipline) — explicitly not a Lawrence mapping since 2026-11-01, with the refusal audit as the untyped domination case.
- `lines()` / `_outcome_refusal_lines()` / `health()` aggregate follow-through as a projection plus a freshness guard: outcome-refusal audit (storage), initiative/substrate/watch snapshots (signal), and one-alert-per-episode routing. No caller reads the power-form helpers yet.

The module therefore models *learning processes* and *power forms* and stores *refusal evidence*, but it does not name the complementary *knowledge-management cycle* or the enablers the literature uses to explain why a cycle closes or stays open.

## What is new here

1. **The 4I alone does not close the loop; the paper pairs it with a KM cycle.** For organizational learning the authors use Crossan et al. 1999 4I; for knowledge management they use the KM cycle / processes of knowledge per Alavi & Leidner (2001) and Wilson & Campbell (2016), as refined by Lin & Lee (2006): acquisition, conversion, application and protection — and in the § Procesos de conocimiento enumeration, creation, transfer, acquisition, storage, retrieval and application (pp. 189–190, citing Gold et al. 2001; Alavi & Leidner 2001; Manesh et al. 2019; Shehzad et al. 2024). The paper's argument is that learning processes and KM processes must be managed together; availability of knowledge "facilita el proceso de absorción, permitiendo la apropiación de rutinas y la consolidación de capacidades" (Resumen, p. 182). The plant's follow-through currently names only the first half.

2. **Three enablers bridge the two cycles: people, process, culture (pp. 185, 209–211).** Content analysis of 46 sources yields exactly three emergent categories: "las personas como eje/foco de ambos conceptos, el enfoque de procesos y la cultura organizacional" as the common facilitator, plus the explicit conclusion that organizations "deben desarrollar ambos conceptos desde una perspectiva sistémica" (p. 183, Conclusiones). Each is defined in § Resultados:
   - *People*: knowledge resides in people; talent and competencies are "fundamentales" (Davenport & Prusak 1998; Bell 1984; Zia et al. 2023; § Conocimiento organizacional, pp. 187–188). AI can simulate but the paper keeps people "en el centro" (§ p. 188).
   - *Process*: KM as a structured coordination of acquisition→conversion→application→protection and the six-process variant (§ Procesos, p. 189).
   - *Culture*: ISO 9001:2015 §7.1.6 quoted verbatim — "La organización debe determinar el conocimiento necesario para la operación de sus procesos … y … evaluar su conocimiento actual y determinar cómo adquirir o acceder a cualquier conocimiento adicional" (p. 188, citing ISO 2015 p. 9) — then argued as requiring an organizational culture where "el conocimiento fluya libremente y los colaboradores sean valorados por poseerlo" (p. 188).

3. **What this changes for this file.** The plant's follow-through implements *storage* (refusal audit tail with `REFUSAL_AUDIT_MAX_BYTES`, artifact retention elsewhere) and *application* (routing stages, health guard) but has no named measure for the other KM processes (acquisition/creation/transfer/retrieval/protection) and no representation of the culture/people enablers. The paper does not measure an effect; it is a conceptual synthesis with a qualitative coding (46 documents, two pre-set categories). It proposes no mechanism or metric. Transfer is therefore analogical: if a follow-through failure is "knowledge was created (refusal written) but not retrieved (no reader) or not applied (no routing change)," the current dashboard shows the first and partly the third, but not the gap between them. The people/culture gap maps to the observed spread of refusals across roles (46 field-review, dozens per other role on 2026-11-01) — the paper would ask whether the spread reflects a person distribution or a culture guard — but the file has no per-role success-rate to answer it.

Limits the source states and this review keeps: qualitative, descriptive, document-analysis only; 46 sources selected via Google Scholar relevance; no measured effect of any enabler on any KM↔OL outcome; categories are emergent, not exclusive; AI/KM interaction is noted but not studied. Nothing here shows that adding the KM vocabulary would make the plant learn faster.

Distinction from the prior recent (Wodnik et al. 2024 / 6I): that REVISIT corrected the routing→power analogy and retyped reminder→discipline; this one does not change any power form. It complements the 4I model the module already cites with the KM cycle the same paper pairs it with, and names the missing enabler (culture/people) the prior reviews' Hirschman and SRE lenses do not.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

Add a boundary paragraph in the module docstring (or beside `LEARNING_PROCESSES` / `PROCESS_POWER_MAP` / `ROUTING_POWER`) citing Jurado-Zambrano et al. 2024 §§ Procesos de conocimiento / Relaciones / Conclusiones and ISO 9001:2015 §7.1.6 (p. 9 as quoted p. 188), stating: (a) the plant's follow-through models Crossan 4I + Lawrence power forms and currently implements KM storage (refusal audit, `REFUSAL_AUDIT_MAX_BYTES`) and application (routing/health), (b) the complementary KM processes per Alavi & Leidner / Wilson & Campbell are creation, transfer, acquisition, storage, retrieval, application (and Lin & Lee variant acquisition, conversion, application, protection), and (c) the paper's three enablers — people as axis, process focus, organizational culture as common facilitator — with the explicit gap that the module names no culture/people measure; any future wiring of acquisition/transfer/retrieval would need per-role attribution and a culture guard, analogous to the ISO requirement to "determinar el conocimiento necesario … y … cómo adquirir o acceder a cualquier conocimiento adicional." Keep the change docstring-only; no behaviour.

Revertible by `git revert`. Verification: `python3 research/field-review/field_review.py check` must stay PASS; `python3 -m py_compile src/mishe_tauftauf/follow_through.py` must pass. No live effect expected from a docstring.
