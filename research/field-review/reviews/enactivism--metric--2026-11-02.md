---
field: enactivism
angle: metric
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity.py
---

# Graded adaptivity as a metric — viability distance and regulative cost, not binary alive/dead (Di Paolo 2005)

## What we read

- **Di Paolo, E. A. — "Autopoiesis, adaptivity, teleology, agency", *Phenomenology and the Cognitive Sciences* 4:429–452 (2005), doi:10.1007/s11097-005-9002-y**, PDF <https://hum.unne.edu.ar/postgrado/doctorados/cognitiva/apuntes/cursos/conferencia%20di%20paolo/Papers/DiPaolo_2005.pdf> fetched 2026-11-02 to `/tmp/dipaolo2005.pdf`, 172_265 bytes, sha256 `576e825142860f3b0fe2585cf43fa5baceab593bf78e08cf79d5c69c733a833b`, 24pp, `pdfinfo` Creator Textures LaserWriter 8 / Acrobat Distiller 7.0.5 2006-05-15, `pdftotext -layout` 978 lines retained; read pp.429–448 in full and notes scanned.

  - Metric read — viability set and adaptivity (p.438): an autopoietic system's **viability set** is assumed of finite measure, bounded, and possibly time-varying; **robustness** is endurance inside that range without active compensation. **Adaptivity** is strictly more (§Adaptivity p.438 box):

    > *A system's capacity, in some circumstances, to regulate its states and its relation to the environment with the result that, if the states are sufficiently close to the boundary of viability, (1) Tendencies are distinguished and acted upon depending on whether the states will approach or recede from the boundary and, as a consequence, (2) Tendencies of the first kind are moved closer to or transformed into tendencies of the second and so future states are prevented from reaching the boundary with an outward velocity.*

    This capacity may be dedicated mechanisms or an emergent aspect of realising autopoiesis (p.438) — it is not a consequence of the definition (pp.433–437). A **breakdown** is then defined as the *severity of a negative tendency distinguished and measured by the amount of regulative resources that it demands to compensate for it* with or without plastic re-structuring (p.438). That severity is the paper's primitive metric: not "alive/dead" but how much regulation a rated tendency costs.

  - Graded and recursive character (pp.439–441): adaptivity does not imply an optimal state — neutral spaces may remain equally adapted (p.440). It is graded by the range where regulation succeeds, and **recursive** in two senses: (i) adaptive processes regulate states not by proximity to the viability limit itself but by proximity to *activating more basic adaptive responses* (p.440, food preference avoiding reserve consumption), (ii) regulative processes themselves are plastically adjusted depending on effectiveness (p.440). Dysfunction then gets operational definitions via failures of adaptivity: exhaustion of resources, malfunction of regulation, loss of adaptive buffering provoking extreme regulation, disharmonious activation of conflicting mechanisms (p.440) — health is not statistical normality but many ways of being viable (Canguilhem 1966; Goldstein 1934, p.440).

  - Behaviour vs coupling (pp.442–443): **structural coupling** is the ongoing happening of non-lethal physical encounters; **behaviour** is the *regulation of* structural coupling — parametrical control of constraints by the organism's influence (ion channels, displacement, biofilms, p.442). Varela 1979/Maturana & Varela 1980 defined behaviour as any structural change while maintaining autopoiesis (pp.442–443) so "receiving a blow" counts as behaving. The paper separates them: *unregulated coupling is suffering an exchange; behaviour is the control and selection of what exchanges to suffer* (p.442). Activity is asymmetrical: only the organism regulates for itself (p.443, Ruiz-Mirazo & Moreno 2000 agent). Intentional structure is testable by inspecting organisation and its relation to viability, not by functional mimicry (p.443).

  - Temporality of the adaptive act (pp.444–445, after Langer 1967): an act has onset (sensing negative tendency) → acceleration (activation) → consummation (overturning) → cadence (de-activation), with minimal temporal granularity and direction (unlike conservation which is time-reversible, p.444). Concatenated acts produce valenced rhythms of tension/satisfaction; history follows from neutral-valence changes that leave the organism marked and constrain further dynamics (pp.444–445).

  - Limits retained: adaptivity needs autopoiesis to generate the norm — feedback alone does not generate purpose (Jonas 1966 critique, p.439 fn.7). Phenomenological guidance circulates with science but must be disciplined (Gallagher 1997 / van Gelder 1999 / Varela 1996/1999, p.432). Non-metabolic values (sensorimotor habits, social values, projects) require their own closed self-sustaining forms analogous to metabolism (§The road ahead pp.445–448) and are not claimed measured here; language/writing and other-minds deferred to Froese & Di Paolo 2011. Pre-revisit `enactivism--metric--2026-10-31` used Iizuka & Di Paolo 2007 stop-plasticity freeze as its metric; this revisit supplies the distinct graded-adaptivity metric above.

