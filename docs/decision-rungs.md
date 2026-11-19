# Decision rungs: System 0, System 1, System 2

Every recurring decision a mind makes goes on the lowest rung that can carry it.

- **System 0** is deterministic code: a check, a threshold, a lookup or a path rule. If a rule decides it, write the check and delete the mind work.
- **System 1** is a typed decision model. It picks one label from a list we supply and returns per-option probabilities and a confidence. It is used only for a residual that System 0 cannot settle. Every choice offers an escape option (unclear / none). Code, not the model, acts on the answer. A confidence below the threshold, or the model choosing the escape option, escalates to a mind.
- **System 2** is a mind: a model-backed agent on one of the installed engines. It takes what neither rung settles.

## Registry

Each System 1 decision has a registry entry: its question, its criteria text, the model, a confidence threshold, and a log of every answer. The log keeps `outcome` null until a later real outcome is joined, so the threshold is calibrated from evidence, not assumed.

The first entry is the commit-risk class in `src/mishe_tauftauf/commit_risk.py`:

- Question: `commit-risk-class`. Criteria: `routine`, `delivery-path`, `dispatch-path`, `ordinary`, plus the escape option. `routine`, `delivery-path` and `dispatch-path` are path rules (System 0). `ordinary` — product code with no dispatch, delivery or locking effect — is almost always a model label: a path rule cannot tell ordinary product code from the rest, so the model picks it for the residual instead of escaping to `unclear`. One stable view path, `tools/network_view.py`, is a System 0 lookup for `ordinary`; it is one file, not a family, because router and boundary tools can change host-guard state.
- System 0 path rule: docs, tests and checks are supporting paths, so a non-routine rule decides even when they accompany the code. Engine runner modules (`engine_*.py`) are `dispatch-path`; release, promotion, pin and delivery modules are `delivery-path`. Two different non-routine rules, or any unmatched path, remains the residual. A replay of the 600-row ledger on 2026-11-18 found 58 residual commits this rule now decides: 36 of 39 model labels agreed, 3 were arguably rule-correct (`engine_codex.py`, `wake.py`, `pin_observation.py`) and 19 escalations gained a label. A replay of the 633-row ledger on 2026-11-18 found the `tools/network_view.py` lookup makes 15 more escalates deterministic with no new disagreement against the 29 model labels on that path; `tools/root-migration-flip.sh` (7 `delivery-path` / 1 `dispatch-path`) and `duties.json` (all four labels) stay residual.
- Exact-path lookup: `tools/delivery_check.py` is `delivery-path`. In the 643-row ledger of 2026-11-19 all four model-labelled commits touching that file chose `delivery-path`, and two escalates had no other non-routine path; the lookup makes all six deterministic with no disagreement. It stays one file, not a `tools/deliver*` family.
- Rule correction (2026-11-19): `src/mishe_tauftauf/delivery_requests.py` is `delivery-path`, not `dispatch-path`. It enqueues and consumes delivery requests and owns patch/revert phases together with `release_promotion`, which shares the same lock and admission mechanisms and is already `delivery-path`. The file was listed in the dispatch pattern from the first rules commit, shadowing the delivery pattern's `deliver\w*`; both model-labelled commits touching it chose `delivery-path` (confidence 1.0 and 0.89). Removing the dispatch listing makes a pin-delivery + release-promotion commit deterministic instead of a residual, and future commits to that file deterministic too. The "dispatch lock" and "admission" words in its docstring name the mechanism, not the class: the class follows what the code does.
- Limit of path rules: a path alone cannot decide the scheduler-config case. A 2026-11-18 replay of the provider-decided rows found 28 commits touching `duties.json` split across all four labels (`delivery-path` 7, `dispatch-path` 6, `ordinary` 11, `routine` 4): the class follows which duty changed, not the file. Adding `duties.json` to a `delivery-path` regex would misclassify 21 of those 28, so it stays a model residual.
- Threshold: 0.7. Below it the commit escalates to a mind. It is not calibrated: the ledger's `outcome` field stays null, and `src/mishe_tauftauf/commit_risk_outcomes.py` scores rows separately with a git-history proxy (a later fix or revert on the same paths within 72 h). On 2026-11-19 that scored 11 of 237 aged rows as followed — dispatch-path 4/13 (31%), delivery-path 2/16 (13%), ordinary 0/29, routine 0/100. The ordering supports the class vocabulary but cannot set 0.7: the proxy measures fix-ups, not classification correctness, and confidence bands do not separate follow-up (0.7-0.85: 0/20, >=0.85: 6/41). The threshold stays 0.7 until an outcome that measures a wrong decision exists.
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
