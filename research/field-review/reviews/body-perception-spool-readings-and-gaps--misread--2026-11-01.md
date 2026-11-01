---
field: body-perception-spool-readings-and-gaps
angle: misread
date: 2026-11-01
status: discarded
owner: senses
---

# body-perception-spool-readings-and-gaps — misread (2026-11-01)

## What we read

- Rubin, "Inference and Missing Data", Biometrika 63(3):581–592, DOI 10.1093/biomet/63.3.581 (the source of the MCAR/MAR/NMAR taxonomy). Source: https://doi.org/10.1093/biomet/63.3.581. Not read: the OUP page shows only the abstract behind a paywall, so no definition is quoted from it here.
- Mealli and Rubin, "Clarifying missing at random and other assumptions", Biometrika 102(4):995–1000, DOI 10.1093/biomet/asv035. Source: https://doi.org/10.1093/biomet/asv035. Not read: the only copy found is closed-access; no text was quoted from it.
- Read: the Section 2 "Notation and definitions" sentences of arXiv:1710.06891 (a later paper on diagnosing missing always at random), via the HTML rendering https://ar5iv.labs.arxiv.org/html/1710.06891. The defining equations were stripped by the renderer, so only the lead-in sentences ("missing at random (MAR) if", "missing always at random (MAAR) if") are read, not the conditions themselves.
- Read in this plant: `src/mishe_tauftauf/body_perception.py` `_gap_records` (around line 701) and `read_node_spool_summary` (around line 1468), and `research/body/personal-multibody.py` around lines 461–481, where the app's own "spool overflow dropped=N policy=oldest" record is counted.

## What this plant already does

- The plant never claims a missingness mechanism. `_gap_records` returns `None` for the whole file when any class-C line is malformed, so the caller reports UNKNOWN rather than a count a reader could take for the whole history.
- `read_node_spool_summary` separates `spool-summary-absent` (no summary published) from a failed read, and reports age from the newest row rather than inferring the missing span.
- The app's own overflow record is counted as `spool_overflow_rows` with its `dropped=N` total and published as a limit, not turned into an estimate of the dropped rows (`research/body/README.md` around lines 197–212).

## What is new here

The misread this angle asks about would be treating the dropped-row count as a basis for ignoring the gap: that is, treating "rows lost to a bounded oldest-first spool" as if the loss were unrelated to the readings themselves. The plant already does not do that. It publishes the count and the limit and does not compute a correction, so no inference step exists for an assumption to attach to. Rubin's taxonomy would classify a load-driven oldest-first drop as not missing completely at random, and the plant's UNKNOWN shape is already the honest response to a mechanism it cannot characterise. The Mealli–Rubin point about what "at random" means (a property of the mechanism, conditional on observed data) only matters if a reader is asked to ignore missingness; this plant is not.

## Discard

The plant already declines to ignore spool and delivery gaps; the missingness reading would change no code, because the only sites that could misread a gap are the ones that already return UNKNOWN. Re-open trigger: a senses or dashboard change that computes a rate, average or trend over delivered rows and so implicitly assumes ignorable loss.
