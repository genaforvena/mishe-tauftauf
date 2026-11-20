# Decision rungs: System 0, System 1, System 2

Every recurring decision a mind makes goes on the lowest rung that can carry it.

- **System 0** is deterministic code: a check, a threshold, a lookup or a path rule. If a rule decides it, write the check and delete the mind work.
- **System 1** is a typed decision model. It picks one label from a list we supply and returns per-option probabilities and a confidence. It is used only for a residual that System 0 cannot settle. Every choice offers an escape option (unclear / none). Code, not the model, acts on the answer. A confidence below the threshold, or the model choosing the escape option, escalates to a mind.
- **System 2** is a mind: a model-backed agent on one of the installed engines (the model runtime a wake runs on). It takes what neither rung settles.

## Registry

Each System 1 decision has a registry entry: its question, its criteria text, the model, a confidence threshold, and a log of every answer. The log keeps `outcome` null until a later real outcome is joined, so the threshold is calibrated from evidence, not assumed.

The first entry is the commit-risk class in `src/mishe_tauftauf/commit_risk.py`:

- Question: `commit-risk-class`. Criteria: `routine`, `delivery-path`, `dispatch-path`, `ordinary`, plus the escape option. `routine`, `delivery-path` and `dispatch-path` are path rules (System 0). `ordinary` — product code with no dispatch, delivery or locking effect — is almost always a model label: a path rule cannot tell ordinary product code from the rest, so the model picks it for the residual instead of escaping to `unclear`. One stable view path, `tools/network_view.py`, is a System 0 lookup for `ordinary`; it is one file, not a family, because router and boundary tools can change host-guard state.
- System 0 path rule: docs, tests and checks are supporting paths, so a non-routine rule decides even when they accompany the code. Engine runner modules (`engine_*.py`) are `dispatch-path`; release, promotion, pin and delivery modules are `delivery-path`. Two different non-routine rules, or any unmatched path, remains the residual. A replay of the 600-row ledger on 2026-11-18 found 58 residual commits this rule now decides: 36 of 39 model labels agreed, 3 were arguably rule-correct (`engine_codex.py`, `wake.py`, `pin_observation.py`) and 19 escalations gained a label. A replay of the 633-row ledger on 2026-11-18 found the `tools/network_view.py` lookup makes 15 more escalates deterministic with no new disagreement against the 29 model labels on that path; `tools/root-migration-flip.sh` (7 `delivery-path` / 1 `dispatch-path`) and `duties.json` (all four labels) stay residual.
- Exact-path lookup: `tools/delivery_check.py` is `delivery-path`. In the 643-row ledger of 2026-11-19 all four model-labelled commits touching that file chose `delivery-path`, and two escalates had no other non-routine path; the lookup makes all six deterministic with no disagreement. It stays one file, not a `tools/deliver*` family.
- Four more exact-path `ordinary` lookups (2026-11-20): `tools/household_view.py`,
  `tools/commit-msg-attribution.py`, `src/mishe_tauftauf/provider_budget.py` and
  `src/mishe_tauftauf/system1_savings.py`. In the 773-row ledger their model answers were
  respectively 8/8, 4/4, 4/4 and 4 ordinary plus one escalate — no model labelled any of them
  dispatch, delivery or routine. Replaying the 440 residual rows with these lookups newly
  settles 18 (7+3+3+5) with zero disagreement against any model label. They stay exact files,
  not a `tools/*` or reporting family: a change to one of them alongside dispatch code still
  leaves the residual to the model. There is deliberately no `system1:` subject rule: the
  subject rule runs only after `system0` returns `None` on a mixed path set, and `ordinary` is
  not the cautious label the delivery/release token is, so a future system1 commit touching
  dispatch code would be mislabelled rather than escalated.
- Four more exact-path lookups (2026-11-20, 879-row ledger): `tools/map_tiles.py` (8/8
  `ordinary`), `src/mishe_tauftauf/commit_risk_text.py` (4/4 `ordinary`),
  `src/mishe_tauftauf/consumer_fence.py` (4/4 `delivery-path`) and
  `src/mishe_tauftauf/arm.py` (6/6 `dispatch-path`). No model labelled any of them a
  different class. Replaying all four against the current rules newly settles 25 residual
  rows (15 model, 10 escalate) with zero disagreement with any model label, zero label flips
  on already-settled rows and zero regressions. `arm.py` is the scheduler's arming path and
  `consumer_fence.py` is delivery's wrong-pin consumer check; each stays one exact file, so
  a mixed path set still escalates. `commit_risk_text.py` is the advisory classifier's own
  text boundary — reporting code, not dispatch or delivery.
