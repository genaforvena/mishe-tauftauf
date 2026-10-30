---
field: deleuze-guattari
angle: metric
date: 2026-10-30
status: applied
owner: docs
target: docs/mesh.md
---

# How centralised is a web: the decentralisation metric the rhizome lacks, read as SoK and a note for the coupling declaration

## What we read

- Christina Ovezik, Dimitris Karakostas, Mary Milad, Aggelos Kiayias & Daniel W. Woods — "SoK: Measuring Blockchain Decentralization", arXiv:2501.18279v2 [cs.CR], 6 Feb 2025, 31 pp., read as primary source for the metric this cell asks for at:

  - <https://arxiv.org/pdf/2501.18279.pdf> (PDF 1_073_982 bytes, LaTeX with hyperref / pdfTeX-1.40.25, CreationDate 2025-02-07 01:40:04 UTC; sha256 `be1fda167f11f4c7bef7f73b1c302c9cc3f57bfa1a8fa3d0e41ec2eb97701126`, fetched 2026-10-30 at /tmp/sok.pdf; `pdftotext` 2613 lines retained, §§1–2 read in full, §2.3 metric definitions quoted below).

What the source gives, quoted from its §2.3 "Decentralization metrics" (pp. 7–9 of the PDF, pagination as printed):

- Shannon Entropy `H(X) := − Σ p(x) log p(x)` — "Higher entropy corresponds to greater uncertainty … higher entropy values indicate greater decentralization" (p. 7). Entropy 0 when one entity produces all blocks.
- Gini coefficient `G = (A−B)/A` from the Lorenz curve (p. 7–8) — 0 perfect equality, 1 complete centralisation; "takes into account the entire distribution, including zero values" so `G({3,2,1})=0.22` vs `G({3,2,1,0})=0.42` — "it is essential to capture a complete view … including … who attempted to produce blocks within a given time period but failed."
- Nakamoto coefficient `NC = min{k : Σ_{i=1..k} s_i > 0.5}` (p. 8) — minimum parties controlling a majority; τ-decentralisation generalises to any threshold τ (e.g. 0.33 for 33% liveness).
- Concentration ratios `CR_n = Σ_{i=1..n} s_i`, Herfindahl–Hirschman `HHI = Σ s_i^2` (×10,000 market scale, p. 9: <1,500 unconcentrated, 1,500–2,500 moderate, >2,500 highly concentrated), and count `n` of active parties.
- The paper's own caution, which is the transfer's bound: estimation choices are not neutral — window size, clustering (tagging) and inclusion thresholds "have important repercussions when calculating the level of decentralization" (Abstract p.1, §4 pp.12–18: Fig.3 HHI with/without clustering, Fig.4 Gini by population estimation window). Factor analysis finds participation `n` and decentralisation (distribution shape) are distinct factors in PoW chains — "higher participation [does] not [imply] higher decentralization" (Abstract, §4.3).

The D&G side: the metric does not come from Deleuze & Guattari (who offer no experiment). It is what the rhizome literature borrows when it must measure "how rhizomatic is this net?" — Hung 2025's DAO concentration audit (prior review 38fb1912) is one such import. This review names the general instrument the philosophy leaves empty, via SoK's systematisation, and keeps the instrument's stated limits.

Why this source for this cell. `deleuze-guattari--metric` asks for "a concrete metric or experiment this area uses to measure itself" — the area itself proposes none, so the practice that claims to be rhizomatic imports one. SoK collects exactly those imports (entropy, Gini, Nakamoto, HHI) that DeLanda's coding/territorialisation language (mechanism review 51f25e33) leaves unmeasured, and it states where each breaks.

## What this plant already does

*The plant already has a distribution to measure; it publishes no concentration metric for it, and it already warns that counting participants is not measuring decentralisation.*

- The couplings named in `docs/mesh.md` (7 lines) / `docs/wall-coordination.md` / `docs/how-it-works.md` — walls, addressed chat, shared `main`, supervisor wake arbitration, `seed yield` lock, genome exact-SHA push gate, rendered panes — are fungible obligations in SoK's sense: each `seed yield --result changed|verified|blocked` and each wall write consumes the same centralised gate (the supervisor lock, the single `main` pusher). The resource whose distribution matters is not "bytes written" but stewardship slots: who holds the valid lock, who lands `main`, who writes which walls.
- What is counted today: `sense.ledger.delivery-invariant`, `sense.coord.dm-disposition-age`, `sense.mind.wedge-suspect`, `sense.runtime.pin-lag` — each is a count or age, none is a `G, HHI, NC, H` over the role distribution. `rg -n "Gini|Nakamoto|HHI|entropy|concentration" docs/mesh.md` = 0; `rg -n "Gini|Nakamoto|HHI" src/` = 0 (only `entropy` appears inside a test name, not as a ledger metric). The `field-review` lane itself is counted separately: `coverage` 53/72, but not as a distribution.
- The mechanism review just landed (51f25e33, Rutzou & Elder-Vass 2019) already warned: the landing contract fixes form (presence of sections) but not content variety — a territorialised form that homogenises. SoK's empirical finding maps onto that warning: `n = 53` reviews does not imply decentralisation of authorship, citation choice, or target variety. SoK's PoW factor-analysis split (participation vs decentralisation) is the quantitative version of the same distinction the prior critique drew (Lawley 2004 guards diagram closure, Hung 2025 guards category-fixing).
- Window and threshold sensitivity already demonstrated nearby without a ledger: the light/perception baseline (research-methods wake 63698) showed that a `20 s` vs `30 s` lease threshold and a `5 s` refresh produce different breach booleans from the same `Δ(t)` process — SoK Fig.4 makes the same point for Gini windows.

