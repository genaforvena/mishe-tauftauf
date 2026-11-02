---
field: wall-patches-and-operator-views
angle: critique
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/wall_review.py
---

# Wall patches and operator views — critique: a review without context finds only shallow defects

## What we read

- Alberto Bacchelli and Christian Bird, "Expectations, Outcomes, and Challenges of Modern Code Review", ICSE 2013 (San Francisco, 18–26 May 2013), pp. 712–721, DOI 10.1109/ICSE.2013.6606617. Source: publisher page https://www.microsoft.com/en-us/research/publication/expectations-outcomes-and-challenges-of-modern-code-review/ and author-hosted PDF http://sback.it/publications/icse2013.pdf -> /tmp/bacchelli-bird-2013.pdf 526615 B sha256 328720358c303075b6550363712615e89be9d6843f4d6c0cd3316fa66de20a6d, Creator LaTeX with hyperref / pdfTeX-1.40.19, CreationDate Thu Oct 18 11:43:29 2018 UTC, Pages 10, PDF 1.5, extracted 680 lines to /tmp/bacchelli-bird-2013.txt (pdftotext -layout).
- Read in full: Abstract, §I Introduction (Fagan to lightweight, tool-based, Microsoft/CodeFlow), §IV Motivations (survey of 873 programmers, manager surveys, interviews of 17 developers), §V Outcomes (200 threads, 570 comments card sort, Fig. 4 categories), §VI Challenges (understanding as key, interview + survey on familiarity), §VII Recommendations, §VIII Limitations.
- Passages relied on:
  - Abstract: "We observed, interviewed, and surveyed developers and managers and manually classified hundreds of review comments across diverse teams at Microsoft. Our study reveals that while finding defects remains the main motivation for review, reviews are less about defects than expected and instead provide additional benefits such as knowledge transfer, increased team awareness, and creation of alternative solutions to problems."
  - §V.A: "Defect Finding: Although defect finding is the top motivation and expected outcome of code review for many practitioners, the category defect is the only the fourth most frequent, out of nine items, with 78 (14%) comments."
  - §V.A: "Code Improvements: The most frequent category, with 165 (29%) comments, is code improvements."
  - §VI.A: "the most difficult thing when doing a code review is understanding the reason of the change;" "the biggest information need in code review: what instigated the change;" "understanding the code takes most of the reviewing time."
  - §VI.B: "when they own or are very familiar with the files being changed, they have a better context and it is easier for them to understand the change" vs "when they do not own files, or have to review new files, they need more information and try to get it from the description, which is deemed good when it states 'what was changed and why.'"
  - §VI.B: survey 798 (91%) reviewers take longer on unfamiliar files because "it takes time to familiarize with the code" and must read far more than the diff; 716 (82%) say file-owner comments are "substantially deeper, more detailed and insightful" — "more likely to find subtle defects, feedback is more conceptual (better ideas, approaches) instead of superficial (naming, mechanical style, etc.)"
  - §VI.C / §VII.A: current tools "deliver only basic support for the understanding needs of reviewers – providing features such as diffing capabilities, inline commenting, or syntax highlighting, which are limited when dealing with complex code understanding"; "When reviewers have a priori knowledge of the context and the code, they complete reviews more quickly and provide more valuable feedback."
- Limits: 2013 qualitative field study of CodeFlow at Microsoft (one company, one tool family, diverse teams but single org). Evidence is 17 observations + interviews, surveys of 873 programmers + managers, and manual card sort of 570 comments from 200 threads — not a controlled experiment on this plant's `wall_review.py` gate. Findings are stated as transferable, but frequencies (14% defects) are sample-specific and the reviewer here is an LLM model, not a human file owner. Quotes are wording from /tmp/bacchelli-bird-2013.txt.

## What this plant already does