- Two more exact-path lookups (2026-11-20, from the 50-commit repo-change delta): `tools/map_render.py`
  (`ordinary`, 3/3 model answers at 0.90/0.99/0.98, all "map:" observation subjects) and
  `src/mishe_tauftauf/retirement.py` (`delivery-path`, 2/2 at 0.99/0.73). Replaying just these two
  against the current rules newly settles 4 ledger rows (2 map-render, 2 retirement), agreeing
  with the earlier model label on each, with zero label flips and zero regressions. `retirement.py`
  retires finished author branches and protects live runtime pins for the delivery watcher — the
  sibling of `consumer_fence.py`, not a `retire\w*` family; a mixed path set with dispatch code
  still escalates.
- Rule correction (2026-11-19): `src/mishe_tauftauf/delivery_requests.py` is `delivery-path`, not `dispatch-path`. It enqueues and consumes delivery requests and owns patch/revert phases together with `release_promotion`, which shares the same lock and admission mechanisms and is already `delivery-path`. The file was listed in the dispatch pattern from the first rules commit, shadowing the delivery pattern's `deliver\w*`; both model-labelled commits touching it chose `delivery-path` (confidence 1.0 and 0.89). Removing the dispatch listing makes a pin-delivery + release-promotion commit deterministic instead of a residual, and future commits to that file deterministic too. The "dispatch lock" and "admission" words in its docstring name the mechanism, not the class: the class follows what the code does.
- Limit of path rules: a path alone cannot decide the scheduler-config case. A 2026-11-18 replay of the provider-decided rows found 28 commits touching `duties.json` split across all four labels (`delivery-path` 7, `dispatch-path` 6, `ordinary` 11, `routine` 4): the class follows which duty changed, not the file. Adding `duties.json` to a `delivery-path` regex would misclassify 21 of those 28, so it stays a model residual.
- Subject rule (2026-11-19): on the residual, a commit whose subject opens with a `delivery` or `release` area token (`delivery: ...`, `release review: ...`, `release_promotion: ...`) is `delivery-path` from code. Minds write subjects as `area: summary`, and in the 688-row ledger every model-answered row of that family chose `delivery-path` (38 of 38); the label is also the cautious one, so an over-broad token costs a review glance, not a skipped one. It runs only after the path rules — three ledger rows with such subjects were settled `routine` by the byte-exact path rules — and it requires the colon, so free prose stays with the model. A same-day replay: of the family's 53 rows, 33 are already path-rule-settled, the subject rule settles 15 model rows (all agreeing) and 5 escalations, leaving zero disagreements and zero family residual. The mirrored rule (`network:` to `ordinary`) is dead: 1 of its 43 rows is `dispatch-path` (`network: the router self-limit gates on the 5-minute load`), and `dispatch-path` is exactly the label a prefix shortcut must never hide.
- Threshold: 0.7. Below it the commit escalates to a mind. It is not calibrated: the ledger's `outcome` field stays null, and `src/mishe_tauftauf/commit_risk_outcomes.py` scores rows separately with a git-history proxy (a later fix or revert on the same paths within 72 h). On 2026-11-19 the aged sample grew 237 to 264 to 298 rows, followed 11/237, 14/264 and 18/298. The proxy measures fix-ups, not classification correctness, so it cannot set 0.7. The 298-row sample sharpens why: follow-up tracks the commit's label, not the model's confidence. Model-labelled delivery-path commits were followed 6/23 (26%) and dispatch-path 4/15 (27%), against ordinary 1/40 (2.5%) and routine 0/5; those two high-impact labels are the ones System 0 already decides by path. Within a label the confidence split is flat — delivery-path 1/8 below 0.85 versus 5/15 at or above, ordinary 0/19 versus 1/21 — and no aged model-labelled row fell below 0.7. The apparent confidence split (below 0.85 1/31, at or above 10/52, two-sided Fisher p about 0.05) is that label mix, not confidence. Escalated rows (confidence below 0.7 or the escape option, handled by a mind) were followed 7/84 (8%), no higher than labelled rows. The threshold stays 0.7 until an outcome that measures a wrong decision exists.
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
`changed_by_trigger` for each trigger class that started in the trailing 24 hours.
Its `ended` count covers wakes that started in that window and have a recorded
end. Its changed rate uses those that settled as `yielded` with a recorded result,
and is null when there are none. The top-level `changed_rate_all` keeps the whole
`ended` population as its denominator, so it can differ from the per-trigger rates.
A ledger with no readable start reports UNKNOWN. Readable starts before the
window can support a measured zero even when a torn line was skipped.