## What this plant already does

- `research/field-review/fields.json:98-101` declares `enactivism` as "sense-making, autonomy, the body" with keywords `enactiv`/`sense-making`/`autonomy`; `research/field-review/README.md:37` carries the cross-domain lens. `python3 research/field-review/field_review.py next` this wake reports ALREADY EMBODIED hits only via that declaration and predecessor enactivism reviews — no `viability set` / `breakdown` as regulative cost / `graded adaptiv*` / `regulation of structural coupling` vocabulary is embodied in `src/` or `docs/` outside reviews.

- `src/mishe_tauftauf/host_capacity.py` (305 lines) is the plant's independent host-resource boundary. `decide()` computes a row with `limited`, `slots`, `cooldown`, `render_timeout` from the latest `observe()` sample and holds `stable_seconds=120` before relieving pressure; `status()`/`guard()` report `UNKNOWN` when the controller is busy. The row is a binary/bounded gate: limited or not, known or unknown. It records no graded distance to the viability boundary in Di Paolo's sense and no measure of how much regulative resource a tendency demanded.

- `src/mishe_tauftauf/model_change.py:436-449` holds the edge-triggered `dispositions.edge()` dedupe and age ladder health→genome — already used as the target of the predecessor stop-plasticity metric. That metric asks whether a post-cut routing transition depends on stored `alerts` state (freeze test); this metric asks a different question: for a tendency near the viability boundary, what is its severity as regulative cost and where in the viability set does regulation succeed or fail.

- Health signalling elsewhere is binary over thresholds (RED/UNKNOWN) with stale-age gates, and trial-level reporting (`src/mishe_tauftauf/trial_metrics.py`) grades research practice (TOP/Leiden) but not organismic regulation. None distinguishes **robustness** (tolerating perturbations inside the viability set without active regulation) from **adaptivity** (monitoring tendency, distinguishing approach vs recession, and acting to overturn outward velocity near the boundary, p.438 criteria 1–2).

## What is new here

The 2005 source supplies an operational metric the plant has not embodied: **graded adaptivity measured as regulative-resource cost of a distinguished tendency near a bounded viability set**, with explicit contrasts to the plant's current binary gates.

1. **A viability set, not just a threshold.** The paper assumes the set is of finite measure, bounded, possibly time-varying (p.438) — not a single hard limit. The plant already varies bounds (host-pressure `CRITICAL`/`LIMITED`, render slots occupied, `stable_seconds` hold) but reports them as discrete states. The metric says to report *distance to boundary* and *outward velocity* of the tendency, because adaptivity is defined only "if the states are sufficiently close to the boundary" (box p.438).

2. **Breakdown as cost, not as death.** The primitive measure is not "did we lose autopoiesis?" (conservation, all-or-nothing, pp.435–437) but "how much regulation did this tendency demand to keep the future state from reaching the boundary with outward velocity?" (p.438). That lets the plant distinguish two `LIMITED` samples that both stay alive but demand very different regulative effort — a distinction the current `limited: true` cannot make, and the one Di Paolo uses to define health vs pathology (p.440).

