---
field: outcome-publication-and-chat-protocol
angle: metric
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/chat_protocol.py
---

# Outcome publication and chat tape lifecycle — metric REVISIT: PARADISE weighted performance (Kappa + normalized costs) for dialogue publication

## What we read

- Marilyn A. Walker, Diane J. Litman, Candace A. Kamm, Alicia Abella, **"PARADISE: A Framework for Evaluating Spoken Dialogue Agents"**, *Proceedings of ACL/EACL 1997* (P97-1035). Primary text fetched this wake as PDF to `/tmp/P97-1035.pdf` (969 KiB, sha256 b0c51a1350c020d8ec02a9733efc908f0f218e8b9a31438ff772b079258fba48, 1624-line `pdftotext` at `/tmp/P97-1035.txt` sha256 41a6aafb0bde0051a79c21ab9f3fd3608edc342168872ff3a8fed3d5aa7fe90c) and viewed at https://aclanthology.org/P97-1035.pdf (canonical) / https://aclanthology.org/P97-1035/ and arXiv cmp-lg/9704004.

  Sections read this wake from the extraction (not a summary of a summary): Abstract (decouples task requirements from dialogue behaviors, supports subdialogue calculation, specifies relative contribution, normalizes for task complexity), §1 Introduction (reference-answer limits, Danieli & Gerbino train-timetable Agent A/B comparison, lack of generalization and factor analysis), §2 Performance Model — §2.1 Tasks as Attribute Value Matrices (AVM decoupling What from How), §2.2 Measuring Task Success via Kappa, §2.3 Measuring Dialogue Costs (ci over (sub)dialogues via AVM tagging), §2.4 Estimating a Performance Function (Z-score normalization + regression on user satisfaction), §2.5 subdialogue calculation, §3 generality.

  Passages relied on (verbatim from `/tmp/P97-1035.txt`):

  1. Abstract: PARADISE "decouples task requirements from an agent's dialogue behaviors, supports comparisons among dialogue strategies, enables the calculation of performance over subdialogues and whole dialogues, specifies the relative contribution of various factors to performance, and makes it possible to compare agents performing different tasks by normalizing for task complexity."
  2. §2 model framing: "PARADISE uses methods from decision theory (Keeney and Raiffa, 1976; Doyle, 1992) to combine a disparate set of performance measures (i.e., user satisfaction, task success, and dialogue cost) into a single performance evaluation function." and objective structure Fig.1 — maximize user satisfaction as external criterion.
  3. §2.1 AVM: "An attribute value matrix (AVM) can represent many dialogue tasks. This consists of the information that must be exchanged between the agent and the user during the dialogue, represented as a set of ordered pairs of attributes and their possible values." — decouples What from How equally for radically different dialogue strategies (Figures 2 vs 3 share same Table 2 key).
  4. §2.2 Kappa: "Given a confusion matrix M, success at achieving the information requirements of the task is measured with the Kappa coefficient (Carletta, 1996; Siegel and Castellan, 1988): K = (P(A) - P(E)) / (1 - P(E))" where "P(A) is the proportion of times that the AVMs for the actual set of dialogues agree with the AVMs for the scenario keys, and P(E) is the proportion of times that the AVMs for the dialogues and the keys are expected to agree by chance" with P(A)= sum M(i,i)/T and P(E)= sum ti^2 / T^2 (ti column sum, T total). "K is superior to transaction success, concept accuracy, and percent agreement because K takes into account the inherent complexity of the task by correcting for chance."
  5. §2.3 cost measures: "PARADISE represents each cost measure as a function ci that can be applied to any (sub)dialogue." Examples "number of turns or elapsed time … inappropriate or repair utterances" — ci tagged by AVM attributes so "each attribute accounts for 1/N of any costs derivable from" an utterance contributing to N attributes; greetings tagged with all attributes.
  6. §2.4 performance equation: "Performance = (alpha · N(K)) - sum wi · N(ci)" where "N is a Z score normalization function … N(x) = (x - x_bar) / sigma_x" to overcome different scales, and "weights alpha and wi must be solved for … Multiple linear regression produces coefficients describing the relative contribution … US is treated as the predicted factor. Normalization … guarantees that the relative magnitude of the coefficients directly indicates relative contribution." Hypothetical result: "Performance = .40 N(K) - .78 N(c2)" (only K and #rep significant p<.02).
  7. §2.5 subdialogue: performance can be calculated for subdialogues as well as whole dialogues because AVM tagging links information goals to utterances; §3 generality: same function structure across tasks enables comparison normalized for task complexity.

  Not read in full: complete reference apparatus beyond quoted pages, Table 5 regression raw data in full, and follow-on PARADISE journal extensions; the hypothetical-data illustration (§2.4 eight+eight users) is treated as illustrative, not as empirical validation of a specific alpha/wi value.

## What this plant already does

- `src/mishe_tauftauf/chat_protocol.py` decodes lifecycle completions (`_WAKE_RE/_YIELD_RE/_CLEAR_RE`, `decode_lifecycle`, `decode_site_sync`, `grounding_ack`) and carries advisory vocabularies added this week — AutoGen conversation patterns/capabilities/termination (`CONVERSATION_PATTERNS/AGENT_CAPABILITIES/CHAT_TERMINATION_TOKENS` + validators) and Suchman guardrail (`CHAT_VOCABULARY_POLICY="advisory"`) — but has no dialogue performance metric, no Kappa, no cost normalization, no weighting.
- `src/mishe_tauftauf/trial_metrics.py` already measures window-to-window noise via `placebo_scale` (coverage-gated `PLACEBO_WINDOW`, `PLACEBO_COUNTS`), `campbell_design2_audit`, `huang_checks`, Leiden principles, TOP Factor retired, and `per_1k_chars_seconds` latency normalization per 1k chars. None uses chance-corrected Kappa, Z-score `N(x)`, or regression on user satisfaction; `PLACEBO_COUNTS` covers wakes/redeliveries/settlements/observations/addressed_messages but not dialogue costs; predecessor `outcome-publication--metric--2026-11-01` (Manheim Goodhart) applied to the same trial file for outcome-entry placebo baseline — different mechanism (posting noise) from this one (dialogue success vs cost weighting).
- `src/mishe_tauftauf/outcome_publication.py` fixes `OUTCOME_KINDS = (accepted, blocker-resolved, blocker-retired, hypothesis-changed)` and `OUTCOME_PREFIX` header; no confusion matrix, no kappa, no cost vocabulary.
- `src/mishe_tauftauf/outcome_events.py` / `witness_analysis.py` classify and advise on prior context but do not compute a unified performance score; tape sweeps for `lifecycle|completion|tape` hit decoders and docs only — zero hits for `Kappa`, `N(K)`, `P(A)/P(E)`, `Performance.*N(ci)`, or `Z score normalization` before this review (occurrence sweep, not meaning).
- User satisfaction as external criterion (Kamm 1995 single-item or multi-item mean, Fig.1) has no analogue in this plant's reporting; operator views and wall patches note check times but not usability-correlated weighting.

## What is new here

PARADISE contributes four ideas absent from this plant's outcome/chat vocabulary, each bounded by what the paper concedes:

1. **Chance-corrected success (Kappa) instead of raw agreement.** Transaction success / concept accuracy / percent agreement overstate success when keys are skewed. Kappa `K=(P(A)-P(E))/(1-P(E))` corrects for chance, enabling comparison across tasks of different complexity. Transfer: wall outcomes carry an information requirement (kind + evidence digest + prose per `outcome_publication.py` header/reference), but success is currently boolean decode-or-gap. A Kappa-like confusion matrix over `OUTCOME_KINDS × evidence binding` (rows observed vs columns key) would distinguish 80% correct on a uniform vs skewed kind distribution, which raw success cannot.

2. **Cost functions over (sub)dialogues via AVM tagging.** PARADISE represents each cost as `ci(D)` applicable to any (sub)dialogue, with AVM attribute tags deciding costs per subtask (1/N split) and subdialogue segmentation (e.g., Fig.4 hierarchy S2→S3,S4). Transfer: the chat tape is already segmentable by lifecycle (`seed wake/seed yield/seed clear`) and wall patch sequences; tagging a segment by its `outcome_events`/wall goal attributes yields per-subdialogue costs (turns, elapsed time, repair utterances) without changing decoders, and makes wall-patch retry loops accountable as subdialogue costs rather than whole-tape averages.

3. **Z-score normalization to compare incommensurates.** Costs live on different scales (seconds vs utterance counts); PARADISE normalizes each factor `N(x)=(x - x_bar)/sigma` before combination so weight magnitudes are comparable. Transfer: `trial_metrics.py:per_1k_chars_seconds` normalizes latency by volume, but the plant has no cross-metric Z-score; adding it would put `time-to-yield` (seconds) and `repair-utterances` (counts) on the same axis before any weighting.

4. **Regression on user satisfaction to learn weights, not stipulate them.** Weights `alpha, wi` are not stipulated; they solve `US ~ alpha N(K) + sum wi N(ci)` via multiple linear regression, with second regression retaining only significant predictors (p<.02 in illustration). Conclusion `Performance=.40 N(K) - .78 N(c2)` — repair cost outweighs success in that domain — is an empirical finding, not an axiom. Transfer: operator satisfaction / sweeper liveness review (e.g., weekly SUS or explicit `Turn settled(verified)` vs `changed` as proxy) could serve as US; without it, weights are opinion. The mechanism names the gap rather than filling it.

  Why this is not the predecessor Goodhart metric: Goodhart warns the reported outcome count is selected-on and noisy without a placebo swing; PARADISE warns that even a correct count is insufficient — success and multiple costs must be jointly weighted by their observed contribution to an external satisfaction criterion, with chance correction and normalization. The two compose (Goodhart gives the per-count noise floor; PARADISE gives the cross-factor combination) but are orthogonal proposals to different symptoms.

## Application

target: src/mishe_tauftauf/chat_protocol.py

Proposal for the owner (genome, who owns `chat_protocol.py` decoders and the tape dialect; change is additive vocabulary only, revertible by single-commit revert, isomorphic to recent AutoGen/Austin/Suchman/CloudEvents blocks — no new runtime, no decoder gate, no emitted bytes until a future consumer opts in):

1. Beside the existing vocabulary blocks (AutoGen §2.1/§2.2, Suchman guardrail, CloudEvents envelope note if present), add a **PARADISE metric vocabulary comment** citing `Walker et al. 1997 P97-1035 §2.2-§2.4 (ACL Anthology https://aclanthology.org/P97-1035.pdf) — Kappa success, ci costs over (sub)dialogues via AVM tagging, Z-score N(x), Performance=alpha N(K)-sum wi N(ci) with US regression; single confusion matrix inflates K when cross-attribute confusions few (p.274 fn.8)`.

2. Add pure, advisory constants co-located with that comment (names illustrative, `Final`, owner chooses identifiers):

   ```python
   PARADISE_KAPPA_FORMULA: Final[str] = "K=(P(A)-P(E))/(1-P(E))"
   """Kappa per PARADISE §2.2 (Carletta 1996; Siegel & Castellan 1988) — chance-corrected agreement."""

   PARADISE_COST_MEASURES: Final[tuple[str, ...]] = ("utterances", "repair_utterances", "elapsed_time", "inappropriate_utterances")
   """Example ci per PARADISE §2.3 — each ci(D) applicable to whole dialogue or subdialogue via AVM tagging; 1/N split for multi-attribute utterances."""

   PARADISE_PERFORMANCE_SPEC: Final[str] = "Performance=alpha·N(K)-sum wi·N(ci)"
   """Performance per §2.4 — N(x)=(x-x_bar)/sigma, weights via regression on user satisfaction US; subdialogue-capable per §2.5."""
   ```

3. Add three pure predicate helpers (no I/O, no Feed read) in the same module:

   ```python
   def paradise_kappa(*, agreements: int, total: int, chance: float) -> float | None: ...
   """K from P(A)=agreements/total and P(E)=chance; None when total==0 or chance==1."""

   def paradise_normalize(value: float, mean: float, sd: float) -> float | None: ...
   """N(x)=(x-mean)/sd; None when sd==0."""

   def paradise_performance(*, kappa_norm: float, cost_norms: dict[str, float], weights: dict[str, float]) -> float | None: ...
   """alpha N(K) - sum wi N(ci); None on missing weight; pure, advisory."""

   def paradise_cost_for_subdialogue(cost_full: float, attributes_in_utterance: int) -> float: ...
   """1/N split per §2.3 — utterance contributing to N attributes counts 1/N per attribute."""
   ```

   Mark `UNACTIVATED: no caller in running pin, same posture as AutoGen/Austin/CloudEvents blocks` until `trial_metrics.py:report` or an operator-view consumer opts in. Existing `decode_lifecycle/decode_market/decode_site_sync` tolerant-prose behavior unchanged; existing tests unchanged; add a future vocabulary-only test that `paradise_kappa(agreements=795,total=1000,chance=0.079)` ≈ 0.777 (Agent A), `paradise_normalize(…)` matches Z-score, and `paradise_cost_for_subdialogue(1.0,2)==0.5` (D2 repair split).

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.chat_protocol import PARADISE_KAPPA_FORMULA, paradise_kappa; print(paradise_kappa(agreements=795,total=1000,chance=0.079))"` prints ~0.777, `tools/check_parsing.py` PASS, and `python3 research/field-review/field_review.py check` remains PASS (328 landed after this file, was 327). Observation: run those three checks on the landed commit. Decision: keep if advisory-only and checks pass; revise helpers to return None on degenerate inputs if check flags them; revert single commit if owner finds vocabulary unhelpful — no migration debt because no caller.

## Limits

- PARADISE's hypothetical data (eight Agent-A + eight Agent-B users, Table 5) is illustrative; the `Performance=.40 N(K)-.78 N(c2)` result is not empirical evidence for this plant's tape — only the mechanism transfers. Real weights require a `US` collection (Kamm single-question or multi-item mean) this plant does not yet run; without it, any alpha/wi is stipulated, not learned.
- Single confusion matrix averaging `P(E)` across all attributes inflates K when cross-attribute confusions are rare (p.274 fn.8); PARADISE notes `K` should then be computed per attribute or averaged per attribute — the `paradise_kappa` helper above does not cure that; callers must choose the matrix scope.
- Linear regression assumes linear contribution of success/costs to satisfaction (Fig.1 decision-theoretic additive model); violations (interaction effects, ceiling effects on US) are outside the 1997 formulation — contemporaneous critiques (Hajdinjak & Mihelic J-06-2004 survey) note alternative weightings and task-dependence.
- Z-score normalization requires a normative population (sigma); on a small wall with few placebo windows sigma is unstable — composes with the Goodhart `placebo_scale` concern but does not replace it.
- Owner is `genome` because Genome owns `chat_protocol.py` decoders and tape dialect; field-review does not edit that file itself in this turn.