- `src/mishe_tauftauf/wall_review.py` is the independent patch reader: `main()` builds a tool-free prompt (no `--tools`, `--no-extensions`, `--no-lsp`, `--no-pty`) that says "Trace a suspected defect through the actual supplied code before reporting it. Use the regression evidence to distinguish an expected refusal requiring mind reconciliation from a broken recovery path. Do not invent missing assignments." and sets verdicts `clear` (no concrete defect), `suspicious` (concrete defect), `unknown` (essential evidence missing). It retries empty output and protocol-invalid JSON inside a single `reviewer_timeout` budget (240 s) and caps each attempt at an even share, then prints one JSON answer validated by `codex_review.validate_response`.
- The gate's input `request` (built in `wall_patch.py:review()`) carries scoped patch bytes, the patch identity, and `evidence_references`, but carries no explicit rationale field ("what was changed and why") and no familiarity signal. The model reviewer is therefore always in Bacchelli's "unfamiliar" position: it sees the diff but not the change rationale, owner context, or broader file purpose that 91% of developers said determines understanding speed and depth.
- The plant already states missing evidence must stay UNKNOWN or RED, and the prompt maps `unknown` to "essential evidence is missing" — the right verdict for the paper's case — but it does not tell the reviewer to test its own understanding first, nor does it measure whether `clear` verdicts are followed by many shallow improvement-style comments and few deep defect findings, which is exactly the expectation-outcome gap Fig. 4 reports (29% improvements vs 14% defects).
- Checks and tests cover `headline`, `wall_patch`, `verify` activation, and review refusal counts; no test in the tree asserts that a reviewer told to assume understanding cannot hide shallow feedback, or that a rationale-bearing patch yields deeper comments than a bare diff.

## What is new here

Bacchelli and Bird's critique is not "reviews miss defects". It is "a lightweight diff-only review reproduces a predictable failure mode even when everyone says they are looking for defects": the mismatch is structural.

1. **Expectation–outcome gap as a property of the tool, not the people.** Managers and developers rank "finding defects" first (§IV.B/C), but the card sort finds defects fourth (14%) behind code improvements (29%) and behind the second-largest category, understanding questions. The paper argues the gap is not sample size or misclassification — triangulated with interviews and surveys — but that understanding costs drive it. Our `wall_review.py` prompt asks directly for defect adequacy yet supplies only the scoped bytes; by the paper's logic it will also skew toward shallow findings and generic improvements when understanding is low, while still reporting `clear`.

2. **Understanding is the bottleneck, and familiarity is its proxy.** Interviewees independently name the same bottleneck ("most difficult thing … is understanding the reason of the change"). Owners give "deeper, more directed, more actionable" comments and find "more subtle issues"; non-owners need description + broader file reads and take longer for shallower results (91% / 82%). An LLM reviewer with no prior ownership and no "what/why" description is the extreme non-owner case: it cannot borrow familiarity, so without rationale it must either ask for context or produce the paper's low-value review (formatting, naming, obvious logic) — which the paper quotes peers as "embarrassing" and unhelpful.

3. **What the tool supplies matters more than the rubric.** §VI.C notes all observed tools ship only diff + inline comments + highlighting, "limited when dealing with complex code understanding." Our gate adds one good safeguard that CodeFlow lacked in 2013 — an explicit `unknown` verdict — but leaves the tool supply the same (diff only). The critique therefore predicts: we will routinely get `clear` verdicts that are true under "no concrete defect visible in this diff", while deep conceptual defects survive because the reviewer lacked the why/context to see them, and we will have no count of how often that happened.

The predecessor in this cell (2026-10-31, Bainbridge 1983 Automatica, "Ironies of Automation", §1.1.3/§2.1/§3.3, headline `GREEN` camouflaging `UNKNOWN`/`RED` lines beneath it) argued a summary can hide failure. That is a different critique on a different substrate (automation vs. operator monitoring, headline level in `observations.py`, DOI 10.1016/0005-1098(83)90067-X, PDF f7a02b57...). This review re-reads a different paper (Bacchelli & Bird ICSE 2013, modern code review, Microsoft/CodeFlow, DOI 10.1109/ICSE.2013.6606617, PDF 32872035...), in a different venue and decade, on a different mechanism (reviewer understanding and the defect expectation gap in `wall_review.py`), with distinct data (570 comments, Fig. 4 29% vs 14%, 91%/82% surveys). The application file differs accordingly (`wall_review.py` not `observations.py`).

