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
- State: derived only — sequence, sender role, recipient role, tag, body-length bucket, a bounded first-line excerpt and a bounded markdown subject. Never the full DM body.
- Log: `.mishe-tauftauf/decision-log/dm-triage.jsonl`, one row per triaged message, written at the wake mail seam (`wake.py` `_execute`) and never raising. Shadow only: nothing reads it for dispatch. A missing or failing provider leaves `escalate` rows; it never stops the wake.
- Measured 2026-11-17: the first backfill (address-line-only state) escaped 58/60; adding the bounded subject brought live decisions to 52 `system1` rows of 140 total (43 `fyi`, 9 `action`), the rest escalate. Thresholds are not calibrated yet; `outcome` stays null until a real outcome is joined.

## The duty

The `commit-risk` duty (hourly, `duties.json`) runs the advisory pass over recent commits:

- GREEN: the pass ran. With the daily cap spent, residual commits are held and reported, which is not a fault.
- UNKNOWN: the decision provider is not configured or unreadable, or residual commits stay unanswered while the daily cap has room (the service is failing).

The signal is advisory. Nothing gates delivery on it, and a missing or failing decision service never stops work: the commit is read by a mind instead.
