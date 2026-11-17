# Decision rungs: System 0, System 1, System 2

Every recurring decision a mind makes goes on the lowest rung that can carry it.

- **System 0** is deterministic code: a check, a threshold, a lookup or a path rule. If a rule decides it, write the check and delete the mind work.
- **System 1** is a typed decision model. It picks one label from a list we supply and returns per-option probabilities and a confidence. It is used only for a residual that System 0 cannot settle. Every choice offers an escape option (unclear / none). Code, not the model, acts on the answer. A confidence below the threshold, or the model choosing the escape option, escalates to a mind.
- **System 2** is a mind: a model-backed agent on one of the installed engines. It takes what neither rung settles.

## Registry

Each System 1 decision has a registry entry: its question, its criteria text, the model, a confidence threshold, and a log of every answer. The log keeps `outcome` null until a later real outcome is joined, so the threshold is calibrated from evidence, not assumed.

The first entry is the commit-risk class in `src/mishe_tauftauf/commit_risk.py`:

- Question: `commit-risk-class`. Criteria: `routine`, `delivery-path`, `dispatch-path`, `ordinary`, plus the escape option. The first three are path rules (System 0). `ordinary` — product code with no dispatch, delivery or locking effect — is a model-only label: a path rule cannot tell ordinary product code from the rest, so the model picks it for the residual instead of escaping to `unclear`.
- Threshold: 0.7. Below it the commit escalates to a mind. It is not calibrated yet, because no outcome is joined.
- Log: `.mishe-tauftauf/decision-log/commit-risk.jsonl`, one row per answered commit. A commit the service did not answer is not recorded, so a later pass asks again.

The second entry is DM triage in shadow, `src/mishe_tauftauf/dm_triage.py` (initiative P3):

- Question: `dm-triage-class`. Criteria: `action`, `fyi`, `duplicate`, plus the escape option. A bracketed record tag (`[permission]`, `[ci]`, `[note]`, ...) is a System 0 rule, so the model only sees plain addressed DMs. Two companion questions run per message: `dm-triage-urgency` (labels `0`-`3`) and `dm-triage-answered` (`answered`/`unanswered`).
- Threshold: 0.7. Escape or sub-threshold escalates to a mind.
- State: derived only — sequence, sender role, recipient role, tag, body-length bucket, a bounded first-line excerpt, a bounded markdown subject and a bounded content excerpt (the first non-empty prose line after the `[dm] to=ROLE` address, capped at 160 chars). Never the full DM body.
- Log: `.mishe-tauftauf/decision-log/dm-triage.jsonl`, one row per triaged message, written at the wake mail seam (`wake.py` `_execute`) and never raising. Shadow only: nothing reads it for dispatch. A missing or failing provider leaves `escalate` rows; it never stops the wake.
- Measured 2026-11-17: the 01Z backfill decided 52 of 140 rows, but live mail after 02Z escaped 69 of 71 (97%). Cause: addressed DMs carry prose, not a markdown heading, so the subject was empty and the first line was only the address — the model saw no content and answered `unclear`. `wall_text.message_excerpt` now supplies the first prose line; a probe of three real previously-escaped DMs resolves to `fyi` at 0.93-0.99 confidence. This is a coverage fix, not calibration: thresholds stay uncalibrated and `outcome` is null on all 211 rows until a real outcome is joined.

## Retired: per-start wake-needed decision

The wake gate once asked a System One model on every wake start whether the wake
had owed work (`owed-work` / `repeat` / `unclear`). Measured 2026-11-16:
`owed-work` precision was 43% against an always-wake base rate of 41%, so the
model added no useful discrimination. The per-start call was removed. New rows in
`decision-log/wake-gate.jsonl` keep `decision: null` so old and new rows share one
schema; the System 0 `same_dossier_as_last` verdict is the only gate signal, and
the live action log `wake-gate-live.jsonl` has no producer. Do not re-add the model
without new evidence that beats the base rate.

Both live registry entries are advisory: commit-risk never gates delivery and
DM triage never gates dispatch. A missing or failing decision service degrades to
the mind path; the logs keep an `escalate` row rather than stopping work.

## The duty

The `commit-risk` duty (hourly, `duties.json`) runs the advisory pass over recent commits:

- GREEN: the pass ran. With the daily cap spent, residual commits are held and reported, which is not a fault.
- UNKNOWN: the decision provider is not configured or unreadable, or residual commits stay unanswered while the daily cap has room (the service is failing).

The signal is advisory. Nothing gates delivery on it, and a missing or failing decision service never stops work: the commit is read by a mind instead.
