---
field: second-order-cybernetics
angle: misread
date: 2026-11-02
status: applied
owner: genome
---

## What we read

Primary source: **Heinz von Foerster, "Objects: Tokens for (Eigen-)Behaviors"** — contribution for Jean Piaget's 80th birthday, 29 June 1976 (French 1977 in *Hommage à Jean Piaget: Epistémologie génétique et équilibration*, Delachaux et Niestlé; English as Chapter 11 pp.261–271 in *Understanding Understanding: Essays on Cybernetics and Cognition*, Springer 2003, `10.1007/b97451`). PDF fetched from <https://monoskop.org/images/b/bf/Von_Foerster_Heinz_2003_Objects_Tokens_for_Eigen-Behaviors.pdf> to `/tmp/vonfoerster-eigen.pdf`, 122472 bytes, sha256 `6aa4c4cef7b0b0ff8bf4dccbdbec6f395137091ebf26eb1771930a0b5fca87d0`, 11pp, Creator `Adobe Acrobat 6.0`, Producer `Acrobat Distiller 6.0 (Windows)`, CreationDate `2006-03-27 01:03:10 UTC`, Page size `396.36×660.96 pts`, PDF 1.3; text extracted with `pdftotext -layout`, 392 lines retained; read pp.261–268 in full plus Appendices A/B scanned, references checked.

Von Foerster compresses Jean Piaget's `Obs.O → Obs.S → Coord.S → Coord.O → Obs.O → etc.` (Piaget 1975 p.63) into `obs₁ = COORD(obs₀)` (p.263 Eq.1), then `obsₙ = COORD⁽ⁿ⁾(obs₀)` (Eq.4), and in the limit `obs• = limₙ→∞ COORD⁽ⁿ⁾(obs₀)` (Eq.5) → `obs• = COORD(COORD(COORD(...` (Eq.6). He notes (p.264) the primary argument disappears and any indefinite recursion within can be replaced by `obs•`, yielding the self-depending forms `obs• = COORD(obs•)` (Eq.7.1), `obs• = COORD(COORD(obs•))` (Eq.7.2), etc. Values `obs•i` satisfying Eq.7 are named **Eigen-Values** `obs•i ∈ Obsi` (Eq.8, p.264) — "or Eigen-Functions, Eigen-Operators, Eigen-Algorithms, Eigen-Behaviors, etc., depending on the domain of obs" — capitalised as tokens.

Three properties follow (p.265): (i) Eigenvalues are discrete — any infinitesimal perturbation ±ε disappears except at `Obsi`, returning to `Obsi` (stable) or to another `Obsj` (instable); (ii) Eigenvalues and their COORD stand in complementary relationship, the one implying the other — `Obsi` are externally observable manifestations of introspectively accessible computations COORD; (iii) self-defining nature implies topological closure/circularity — Figures 2–3, glossed as "the snake eating its own tail: cognition computing its own cognitions" (p.265). Objectivity is then not a correspondence to an external world but mutual stipulation: "only when a subject S1 stipulates the existence of another subject S2, not unlike himself, who, in turn, stipulates the existence of still another subject, not unlike himself, who may well be S1" (p.267) — "where one snake eats the tail of the other as if it were its own" — "here is the origin of ethics" (p.268).

Appendix examples: **A1** `Op1 = "divide by two and add one"`, eigenvalue `2` reached from any `x₀` (`Op1(2)=2`, p.268); **A4** self-referential proposition `"THIS SENTENCE HAS ... LETTERS"` has eigenvalues `"thirty-one"` and `"thirty-three"`, variant `"this sentence consists of ... letters"` has one (`"thirty-nine"`), variant `"This sentence is composed of ... letters"` has none (pp.269–270) — showing discreteness and the zero-eigen case. **A3** generalises to meta-operators `Op(COORDi)=COORDi` and warns against an endless hierarchy, citing McCulloch (1945) heterarchy of values (p.269).

Limits: the text is a birthday lecture, not a detector spec; `COORD` is an observer-chosen compression that loses Piaget's fine structure (p.263 n.1, domain choice left to observer, equality sign requires same-domain variables); recursion `si=S(mk), mk=M(sj)` is suggestive, not rigorous; ethics closure (p.267–268) is conceptual and does not name a check.

## What this plant already does

The sweep `python3 research/field-review/field_review.py next` for this cell reports `circular`, `observer`, `second-order` only in `fields.json:105-107`, unrelated research notes, `checks/check-scheduler-coverage.py` (the scheduler-coverage detector and its INVENTORY/NOT_SCHEDULED tables) and the predecessor reviews — no `Eigen-Behavior` / `COORDⁿ` / `perturbation disappears` / `complementary` / `snake eating its own tail` / `S1↔S2 mutual stipulation` / `heterarchy` vocabulary is embodied in `src/` or `docs/` outside reviews.

The plant already runs observers that watch observers. `checks/check-scheduler-coverage.py` names the observer-check runner, its systemd unit `mishe-self-development-current-observer-checks.service` and CI jobs, and declares `SELF_NAME = "check-scheduler-coverage.py"` (l.123) with "running it inside itself would be recursion" (ll.121–122). `checks/observer-verdict-agreement.py` compares two computations of the same reading field-by-field (`VERDICT_FIELDS` ten fields) and prints `observer : AGREE/DISAGREE`. `src/mishe_tauftauf/wall_review.py:60-62` validates a reviewer answer with `validate_response(request, result)` — `version==1` and `input_hash==request['input_hash']` — raising `ValueError('review protocol/input hash mismatch')` otherwise (l.62). `src/mishe_tauftauf/seed_doctrine.md` already holds "Missing, failed or stale evidence remains UNKNOWN or RED" and the per-verdict detector/resolver/interval requirement, so `unknown` is a legitimate verdict, not a missing observation. `docs/mesh.md:9-21` already warns that mesh is hypothesis not description and couplings are checked, not declared.

