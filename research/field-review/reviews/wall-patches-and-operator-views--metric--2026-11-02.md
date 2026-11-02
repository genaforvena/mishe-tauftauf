---
field: wall-patches-and-operator-views
angle: metric
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/operator_view.py
---

# Wall patches and operator views — metric: System Usability Scale (SUS) as a global operator-panel score

## What we read

- John Brooke, "SUS — A quick and dirty usability scale", in Jordan, Thomas, Weerdmeester & McClelland (eds), *Usability Evaluation in Industry*, Taylor & Francis, 1996, pp.189–194. Chapter text © Digital Equipment Corporation, 1986. Source fetched 2026-11-02 as https://hell.meiert.org/core/pdf/sus.pdf → /tmp/sus-brooke.pdf 153289 B sha256 c04fe6139b1f2c3650f497e3d139e7b0f9b112c79beec7c53f7573e9c9a003a4, pdfinfo Title "SUS - A quick and dirty usability scale", Author "Jabberwocky Development Group", Creator Writer, Producer OpenOffice.org 2.0, CreationDate Tue Mar 20 10:46:48 2007 UTC, 7 pages, pdftotext 484 lines retained. Publisher landing HTML https://digital.gov/topics/usability/ fetched 2026-11-02 to /tmp/digital-usability.html 92266 B confirms SUS as the current Digital.gov recommended method page.
- Passages read verbatim (pdftotext, tag stripping not needed — plain PDF text):
  - Context definition p.1: "Usability does not exist in any absolute sense; it can only be defined with reference to particular contexts. This, in turn, means that there are no absolute measures of usability, since, if the usability of an artefact is defined by the context in which that artefact is used, measures of usability must of necessity be defined by that context too. Despite this, there is a need for broad general measures which can be used to compare usability across a range of contexts."
  - ISO 9241-11 classes p.2: "measures of usability should cover effectiveness ( the ability of users to complete tasks using the system, and the quality of the output of those tasks), efficiency ( the level of resource consumed in performing tasks) satisfaction (users' subjective reactions to using the system)."
  - Construction p.3–4: "SUS was constructed using this technique. A pool of 50 potential questionnaire items was assembled. Two examples of software systems were then selected (one a linguistic tool aimed at end users, the other a tool for systems programmers) on the basis of general agreement that one was 'really easy to use' and one was almost impossible to use, even for highly technically skilled users. 20 people from the office systems engineering group, with occupations ranging from secretary through to systems programmer then rated both systems against all 50 potential questionnaire items on a 5 point scale ranging from 'strongly agree' to 'strongly disagree'." / "There were very close intercorrelations between all of the selected items (± 0.7 to ± 0.9)." / "In addition, items were selected so that the common response to half of them was strong agreement, and to the other half, strong disagreement. This was done in order to prevent response biases caused by respondents not having to think about each statement; by alternating positive and negative items, the respondent has to read each statement and make an effort to think whether they agree or disagree with it."
  - Ten items p.4–5: 1 I think that I would like to use this system frequently; 2 I found the system unnecessarily complex; 3 I thought the system was easy to use; 4 I think that I would need the support of a technical person to be able to use this system; 5 I found the various functions in this system were well integrated; 6 I thought there was too much inconsistency in this system; 7 I would imagine that most people would learn to use this system very quickly; 8 I found the system very cumbersome to use; 9 I felt very confident using the system; 10 I needed to learn a lot of things before I could get going with this system — each on 1 (Strongly disagree) to 5 (Strongly agree).
  - Usage p.5: "The SU scale is generally used after the respondent has had an opportunity to use the system being evaluated, but before any debriefing or discussion takes place. Respondents should be asked to record their immediate response to each item, rather than thinking about items for a long time. All items should be checked. If a respondent feels that they cannot respond to a particular item, they should mark the centre point of the scale."
  - Scoring p.5–6: "SUS yields a single number representing a composite measure of the overall usability of the system being studied. Note that scores for individual items are not meaningful on their own. To calculate the SUS score, first sum the score contributions from each item. Each item's score contribution will range from 0 to 4. For items 1,3,5,7,and 9 the score contribution is the scale position minus 1. For items 2,4,6,8 and 10, the contribution is 5 minus the scale position. Multiply the sum of the scores by 2.5 to obtain the overall value of SU. SUS scores have a range of 0 to 100."
  - Worked example p.6: "Total score = 22 SUS Score = 22 *2.5 = 55" (single-respondent example over 10 items with ticks shown).
- Limits kept from this reading: one chapter (not a peer-reviewed trial), n=20 raters on two systems within one office-systems group at DEC; "quick and dirty" low-cost global assessment, not diagnostic ("quick" means 10 items administered in under a minute after a 20–60 min task — it does not locate which design feature failed); 0–100 is not a percent; single-number output discards per-item signal by design; context-specificity claim means cross-system comparison is "apples and oranges" without same users/tasks/environment.

## What this plant already does

