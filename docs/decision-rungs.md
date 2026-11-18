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

The second registry entry was DM triage, now retired. `src/mishe_tauftauf/dm_triage.py` keeps a
System 0 pass over machine-addressed mail: a bracketed record tag and the fixed OnFailure
sentence `failure.report` writes decide `fyi` and `action`; prose is left to the mind. The two
templates live in `wall_text`, shared by reporter and matcher so a wording change cannot
silently stop classifying notices. Its log stays at `.mishe-tauftauf/decision-log/dm-triage.jsonl`,
written at the wake mail seam (`wake.py` `_execute`), never raising, and with no consumer in dispatch.

Its System 1 question `dm-triage-class` (criteria `action`/`fyi`/`duplicate` plus escape,
threshold 0.7) was removed on 2026-11-18. The reasons are measured, not stylistic:

- No consumer. `grep` over `src/`, `tests/`, `tools/`, `checks/` and `docs/` found only the
  module, its wake writer, its tests and this page.
- No outcome. Two attribution proxies failed: the containing wake's result, and a
  message-level join (reply DM, or a later commit touching a named path/sha) over 301 rows.
  The latter attributed 71 rows but inverted the classes — `action` 12% (3/24), `fyi` 21%
  (22/103), escape 26% (46/175) — because naming a path reports on it as often as it
  requests work. `outcome` is null on every row, so 0.70 was never calibrated.
- Cost without benefit: 80 provider calls in the 24 h before removal, one per residual DM,
  on the critical path before the wake engine started. The `urgency` and `answered`
  companions were removed earlier the same day for the same reason (no consumer).

Do not re-add the model without an outcome that separates "the recipient had to act" from
"the recipient knew something", and a consumer of the answer. The historical rows stay as
evidence.

## Retired: per-start wake-needed decision

The wake gate once asked a System One model on every wake start whether the wake
had owed work (`owed-work` / `repeat` / `unclear`). Measured 2026-11-16:
`owed-work` precision was 43% against an always-wake base rate of 41%, so the
model added no useful discrimination. The per-start call was removed. New rows in
`decision-log/wake-gate.jsonl` keep `decision: null` so old and new rows share one
schema; the System 0 `same_dossier_as_last` verdict is the only gate signal, and
the live action log `wake-gate-live.jsonl` has no producer. Do not re-add the model
without new evidence that beats the base rate.

Commit-risk is the only live System 1 registry entry and it is advisory: it never gates
delivery. A missing or failing decision service degrades to the mind path; the log keeps an
`escalate` row rather than stopping work. The retired DM class lane left prose to the mind
in exactly the same way.

## The duty

The `commit-risk` duty (hourly, `duties.json`) runs the advisory pass over recent commits:

- GREEN: the pass ran. With the daily cap spent, residual commits are held and reported, which is not a fault.
- UNKNOWN: the decision provider is not configured or unreadable, or residual commits stay unanswered while the daily cap has room (the service is failing).

The signal is advisory. Nothing gates delivery on it, and a missing or failing decision service never stops work: the commit is read by a mind instead.