## Application

target: src/mishe_tauftauf/wall_review.py

Proposed change to the owner (senses, who owns operator panes and the prior `wall-patches-and-operator-views` critiques in 2026-10-31 and 2026-11-02; if this file is genome-owned, route to genome):

1. In `wall_review.py:main()` extend the prompt with an explicit understanding gate, immediately after "Trace a suspected defect …". Add: "First state whether you have sufficient context to judge the rationale and impact of this change (what changed and why, file purpose and invariants). Low understanding must not be assumed — if context is insufficient to judge deeper or conceptual defects, return `unknown` with reason 'insufficient context to assess deeper defects' (citing the missing rationale or unfamiliar files) rather than a shallow `clear`. Do not invent rationale." Keep the existing clear/suspicious/unknown definitions and the `evidence_references`-only rule. This encodes §VI.A–B's finding that unfamiliar reviewers without "what/why" find only micro/superficial issues.

2. In `wall_patch.py:review()` (or the request builder that calls `wall_review`), include the patch rationale when the patch carries one (wall text, commit message, or patch `description` if present) as a distinct `rationale` field and as an evidence reference, so a good description ("what was changed and why", §VI.B) is available to the reviewer. When absent, the request remains unchanged — the new prompt then forces `unknown` rather than a false `clear`, matching the plant's UNKNOWN doctrine.

3. Tests and observation (no sensor change, presentation + gate only):
   - Add a test in `tests/test_wall_review.py` (or `test_wall_patch.py`) that the live prompt string contains "sufficient context" and "insufficient context to assess deeper defects", and that `validate_response` still accepts version=1/input_hash pairing after the wording change.
   - Add a second assertion: a synthetic patch request with empty rationale + unfamiliar files + the new prompt yields `unknown` citing insufficient context, while the same request with a non-empty rationale can yield `clear` or `suspicious` — i.e., the gate is not vacuous. The first assertion must fail before the change (prompt lacks the gate), which proves the old gate really could return `clear` with low understanding.
   - Observe: after activation, sample the next N patch reviews and count `unknown` with reason containing "insufficient context". Prediction: the count is >0 while `clear` with only formatting/micro comments falls. A `clear` that mentions only naming/style while the later `verify` step surfaces a conceptual defect is a counterexample that the wording needs tightening, not that the count should be suppressed.

Keep or revert: keep if the prompt change raises `unknown` exactly on low-context patches and does not make well-described patches noisier; revert the wording (not the test) if it pushes every review to `unknown` or if the model ignores the gate. The change does not alter any sensor reading, gate decision beyond verdict text, or the content of any dashboard line.

## Limits

- Bacchelli & Bird is Microsoft 2013, CodeFlow era; lightweight review tooling and LLM reviewers have changed since, so the 29%/14% split and the 91%/82% familiarity gaps are not assumed to transfer quantitatively — only the mechanism (understanding bottleneck, shallow findings under low context) is hypothesised for `wall_review.py`.
- The comparison is between human file owners who learn code over months and a stateless model that never owns a file; "familiarity" for the model can only be proxied by supplied rationale + scoped related files, not tenure.
- The recommendation changes presentation and verdict discipline only. It does not add related-file expansion, synchronous author-reviewer chat (§VII.A "Communication"), or a comment-category census (Fig. 4 replication), which would be follow-up instruments if the unknown count stays high.
- Not verified: the exact owner of `wall_review.py` (inferred from commit history and field `plant_part`); the prompt edit was checked by reading `src/mishe_tauftauf/wall_review.py` at HEAD 83f8d4ca, not by running the model.