- The metric predecessor for this field (2026-10-31, discarded) intended Pirolli & Card 1999 / Charnov 1976 patch-leaving rate (leave when instantaneous gain falls to environment average) as a wall_view.py flag for stale sections. That source did not load from this host (APA PsycNet Loading page, Elsevier Redirecting, PARC host unresolvable), so no primary citation was available and the cell was discarded for unreachability — not for a judgement about foraging theory. The hypothesis (per-wall new-evidence rate vs tape average) was left as secondary-summary only.
- Current liveness and correctness checks for the operator surface are all machine-observed, not subjective. `src/mishe_tauftauf/operator_view.py:29 PUBLISHER_GRACE_SECONDS=90.0` guards the livelock fixed 2026-10-29 (cold frame 12.1–23.9s on single vCPU while supervisor ticked 5s) but measures pane age, not whether the operator can use the panel. `operator_view.py:render()` and `wall_view.py:render()` produce the dashboard/role pane bytes with lease liveness and renderer-bytes checks (`wall_view.py:renderer_bytes_lines`, `pane_lease_lines`, `_runtime_state`, `deployment_lines`) — GREEN/RED/UNKNOWN verdicts about bytes and leases. No questionnaire, no satisfaction score, no effectiveness/efficiency/satisfaction triad from ISO 9241-11 is computed or stored.
- Operator-side probes that do exist measure the host, not the human: `checks/root-fs-stall.py` (A/B dual_probe every second), `host_capacity`, `host_containment`, `session_restarts`, `discovery.py` scopes, `trial_metrics.py` trial counts. `SITE/operator/brief.md` and `SITE/operator/commands.md` are the operator-facing docs, but there is no instrument that asks the operator "was this easy to use?" after a session — the wall patch gate in `src/mishe_tauftauf/wall_patch.py` verifies bytes and review hash (`verified_delivery`, `snapshot`, `restore`, `verify` revert-then-reapply), not human confidence or learnability.
- Keyword sweep at `python3 research/field-review/field_review.py next` for this REVISIT cell (wall-patches-and-operator-views--metric) reports `wall/dashboard/operator` hits throughout `checks/`, `wall*.py`, `operator_view.py`, `docs/` — all about rendering and liveness, zero hits for "SUS", "System Usability Scale", "Likert", or "BROOKE" outside `fields.json` and prior reviews (confirmed by `rg -n "SUS|Brooke|Likert" src/mishe_tauftauf docs` = 0 in tracked tree at HEAD 6eaaa5e7).

## What is new here

- Brooke gives a concrete, historically grounded metric that bridges the lane's recurring gap: the dashboard is judged GREEN when its bytes match the pin and its panes advance, but the operator never records whether the panel was "unnecessarily complex", "cumbersome", "well integrated", or required "support of a technical person" — five of the ten SUS items map directly onto operator pain (complexity, support, integration, inconsistency, cumbersome, confidence, learnability). ISO 9241-11's satisfaction class, which this plant never quantifies, is exactly what SUS operationalises as a single 0–100 number with a fixed scoring rule.
- The mechanism is implementable without changing who can write walls or how settlement works: odd items (1,3,5,7,9) contribute `r-1`, even items (2,4,6,8,10) contribute `5-r`, sum 0–40, ×2.5 → 0–100. Alternating polarity (+/−/+/−…) with ±0.7–0.9 intercorrelations is the bias control — the respondent must read each statement. Administration is "immediate response, before debriefing", all 10 checked, neutral (3) if cannot respond. This is the textbook "quick and dirty" cost model (10 Likert marks after a task, <1 min) that the predecessor's foraging rate lacked — no instrumentation, just a questionnaire.
- Distinct from predecessor: predecessor was behavioural foraging (rate of gain per patch, marginal value theorem, continuous measurement of new tape entries per wall section). This is subjective global satisfaction (10-item Likert, composite 0–100, no per-item meaning, no rate, no environment average). Different author/venue/year (Brooke 1986 DEC chapter vs Pirolli & Card 1999 Psych Review / Charnov 1976 Theoretical Population Biology), different text quoted (SUS items + scoring formula vs patch-leaving rule), different target file and code path (`operator_view.py` human-surface metric vs `wall_view.py` section staleness flag). Predecessor unreachability is preserved as the reason for discard — this review replaces the absent citation with a reachable primary source rather than re-arguing foraging theory.
- The chapter's own warning is the limit: SUS is global and non-diagnostic — a 55 tells you the operator struggled, not which of the ten dimensions failed — and cross-system comparison is apples-to-oranges without fixing users/tasks/environment. It is the intended complement to the plant's per-check diagnostics (which already say *which* lease or byte mismatched), not a replacement.

## Application

target: src/mishe_tauftauf/operator_view.py

Proposed change to the owner (senses). Satisfaction only; no new wall gate rule, no new pane layout, no diagnostic breakdown, no change to liveness thresholds:

