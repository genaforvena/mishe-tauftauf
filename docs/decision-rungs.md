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
- Threshold: 0.7. Below it the commit escalates to a mind. It is not calibrated: the ledger's `outcome` field stays null, and `src/mishe_tauftauf/commit_risk_outcomes.py` scores rows separately with a git-history proxy (a later fix or revert on the same paths within 72 h). Two measurements on 2026-11-19, as the ledger grew from 237 to 264 aged rows, scored 11/237 and then 14/264 as followed. The class ordering is not stable across them (dispatch-path 4/13 then 4/14, delivery-path 2/16 then 5/20, ordinary and routine 0 in both), so it cannot set 0.7: the proxy measures fix-ups, not classification correctness. In the larger sample, confidence below 0.7 had 5/75 followed, 0.7 to below 0.85 had 0/28, and 0.85 and above had 9/161. These small proxy counts do not establish whether confidence predicts wrong decisions. The threshold stays 0.7 until an outcome that measures a wrong decision exists.
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

The 2026-11-19 request for a first-class `signal:moving-pin-delivery` wake gate was
declined on measured evidence. The proposed System 0 rule skipped a wake when its
dossier matched the role's last and that wake verified, but `same_dossier_as_last`
is true on 0 of 605 signal starts and 0 of 618 inbox starts in the ledger — it
occurs only on intention wakes (55/143). The rule would skip zero of the 72 daily
moving-pin wakes; its "last wake verified" fallback would have skipped 63 of them,
5 of which changed, losing productive work. The first class has no deterministic
pre-wake discriminator in the ledger: its 72 wakes are only field-review and
genome, and changed and verified wakes share role, reasons and end detail.
`wake-gate-live.jsonl` still has no producer. The `system1-savings` signal reports
`changed_by_trigger` so the changed-rate baseline per class stays observable.

### Revised gate spec (2026-11-19): no safe gate for signal wakes

Ilya's revised objective is to reduce wasted wakes without losing productive changed wakes, using a real pre-wake discriminator or an honest no-gate close. A full search over the clean yielded population of `decision-log/wake-gate.jsonl` — 1,611 of 1,733 joined starts; the other 122 are `state: unknown` engine failures and were excluded — found no such discriminator.

- 562 clean signal wakes, 170 changed (30.2%); 263 `moving-pin-delivery` wakes, 59 changed (22.4%).
- `same_dossier_as_last` is true on 0 signal wakes. A stronger variant — dossier hash seen in this role's previous 20 wakes — covers only 2 clean signal wakes, 1 changed: no separation and negligible coverage.
- Reason count (1 / 2 / 3 or more), presence of an intention reason, and hour of day leave every meaningful subgroup in the 18–56% changed band. On `moving-pin-delivery`: one reason 49/224 (21.9%), two or more 10/39 (25.6%), intention reason 7/32 (21.9%), field-review 33/152 (21.7%), genome 26/111 (23.4%). The earlier apparent "hash seen before" saving was a data artifact: those rows were cancelled `initiatives-system1` wakes with no yield, not productive verified wakes.
- No subgroup has both a near-zero changed rate and useful volume, so skipping any signal subgroup loses productive wakes. Inbox wakes always wake anyway: each carries a human DM or a RED signal, an escape below.

Close: do not gate signal or inbox wakes. Intention wakes are the only class where a dossier-repeat signal discriminates (3/50 changed), but a blanket skip loses three productive rechecks, two of them event-first, so they stay ungated too. `wake-gate-live.jsonl` keeps no producer; the `system1-savings` duty stays the continuing detector.

Any future live gate must carry three safety escapes: force a wake if a role has had none for more than 3 hours; a human DM or a RED signal always wakes, never skipped; self-off if the changed-rate drops — operationally, if the changed-rate among wakes still running falls below the ungated baseline, the gate is suppressing productive work and disables itself. Because wake dispatch is high-impact, a gate also needs a deterministic activation/revert/reapply drill and a fresh review by a mind who did not author it. Reopen only with a pre-wake field that leaves a skip-eligible subgroup with a near-zero changed rate and non-trivial volume, or a corrected rule that neither relies on same-dossier for signal wakes nor loses productive changed wakes.

Commit-risk is the only live System 1 registry entry and it is advisory: it never gates
delivery. A missing or failing decision service degrades to the mind path; the log keeps an
`escalate` row rather than stopping work. The retired DM class lane left prose to the mind
in exactly the same way.

## The duty

The `commit-risk` duty (hourly, `duties.json`) runs the advisory pass over recent commits:

- GREEN: the pass ran. With the daily cap spent, residual commits are held and reported, which is not a fault.
- UNKNOWN: the decision provider is not configured or unreadable, or residual commits stay unanswered while the daily cap has room (the service is failing).

The signal is advisory. Nothing gates delivery on it, and a missing or failing decision service never stops work: the commit is read by a mind instead.