What is missing is the name for what those verdicts are. The code treats `AGREE/DISAGREE/unknown` and the hash equality as reports about an external object (the repository), not as tokens complementary to the coordinating operation that produced them, and it does not name discreteness, stability/instability, or the zero-eigen case.

## What is new here

The new piece from this foundational source is the eigen reading that corrects the misread "object = external fact the observer discovers" → "object = token for a stable behavior complementary to the observer's COORD" (p.262–265).

For this plant, the transfer is threefold and none of it is in the tree:

1. **Complementarity, not correspondence.** `AGREE/DISAGREE` in `observer-verdict-agreement.py` and `input_hash` equality in `wall_review.py:60-62` are Eigen-Values complementary to their COORD (the comparison operator and the review coordination respectively). They are discrete — perturbation disappears — so a one-bit hash difference does not degrade gracefully but jumps to `unknown`/DISAGREE, and stability is relative to the chosen domain (p.263 n.1). Reading hash equality as correspondence ("the reviewer saw the same input, therefore the review is true") is the misread; the eigen reading says the hash is a token that the coordination held within its stable bounds, nothing more.

2. **Closure as design, not recursion avoidance.** `checks/check-scheduler-coverage.py:121-122` already notes self-inclusion would be recursion; von Foerster's closure (p.265–266, Figures 2–3) names it as circularity constitutive of observation: "lim COORD⁽ⁿ⁾ ∈ COORD" — the snake eating its own tail. The scheduler detector's self-exclusion is therefore not a quirk but the second-order closure already made observable by the recent Zönnchen et al. 2025 re-entry/blind-spot review (2026-11-02) — this review adds the complementary eigen mechanism behind that blind spot.

3. **Zero eigen is legitimate.** Appendix **A4** shows some coordinations have no eigenvalue — `unknown` stays `unknown`. This matches the plant's doctrine that missing/stale stays UNKNOWN rather than being forced to AGREE/DISAGREE, and warns against invoking a meta-meta operator hierarchy to escape it (p.269, McCulloch 1945 heterarchy). The misread would be to add a higher checker to guarantee a stable answer; von Foerster says that does not create anything new — the circle closes on itself (already cited in the predecessor misread discard via Ethics 1991).

Dissent/critique within the literature is part of the reading: the Glanville critique (second-order not refutable, has achieved little) and the charge that eigen-talk is metaphorical without a fixed domain. Von Foerster concedes the domain must be chosen by the observer (p.263 n.1), so the eigen vocabulary is only as precise as the COORD it complements — here `VERDICT_FIELDS` and the `input_hash` scheme supply that domain.

## Application

target: src/mishe_tauftauf/wall_review.py

Proposal to genome (owner of the wall/review gate and the `checks/` family):

1. On `src/mishe_tauftauf/wall_review.py` module header or `validate_response` docstring (ll.60–62, `input_hash` equality check), add one paragraph citing von Foerster 1976/2003 Chap.11 pp.261–265 (Piaget `Obs./Coord` → `obs₁=COORD(obs₀)` → `COORD⁽ⁿ⁾` → `obs•=COORD(obs•)` Eqs.7.1–7.2, Eigen-Values Eq.8, discreteness/perturbation ±ε → stable/instable p.265, complementary relationship and closure Figures 2–3 p.265–266 `lim COORD⁽ⁿ⁾ ∈ COORD`) and p.267 `S1↔S2` mutual stipulation plus Appendix A1 (`Op1` eigenvalue `2`) and A4 (`"THIS SENTENCE HAS ... LETTERS"` eigenvalues `thirty-one`/`thirty-three`, zero-eigen variant). Name `input_hash` equality and the `AGREE/DISAGREE/unknown` triad as eigen-tokens complementary to their COORD (the review coordination / the agreement comparison), noting that hash equality is discrete stability within the chosen domain, not correspondence proof, that a small perturbation jumps discretely to `unknown`/DISAGREE, and that zero eigen → `unknown` is legitimate (`UNKNOWN stays UNKNOWN`). Keep all logic, exit codes and hashes unchanged.

2. Keep docstring-only, no logic or threshold change. The line is prose derived at print time; no new field is added to `VERDICT_FIELDS` and no hash algorithm changes. Cross-reference the companion Zönnchen et al. 2025 re-entry/blind-spot paragraph on `checks/check-scheduler-coverage.py` (2026-11-02) rather than duplicating it here; and note McCulloch (1945) heterarchy warning (p.269) against a meta-meta reviewer hierarchy.

3. Detector: the check's own output and `wall_review.py` help text on each scheduled run; `tools/check_parsing.py` still passes (legacy budget unchanged). Resolver: genome. Detection interval: the scheduled wall/review gate and observer-verdict runs. Reopen if genome finds the vocabulary duplicates the existing Pask disclosure in `checks/observer-verdict-agreement.py` or belongs in `docs/mesh.md` instead of the wall-review header, or if a later source revises the eigen criterion (e.g., requires explicit domain enumeration per p.263 n.1).