Since 2026-11-20 the signal also reports `changed_by_signal`, computed from each started
wake's `signal:` reasons (a wake co-firing on several signals counts once per name).
`retirement_candidates` lists signals with at least 20 ended wakes in the 24 h window and a
changed rate below 15% — a flag for trigger-retirement review, never an auto-skip. For each
flagged name the signal also reports `retirement_review`: how many of its changed wakes
carried another independent wake reason (`inbox`, an `intention:`, or a second named signal)
that would have woken the role anyway, and whether that covers every changed wake. That is
the co-firing escape below, computed by System 0 so a reviewer does not re-mine the ledger;
`changed_all_cofired: true` means the signal added no changed wake on its own. A candidate with
zero changed wakes needs the same judgment, not a vacuous pass. The volume floor exempts
rare-event detectors such as `doctor`, whose low changed rate is their purpose; the values
are screening thresholds, not fitted cutoffs. On the ledger that motivated the check no
signal qualifies (`moving-pin-delivery` is 21/75, 28.0%), so the list and the review were
empty at introduction. The three escapes and the no-gate close above still stand.

Read `sole_signal_receipts` alongside the claimed yield rates. For clean yields with a
single signal reason, it counts known and unknown work receipts and shows how many wakes
had an attributed commit or a registered artifact. A wake can count in both output columns.
Signals with no such yields have no entry.
For example, a delivery wake can yield `verified` and register evidence from the live
consumer. Missing, malformed or mismatched receipts count as unknown. Use the receipts
to find evidence before retiring a trigger, then inspect what the outputs establish.

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

## Measured saving

Moves down a rung are only useful if the load they removed is visible. The `system1-savings`
signal reports a `rules` block from `decision-log/commit-risk.jsonl` for the trailing 24 hours:
`system0` (commits a path or subject rule settled), split into `path_rule` and `subject_rule`,
against `system1` (model answers) and `escalate` (a mind read the commit). `rule_share` is the
fraction settled without a model or a mind. This is the measured saving from the rules above,
not a forecast: a row with `source: system0` is one the model and the mind did not take.
Rows outside the window and rows with a torn or non-numeric timestamp are not counted; a
missing ledger reports null counts (`ledger: missing`), never a zero saving. An empty ledger
reports zero counts and no share. The block is advisory like the rest of the signal: a missing
commit-risk ledger does not turn the duty UNKNOWN, because the wake numbers remain measured.

## Docs-naming seam (repo-change, System 0)

The `repo-change` initiative (Ilya, 2026-11-20) calls for one signal with pub and docs
listeners. Docs needs its deterministic part first: which reader docs even name a changed
source path? `mishe_tauftauf.repo_change_docs.docs_naming()` answers that from the changed
paths alone. For each path it searches the reader corpus defined by `docs_tools.search_docs`
(README.md and immediate `docs/*.md`, symlinks excluded) for three identifiers — the full
relative path, the dotted module name for `src/` Python files, and the file stem. A hit must
match the whole identifier, not a substring, so the word `budget` does not name
`budget_producer.py`. Results group passages by doc with line numbers, in path/line order;
duplicate paths collapse and a path with no hit is a complete negative, not an error.

This is evidence for the next rung, not a decision: a candidate doc is what a System 1
decision model would receive alongside the commit, and a no-hit still leaves the separate
question of a behaviour change. Nothing wakes docs from this function, and an unreadable
corpus raises rather than reporting a false no-hit.

The builtin `repo-change` duty (`duties.json`, every 900 s, `max_age` 2700 s) runs that seam
for each changed path. It publishes the newest commit with per-subscriber deltas for `pub` and
`docs`, calls no model and wakes no one itself; the residual judgement goes to System 1.

The wake dossier also carries the registered ack command for each signal the wake saw: for
`repo-change` it is `env PYTHONPATH=src python3 -m mishe_tauftauf.repo_change_signal --home {home}
--repo {repo} --wake {role}`. Running it advances only the named subscriber's cursor to the current
HEAD; a subscriber that reads the detail but never acks keeps receiving the same commit tail. A
resolver watch that is not a cursor reader (system1) gets the same command as a read-only refresh
that moves no cursor. The producer installs the ack command when needed and keeps the duty's
freshness limit, subscribers, wake mode and reoffer interval.