3. **Recursion that is observable without moving a bound.** The first recursive sense (p.440) — regulation triggered by proximity to *activating a more basic adaptive response* rather than by direct viability danger — is directly observable: a degraded `LIMITED` episode that avoids reserve consumption (e.g., admission shedding before OOM) can be scored as more costly than one that rides through. The second recursive sense — plastic adjustment of regulation itself — maps to re-tuning how aggressively admission sheds load after effectiveness is observed.

4. **Behaviour as regulated coupling, not suffering coupling.** For the body track, the paper separates being perturbed (device telemetry arriving or not) from *regulating which exchanges to suffer* (p.442). The existing `sense.body.*` readers already stay `UNKNOWN` rather than inferring human presence — the metric says to score whether a subsequent regulation was *aimed at coupling itself* (parametrical control, p.442) vs passively endured.

Limits carried forward: a feedback loop without a self-generated viability norm does not create purpose (Jonas critique p.439; Varela 1991 sugar gradient pp.437–438). The metric does not claim an optimal state, does not replace the viability norm with a performance optimum (p.440), and does not make `adaptivity` a synonym for `adaptedness` (observer-relative adaptedness vs operational adaptivity, fn.4 p.31 via Froese & Di Paolo 2011). Non-metabolic values (habits, sensorimotor norms pp.446–447) are not measured by host-capacity resource cost and need their own closed forms; this review does not import them.

## Application

target: src/mishe_tauftauf/host_capacity.py

Propose one bounded prose annotation (header comment / module docstring, no logic or threshold change) that records the graded-adaptivity metric and its limit, so a future reader does not over-read `limited`/`UNKNOWN` as an adaptivity measure:

- Keep `decide()` / `status()` / `guard()` as **conservation/robustness** gates (binary viability: bounded set, alive vs disintegrated, pp.434–436) — necessary outer layer but not sufficient for adaptivity; `limited: true` and `UNKNOWN` alone do not satisfy the Di Paolo box criteria 1–2 (p.438).

- Add the enactive metric note (Di Paolo 2005, *Phenomenology and the Cognitive Sciences* 4:429–452, doi:10.1007/s11097-005-9002-y, §Adaptivity p.438 box, pp.439–441): viability set bounded finite-measure time-varying; adaptivity = capacity, in some circumstances, to regulate near the boundary such that (1) approach vs recession tendencies are distinguished and acted upon, (2) outward-velocity tendencies are moved/transformed so the future state is prevented from reaching the boundary with outward velocity; breakdown severity = regulative resources demanded to compensate (p.438); graded by range of success, neutral spaces permitted, recursive in both senses (p.440); dysfunction as adaptivity failure not as loss of autopoiesis (p.440).

- State the scoring discipline: a future claim of adaptivity would name the tendency (which host signal moving outward), the viability boundary it approaches (which resource limit), the regulative resources expended (admission shed, cooldown, slot hold), and whether criterion 1–2 held — without moving any health gate. Keep the plant's no-threshold-auto-change discipline (body-research wake 113202, genome 122590, `model_change.py:436-449`): the note does not drift a bound, it grades what regulation cost near the bound.

- Note the behaviour test (pp.442–443): distinguish suffering `structural coupling` (a poll arriving or not) from `regulation of coupling` (parametrical admission/cool-down aimed at coupling itself); only the latter counts as behaviour/agent regulation in this vocabulary.

No code change beyond comments/docstring is proposed by this review; a future implementable follow-on (outside this prose change) would audit one named `LIMITED` episode literally against the box criteria: state its viability boundary, its tendency and velocity, the regulative cost expended, and whether approach/recession was distinguished — exercising the metric against the paper's own limits without changing runtime.

