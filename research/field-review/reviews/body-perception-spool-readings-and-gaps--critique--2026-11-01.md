---
field: body-perception-spool-readings-and-gaps
angle: critique
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — critique (2026-11-01)

## What we read

- Schafer, J. L. & Graham, J. W. (2002), "Missing Data: Our View of the State of the Art", *Psychological Methods* 7(2), 147–177, DOI 10.1037/1082-989X.7.2.147. Source (full text, PDF): https://educa.saludpublica.uchile.cl/espsystem/files/t/asignaturas_bibliografia/3481/archivos/Schafer%20Graham%20-%202002%20-%20Missing%20data%20our%20view%20of%20the%20state%20of%20the%20art.pdf
- Read in this turn: pp. 147–152 of the PDF (abstract, introduction, "Goals and Criteria", "Types and Patterns of Nonresponse", the MCAR/MAR/MNAR definitions with Figure 2, and "The Plausibility of MAR"). Not read: the later sections on listwise deletion and single imputation in detail, and the sections on ML and MI. Nothing below relies on those.
- The critique the paper makes of our kind of practice, in its own words:
  - p. 147: "ad hoc edits may do more harm than good, producing answers that are biased, inefficient (lacking in power), and unreliable."
  - p. 149: "mean substitution … may accurately predict missing data but distort estimated variances and correlations. A missing-value treatment cannot be properly evaluated apart from the modeling, estimation, or testing procedure in which it is embedded."
  - p. 149: "The sensitivity should be reported" when the assumptions behind a missing-data procedure are not testable.
  - p. 150: "there is no way to test whether MAR holds in a data set, except by obtaining follow-up data … or by imposing an unverifiable model."
  - p. 151: MAR means missingness may depend on observed data but not on the missing values. "Missing not at random" (MNAR) means it depends on the missing values themselves.
  - p. 152: "erroneous assumption of MAR … may often have only a minor impact"; the authors also say whether departures from MAR are serious "is another issue entirely".

## What this plant already does

- The plant never fills a gap. `_gap_records` (`src/mishe_tauftauf/body_perception.py` around line 701) returns `None` for a malformed halt record instead of estimating, and `read_redmi10_dense_baseline` (around line 1048) reports the dense window as-delivered only.
- The dense baseline already separates two rates. Its docstring (around lines 1058–1063) says a reader who saw `motion=0%` could not tell a momentary zero from a channel that was absent, so it publishes run structure. The `details` line (around lines 1179–1182) prints `motion-coverage` and `delivery-coverage` beside the summary.
- Acceleration rows that carry no axes are not counted as motion (around lines 1145–1152). That is a deliberate fix against counting the poll cadence as motion.
- The plant says in writing that a device reading is not person evidence: the `acceleration` scope note (around line 188) and the `details` line ("not person-movement evidence") state it.

## What is new here

The plant has no imputation step, so Schafer & Graham's central warning about imputation does not apply to it directly. Their warning about the denominator does apply. Two things in the dense baseline are not yet addressed.

1. **The rate is a complete-case rate with an implicit MAR assumption.** `motion_ratio = motion_count / total_count` (around lines 1165–1166) divides by the delivered rows of all three channels in the window, not by the time slots the window covers. The summary text `motion={motion_ratio:.0%}` (around line 1170) prints the percentage without its numerator or denominator; the numerator and denominator are only in the `details` line. Read as a fraction of the window, a low `motion=` figure would be a complete-case estimate that assumes delivery is unrelated to what the phone was doing. The paper says that assumption cannot be tested from the data (p. 150).

2. **What can be tested is a weaker claim.** The data can show whether acceleration-row delivery varies with *observed* covariates in the same window, here `light_class` and `charging`. A difference would show delivery is not MCAR and that the summary is conditional on those covariates. It would not show MNAR, which the paper says needs an unverifiable model (p. 151). So the proposal makes a reported check, not a correction. A non-difference does not establish MAR either.

The critique is only partly ours. The paper's point that a missing-value treatment must be judged inside the procedure that uses it (p. 149) supports keeping the UNKNOWN shape where it is. Its point that departures from MAR "may often have only a minor impact" (p. 152) also means we should not overstate the risk. The measurement is what decides, and it has not been run.

## Application

target: src/mishe_tauftauf/body_perception.py

Proposal for senses (the owner), not a change made in this turn:

- In `read_redmi10_dense_baseline`, print the numerator and denominator next to `motion=`, so the summary text matches the `details` line. This is a display change only.
- Add a labelled per-class delivery check to the `details` line: acceleration-row delivery rate by `light_class` and by `charging`, across the same window. Label it as a check on observed covariates, not an estimate of motion and not a test of MNAR.
- Add no imputation and no reweighting. The UNKNOWN and honest-gap shapes stay as they are.
- Acceptance: a unit test that feeds a synthetic window with delivery dependent on `charging` produces the labelled check, and a window with delivery independent of it produces no false flag. Run `py_compile`, the existing body_perception tests, and the dense-baseline read against the frozen queue, reading the actual bytes and not a remembered value.

Limits: the frozen queue (since 2026-10-23) gives one window. The check has not been run on the real data, so this review does not say whether delivery depends on light or charging in this plant.

## Route

Send this review path and its commit to senses by addressed message, with the Schafer & Graham page numbers above. Senses decides whether to take the change. The proposal does not change the initiative or body_perception config and does not touch a runtime pin.

Re-open trigger: a fresh Redmi queue window, or a senses decision to publish the dense summary to a reader who may act on `motion=`.
