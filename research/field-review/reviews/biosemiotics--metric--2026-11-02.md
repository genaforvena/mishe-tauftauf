---
field: biosemiotics
angle: metric
date: 2026-11-02
status: applied
owner: docs
target: docs/mesh.md
---

# Just enough information to locate a sign: Schneider's R_sequence versus R_frequency and what "just enough" would mean for this plant's couplings

## What we read

- **Henrik Nielsen, Mariana Vitti-Rodrigues & Claus Emmeche — "Measuring Meaning of Molecular Motifs", *Biosemiotics* 19:205–222 (2026), https://doi.org/10.1007/s12304-026-09637-1**, open-access research article, Received 2025-11-30 / Accepted 2026-02-04 / Published online 2026-02-24 (Springer Nature 2026).
  - Publisher landing <https://link.springer.com/article/10.1007/s12304-026-09637-1> fetched 2026-11-02 (meta citation_volume 19, issue 2, pages 205–222, DOI 10.1007/s12304-026-09637-1); PDF via <https://link.springer.com/content/pdf/10.1007/s12304-026-09637-1.pdf> fetched 2026-11-02 to `/tmp/nielsen2026.pdf`, 1_202_682 bytes, sha256 `b0309b93bafe751cd390f0aaa01c28d1704754f15d6412495cb9ade5da736cb4`, 18 pages, `pdftotext` 867 lines retained; Abstract p.205, Introduction pp.206–208, "What is a Molecular Motif?" pp.208–210, "What Does Such a Motif Mean?" pp.210–212, "Information of a Motif" pp.211–213, "Information of an Individual Sequence" p.214, "Caveats" pp.215–217, Conclusion pp.217–218 and References read in full. Source URL present as required.
  - Core metric (pp.211–213): per-position information `R_sequence(l) = H_before − H_after = 2 − (−∑ log2 p_n)` bits per position (for N=4 bases, `H_before=2` bits, `H_after` from observed probabilities at position l); total `R_sequence = ∑_l R_sequence(l)` bits per site. Search complexity `R_frequency = log2 G − log2 γ = −log2(γ/G)` bits per site for G=genome size, γ=occurrences. Displayed as sequence logos (`Schneider & Stephens 1990; Shaner et al. 1993`, Fig.3 p.212). Individual sequence score `R_i = ∑_l w_{l,n}` with `w_{l,n}=log2(p_{l,n,obs}/p_{n,bcg})` (p.214).
  - Empirical regularity (p.213): in prokaryotic genomes `R_sequence = R_frequency` is often observed — "the information of the site is just enough to locate it" (`Schneider 2000; Schneider et al. 1986`). If `R_sequence << R_frequency`, sites too weak → non-specific binding throughout genome (false positives, toxic interference); if `R_sequence >> R_frequency`, unnecessarily specific (waste). Interpreted as evolutionary/energetic economy (p.213–214, plus Al Masri et al. 2023 on 3D+1D search, and Schneider's Theory of Molecular Machines I/II 1991a,b on minimum energy dissipation = Shannon channel capacity).
  - Reconciliation claim (Abstract + pp.206–208, 214–217): contra Hoffmeyer (2008:57–68) who dismissed Shannon information as "too impoverished"/"futile metaphor" independent of context, Pattee (2013) + Schneider show syntactic Shannon information is a prerequisite for semantic information ("all semantic information must conform to the logical limitations of syntactic communication", p.207) and that Schneider's quantitative `R` is compatible with biosemiotics and Bateson's "a difference that makes a difference" (pp.214–215: "information we find in the motif consists of the differences (from the random background) that make a difference"). Also addresses Shannon (1948:379) misreading and Brier (2014) on Wiener, and notes independence assumption is empirically often approximate (correlations weak, Adami 2024:163) and caveats for eukaryotes/chromatin (p.214).
  - Limits kept: prokaryote equality does not hold in eukaryotes (p.214 n.2); consensus sequence never occurs in data (like a population mean, Fig.2 p.209); normal distribution of `R_i` with consensus at extreme right tail (Fig.4 p.215).

## What this plant already does

- `docs/mesh.md` 68 lines already publishes one measurement stance: "Concentration, if you measure it, is bound to a stated window ... entropy, Gini, Nakamoto/τ or HHI" and "the plant publishes no single concentration number" (ll.34–44), citing Ovezik et al. 2025 on pre-processing sensitivity. That is the only quantitative metric the introduction defends for its own couplings; it explicitly leaves other summaries named but open.
- The field-review lane's own ALREADY EMBODIED sweep for this REVISIT cell (`python3 research/field-review/field_review.py next` this turn) reports `sign/interpretant/semiosis` hits only via `fields.json` declaration and predecessor biosemiotics reviews, not via any `R_sequence`/`R_frequency`/`sequence logo`/`Schneider` vocabulary — confirmed by `rg -n "R_sequence|R_frequency|sequence logo|Schneider" docs/mesh.md src/mishe_tauftauf --no-heading` = 0 (outside this review). The prior `biosemiotics--metric--2026-10-31.md` (Maran/Kull ecological codes) was discarded exactly because that source named a countable object ("regular irreducible correspondences") but supplied no operational metric and the plant held no sign-relation data to count.
- `src/mishe_tauftauf/wall.py` (`_publish_outcome`) and `src/mishe_tauftauf/initiative.py` bind outcome records to evidence sha256 and refuse re-binding, but they check a binding; they do not measure how specific that binding pattern is relative to the space it must be found in, and they do not display where specificity lies position-by-position.

## What is new here

Nielsen et al. give a concrete, historically grounded metric that bridges the lane's recurring tension: Hoffmeyer's dismissal of Shannon (information too impoverished without interpreting system) versus a usable quantitative measure that *is* biosemiotically legible because "R rather than H should be termed information" (p.216, citing Schneider's "Information Is Not Entropy!" page) — a decrease in uncertainty at a receiver with respect to a background.

For this plant the transfer is analogical but implementable: any recurring record that must be located in a search space has both a pattern specificity and a search complexity.

- **Definition to import:** `R_sequence(l)` per position + summed `R_sequence` (logo height) versus `R_frequency` (log search-space size minus log occurrences). The plant already has both ingredients: a tape/genome `G` (e.g., wall lines, chat events, evidence files in a window) and motif occurrences `γ` (how often a particular outcome kind, wall prefix, or evidence pattern recurs), with a calculable background distribution.
- **Economy test the source makes observable:** `R_sequence ≈ R_frequency` means "just enough" — nature is as specific as it needs to be, no more (p.213). Deviations have named readings: too little → non-specific binding / false positives (reviewer would match any path; outcome would bind any evidence); too much → wasted specificity (over-prescribed evidence path that could be simpler). The source's prokaryote/eukaryote caveat maps directly: a small, flat search space (analogous to prokaryote) can sustain the equality; a chromatin-like structured space (eukaryote) cannot, and the plant's own window/clustering choices are that structure.
- **Compatibility claim to keep:** Bateson's "difference that makes a difference" is not an alternative to Shannon but an operationalization of it: the differences that survive filtering and are conserved are those that make a difference to the organism (pp.214–215), which is exactly how sequence logos filter noise by selection.

This does not contradict `docs/mesh.md`'s current stance (no single concentration number, measures bound to window/cutoff); it adds a second, motif-level quantitative handle the introduction has no vocabulary for, and it corrects any reading that quantitative information theory is inherently anti-semiotic.

## Application

target: docs/mesh.md

Propose one bounded addition to the introduction, keeping its existing 68-line concentration paragraph unchanged, to name the complementary motif-level measure and its economy reading alongside the existing concentration measures. The addition should be conditional on the same window discipline the paragraph already requires, and must state the limits the source states:

- Define the two quantities in plant terms: for a chosen alphabet and a stated window, `R_sequence` (pattern specificity from observed versus background frequencies, summed over positions; displayable as a logo) and `R_frequency = log2 G − log2 γ` (search complexity from space size G and occurrences γ) — with citation `Nielsen et al. 2026, Biosemiotics 19:205–222, doi:10.1007/s12304-026-09637-1` and the Schneider sources it builds on (`Schneider et al. 1986; Schneider & Stephens 1990`).
- State the observed regularity and its reading: in the prokaryotic case often `R_sequence ≈ R_frequency` — just enough to locate the sites (p.213); too little → non-specific binding / false positives, too much → unnecessary specificity — as an interpretable economy/parsimony principle, not a universal law (with the prokaryote/eukaryote and independence caveats, pp.213–214).
- State the compatibility this adds to the introduction's vocabulary: Bateson's "a difference that makes a difference" operationalized as the filtered differences that survive selection, compatible with Shannon when information is understood as `R = H_before − H_after` (decrease in uncertainty at the receiver) rather than entropy itself (pp.214–216), contra Hoffmeyer (2008)'s dismissal of Shannon as too impoverished — without importing Hoffmeyer's, Pattee's or Bateson's broader frameworks.
- Keep the introduction's current commitment that "the plant publishes no single concentration number" and that window/clustering/threshold *are* the measurement; this motif measure is likewise bound to a stated window, alphabet and background, not a new single number.

No code change is proposed by this review; the future implementable follow-on (outside this prose change) would be a one-off notebook computing `R_sequence` and `R_frequency` for one concrete motif in this repository (e.g., wall-outcome bindings or a recurring tape prefix) over a stated window, to test whether that motif is just enough, too vague, or over-specified — which is the metric's experiment in this plant's terms.