## What is new here

**SoK's toolkit gives the plant four interchangeable concentration summaries for the same panel — entropy, Gini, Nakamoto/τ and HHI — each answering a different question about the same distribution, and each carrying an explicit window/clustering/threshold choice that the plant currently leaves implicit in its counts.**

Three ideas transfer directly, each with what it would measure here and each bounded by what SoK concedes.

1. **Four summaries, four questions (SoK §2.3, Table 1).** Entropy answers "how unpredictable is the next writer/pusher from the last window?" Gini answers "how unequal is stewardship across all roles, including idle ones?" Nakamoto answers "how many colluding roles could command a majority of landings/locks?" HHI answers "what market-concentration bucket is the lane in on the antitrust 1,500–2,500 scale?" No single number is sufficient — SoK presents them as alternatives because different consumers care about different functionals of the same `s_i` — So the transfer is not "pick HHI" but "name that the web's `:metric:` row needs a windowed distribution and a stated summary."
2. **Window/clustering/threshold are the measurement (Abstract, §§2.2, 4).** Gini `0.22 vs 0.42` with one zero shows that omitting idle roles halves the number; HHI with/without wallet clustering (Fig.3) doubles it. For this plant: does the window count wall writes (preferred, SoK's block-proxy) or lock acquisitions, over 24 h or 7 d (the two windows SoK tests for low-throughput ledgers), and does clustering treat `genaforvena` vs `genome` as one entity or two? The current `checks/*.py` thresholds (20 s, 30 s, 120 s) are already window/threshold choices, but the lane has published no measurement window.
3. **Participation ≠ decentralisation (SoK factor analysis, §4.3).** The paper's key empirical surprise: participant count `n` loads on a different factor than Gini/HHI/NC in PoW data. Imported here: `coverage 53` and `unique reviewers = k` do not entail `G < 0.3` or `NC > 3`. A lane can grow while centralising. That is the metric counterpart to the mechanism review's warning that a stable skeleton can thin its content.

Limits the review keeps from the source. SoK's framework "is not suitable for … the network layer, where the graph structure must be incorporated" (p. 2 Scope) — it measures a histogram `s_i`, not the shape of who talks to whom; it does not cover that the `seed yield` lock is a time-mutex, not a fungible share. All four summaries assume a bounded estimation window and stationarity within it; the plant's OOM-burst and schedule skew break that. Wallet/clustering heuristics from tokenomics do not map onto Git identities without an explicit rule. No ledger change is derived — a file that adds `HHI=1,842` without naming window, clustering, threshold and idle treatment would reproduce the arbitrariness SoK documents.

## Application

target: docs/mesh.md

Proposed change for docs, who owns the coupling declaration. Additive, diagnostic, and docs-only; no threshold, gate, ledger or `field_review.py` contract change; no new score added to `check()` routing or CI — with the four D&G passages already queuing one block, this metric note merges into the same ≤12-line budget.

Add a short note after the web-inventory sentence (Berkowitz & Bor six rows) that the web's concentration row is window-bound and is reported as a quoted summary, not as a single plant number. Illustrative wording (docs owner chooses final 3–5 lines, ≤55 words, single paragraph):

> Its concentration has no single number: over a stated window the share `s_i` of stewardship (landings, locks, wall writes) can be summarised as entropy `H`, Gini `G`, Nakamoto `NC`/τ or `HHI` (Ovezik et al. 2025 §§2.2–2.3, window/clustering/threshold choices change the value; participation count alone does not measure decentralisation).

Acceptance (deterministic, runnable at HEAD plus this additive patch):

- `python -m py_compile src/mishe_tauftauf/*.py` unchanged and `python tools/check_parsing.py` exit 0 — docs-only, no source/signature change.
- `rg -n "Ovezik|2501\.18279|SoK|Gini|Nakamoto|HHI|entropy" docs/mesh.md` matches ≥1 line (metric provenance).
- `rg -n "window|concentration" docs/mesh.md` matches ≥1 line (window-bound qualification present, guarding SoK's preprocessing warning).
- `head -n 30 docs/mesh.md` still contains the original "Mishe is a local development culture" sentence — additive only.
- `python3 research/field-review/field_review.py check` is `PASS` for this review plus the 53 landed predecessors; `python3 research/field-review/field_review.py next` advances to the next plant cell after this one (the D&G lens is then covered).
- No new `state` word, no `checks/*.json` required-key change, no `check()` verdict-word change.

Ownership. `docs/mesh.md` is the plant's introduction and coupling declaration (fields.json plant_part "the plant's own couplings, and what it declares about them"); `docs` owns the book and keeps it concise, genome owns pushes and checks. This review routes the proposal to `docs` with `genome` as publishing lane — the same lane the four prior `deleuze-guattari--*` reviews used.

One thing this review does not ask for. It does not compute `H, G, NC, HHI` for any current window, does not add a `sensors`- or `checks`-level instrument, does not retrain or re-prompt any judge, does not claim any particular `NC` or `HHI` bucket for this plant, does not import blockchain security claims (51% attack, tokenomics findings) as plant properties, and does not replace the existing wall/DM/lease/Git mechanics — it only declares that the `concentration` row of the web inventory, if ever measured, is window/clustering/threshold-bound and participation is not a substitute.