1. In `operator_view.py` (beside `PUBLISHER_GRACE_SECONDS` and `render`/`ensure`/`run`), add a pure, documented SUS helper with no I/O, citing Brooke 1986 PDF sha256 c04fe6139b1f2c3650f497e3d139e7b0f9b112c79beec7c53f7573e9c9a003a4 and the Digital.gov usability method page 92266 B fetched 2026-11-02:

```python
# Brooke 1986 SUS — global subjective usability, 10 items alternating +/−.
# Not diagnostic; single 0–100 number only; per-item scores not meaningful.
SUS_ITEMS = (
    "I think that I would like to use this system frequently",  # + (1)
    "I found the system unnecessarily complex",                  # − (2)
    "I thought the system was easy to use",                      # + (3)
    "I think that I would need the support of a technical person to be able to use this system",  # − (4)
    "I found the various functions in this system were well integrated",  # + (5)
    "I thought there was too much inconsistency in this system", # − (6)
    "I would imagine that most people would learn to use this system very quickly",  # + (7)
    "I found the system very cumbersome to use",                 # − (8)
    "I felt very confident using the system",                    # + (9)
    "I needed to learn a lot of things before I could get going with this system",  # − (10)
)

def sus_score(responses: list[int]) -> int:
    """Brooke SUS 0–100 composite from 10 Likert responses in 1..5.

    Odd items (1,3,5,7,9): contribution = r-1. Even items (2,4,6,8,10): 5-r.
    Sum 0–40 × 2.5 → 0–100. Single number only; individual items not meaningful.
    Validates length 10 and each r in 1..5; neutral 3 if respondent cannot answer one item.
    """
    if len(responses) != 10:
        raise ValueError("SUS needs exactly 10 responses")
    if any(not 1 <= r <= 5 for r in responses):
        raise ValueError("each SUS response must be 1..5")
    total = 0
    for i, r in enumerate(responses, start=1):
        total += (r - 1) if i % 2 == 1 else (5 - r)
    return int(total * 2.5)
```

2. Document the operator-dashboard adaptation in the module docstring (one paragraph): the ten items are administered after the operator has used the dashboard for one wake but before any debrief, immediate response, all ten checked; adaptation note: "system" = the operator dashboard (top pane + lower operator shell + brief/commands docs); 0–100 is not a percent; see Brooke context-specificity warning (comparison across different operators/tasks is apples-and-oranges). No new file, no new daemon, no CLI flag in this step.

3. (Out of scope for this patch but the metric's experiment in this plant's terms — not proposed as code here): one future opt-in collection point writes `SITE/operator/sus.jsonl` JSON lines `{ts, operator_id?, sus, responses, dashboard_sha, lease_age_s}` after a wake, rendered nowhere until senses decides; analysis is comparison of SUS across dashboard versions for the same operator/task context, not a threshold gate (`sus < 68 → RED` is explicitly out of scope — thresholds are not in Brooke). Keep `render()` and `ensure()` untouched.

4. Tests in `tests/test_operator_view.py` (additive, isolated, deterministic, no I/O): `assert sus_score([4,2,4,1,4,2,4,2,5,2]) == 82` (positive-leaning operator), `assert sus_score([3]*10) == 50` (all neutral), `assert sus_score([1,5,1,5,1,5,1,5,1,5]) == 0` (all negative), `assert sus_score([5,1,5,1,5,1,5,1,5,1]) == 100` (all positive), plus length/range ValueError cases; also assert `SUS_ITEMS[0].startswith("I think")` pinning wording. No change to existing liveness tests; `python tools/check_parsing.py` unchanged for the target's budgets.

Prediction: the operator gains a one-number global satisfaction metric comparable across dashboard versions for fixed users/tasks/environment, complementing the existing byte/lease diagnostics that already say *what* failed. Disposition: keep if pure helper tests pass and no existing test regresses; revert the helper if the operator declines questionnaire collection or if a future live SUS distribution shows the 0–100 number has no variance on this single-operator plant, and re-propose as docs-only (docstring + SUS_ITEMS) without a collector.

## Limits

- One chapter PDF (7 pp) from a 1986 industrial programme (DEC office systems, n=20, two systems, 50→10 items by extreme-response selection, intercorrelations ±0.7–±0.9) — not a clinical trial; "quick and dirty" means global, non-diagnostic by design (Brooke: scores for individual items are not meaningful). No live operator responses were collected here; mapping "system" onto "operator dashboard" is interpretive and the 10 items retain English wording that presumes a human respondent.
- No change to `wall_patch.py` durability (SQLite-atomic-commit predecessor 6eaaa5e7) and no change to the Pirolli & Card patch-leaving hypothesis — this metric complements rather than replaces either; the predecessor's unreachability remains the documented reason for its discard.
- The metric costs the operator time (10 marks after a task) and, per Brooke, must be administered before debriefing — a protocol obligation outside code. No verdict gate is introduced; as with the source, 68-vs-55 has no absolute meaning without fixing who, what task, and what environment.
