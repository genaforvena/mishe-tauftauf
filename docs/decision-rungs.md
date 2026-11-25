# Decision rungs: System 0, System 1, System 2

Every recurring decision a mind makes goes on the lowest rung that can carry it.

- **System 0** is deterministic code: a check, a threshold, a lookup or a path rule. If a rule decides it, write the check and delete the mind work.
- **System 1** is a typed decision model. It picks one label from a list we supply and returns per-option probabilities and a confidence. It is used only for a residual that System 0 cannot settle. Every choice offers an escape option (unclear / none). Code, not the model, acts on the answer. A confidence below the threshold, or the model choosing the escape option, escalates to a mind.
- **System 2** is a mind: a model-backed agent on one of the installed engines (the model runtime a wake runs on). It takes what neither rung settles.

Terms used below:

- **Residual**: a case that no System 0 rule settles.
- **Escalate**: hand a case to a mind (System 2) instead of answering it with a model.
- **Ledger**: an append-only JSON-lines log, one row per answer, in `.mishe-tauftauf/decision-log/`.
- **Outcome**: a later measured result joined to a ledger row. It stays null until one exists.
- **Wake**: one run of a mind on its engine, from its start to its yield. See [how a mind chooses](how-a-mind-chooses.md).
- **Yield**: the last step of a wake, which records its result (`changed`, `verified` or `blocked`).
- **Changed rate**: the share of yielded wakes whose result is `changed`.
- **Receipt**: what a wake left behind, an attributed commit or a registered artifact.
- **Signal** and **duty**: a published reading, whose state is `OK` while fresh, and the periodic check that gives it a verdict, GREEN, RED or UNKNOWN. See [wall coordination](wall-coordination.md).
- **Ack** and **cursor**: a subscriber's ack moves its cursor past a commit, so that commit is not delivered to it again.
- **Internal-only**: a commit whose subject starts with `chore`, `style`, `typo`, `whitespace`, `pin` or `pinbump`, or that touches none of `src`, `tools`, `duties`, `docs` or `tests` (`is_noise` in `repo_change_signal.py`). When the `pub` role (the publication mind; its job is listed in [mesh](mesh.md)) reads the delta, internal-only commits and docs-only commits are skipped (`pub_verdict`), so neither one owes pub a read. Docs-only means every touched path is under `docs/`, `tests/` or `checks/` (`docs_only`); a commit that also touches `src`, `tools` or `duties` still counts.

## Registry

Each System 1 decision has a registry entry: its question, its criteria text, the model, a confidence threshold, and a log of every answer. The log keeps `outcome` null until a later real outcome is joined, so the threshold is calibrated from evidence, not assumed.

Commit-risk is the only live System 1 entry. It is advisory: it never gates delivery. A missing or failing decision service degrades to the mind path and the log keeps an `escalate` row; work does not stop.

## Commit-risk: question, criteria and threshold

Code: `src/mishe_tauftauf/commit_risk.py` (decision), with path rules in `src/mishe_tauftauf/commit_risk_text.py`. Question `commit-risk-class`. Criteria: `routine`, `delivery-path`, `dispatch-path`, `ordinary`, plus the escape option.

- `routine`, `delivery-path` and `dispatch-path` come from System 0 path rules.
- `ordinary` (product code with no dispatch, delivery or locking effect) is mostly a model label: a path rule cannot tell ordinary product code from the rest, so the model picks it for the residual.
- Mixed path sets with two different non-routine rules, or any unmatched path, stay residual.

The System 0 path rules are the `RULES` table in `src/mishe_tauftauf/commit_risk_text.py`. That table is the authority; this page does not copy it. A change to any rule is a rule change and needs a replay against the ledger. Broadly, wake, engine-runner and scheduling modules are `dispatch-path`, and release, promotion, pin, heal and delivery modules are `delivery-path`. The rules a reader is most likely to get wrong:

- Supporting paths (docs, tests, checks) do not dilute a non-routine rule.
- `ordinary` lookups name exact files, not families. A view or reporting file changed alongside dispatch code still stays with the model. A router or host-guard tool never goes in an `ordinary` lookup, because it can change guard state.
- `delivery_requests.py` is `delivery-path`, not `dispatch-path`: its "dispatch lock" wording names the mechanism, not the class.
- Subject rule, run only after the path rules: a commit whose subject has a colon and an area token before it that starts with `delivery` or `release` (`delivery-cadence:` and `release review:` both match) is `delivery-path`. It requires the colon, so free prose stays with the model. There is no `system1:` subject rule, and no mirrored `network:` to `ordinary` rule, because a `dispatch-path` commit must never be hidden behind a prefix shortcut.
- Residual that stays with the model: `tools/root-migration-flip.sh` and `duties.json`. A `duties.json` change follows which duty changed, not the file, so no path regex can classify it.

Threshold: 0.7, not calibrated. Below it a commit escalates to a mind. The ledger's `outcome` field is still null. `src/mishe_tauftauf/commit_risk_outcomes.py` scores rows with a git-history proxy (a later fix or revert on the same paths within 72 h). That proxy measures fix-ups, not classification correctness, so it cannot set the threshold. Current reading: follow-up tracks the commit's label, not the model's confidence, and no aged model-labelled row fell below 0.7. The threshold stays 0.7 until an outcome that measures a wrong decision exists.

Log: `.mishe-tauftauf/decision-log/commit-risk.jsonl`, one row per answered commit. A commit the service did not answer is not recorded, so a later pass asks again. Dated replay counts from earlier passes are in this file's git history (`git log -- docs/decision-rungs.md`). They are evidence, not current rules.

## Retired System 1 entries

### DM triage

`src/mishe_tauftauf/dm_triage.py` keeps a System 0 pass over machine-addressed mail: a bracketed record tag and the fixed OnFailure sentence `failure.report` writes decide `fyi` and `action`; prose is left to the mind. The two templates live in `wall_text`, shared by reporter and matcher. The log is `.mishe-tauftauf/decision-log/dm-triage.jsonl`, written at the wake mail seam (`wake.py` `_execute`), never raising, with no consumer in dispatch.

The System 1 question `dm-triage-class` was removed on 2026-11-18 for three measured reasons:

- No consumer. Nothing in `src/`, `tests/`, `tools/`, `checks/` or `docs/` read its answer.
- No outcome. Two attribution proxies failed (the containing wake's result; a message-level join over 301 rows). The message-level join inverted the classes, because naming a path reports on it as often as it requests work. `outcome` stayed null, so 0.70 was never calibrated.
- Cost without benefit: 80 provider calls in 24 h, one per residual DM, on the critical path before the wake engine started.

Do not re-add the model without an outcome that separates "the recipient had to act" from "the recipient knew something", and a consumer of the answer.

### Per-start wake-needed gate

The wake gate once asked a System 1 model on every wake start whether the wake had owed work. Measured 2026-11-16: `owed-work` precision was 43% against an always-wake base rate of 41%. The per-start call was removed. New rows in `decision-log/wake-gate.jsonl` keep `decision: null`. The System 0 `same_dossier_as_last` verdict (the dossier is the context packet a wake is handed, see [How a mind chooses](how-a-mind-chooses.md)) is the only gate signal. `wake-gate-live.jsonl` has no producer.

Gating signal and inbox wakes was measured and closed on 2026-11-19. Over 1,611 clean yielded starts, 170 of 562 signal wakes changed (30.2%), and no subgroup had both a near-zero changed rate and useful volume. `same_dossier_as_last` is true on 0 signal wakes. Intention wakes are the only class where a repeated dossier discriminates (3/50 changed), but a blanket skip loses productive rechecks, so they stay ungated too. Reopen only with a pre-wake field that leaves a skip-eligible subgroup with a near-zero changed rate and non-trivial volume, or a corrected rule that neither relies on same-dossier for signal wakes nor loses productive changed wakes.

Any future live gate needs three escapes: force a wake if a role has had none for more than 3 hours; a human DM or a RED signal always wakes; the gate disables itself if the changed-rate among wakes still running falls below the ungated baseline. Wake dispatch is high-impact, so a gate also needs a deterministic activation, revert and reapply drill and a fresh review by a mind that did not author it.

## The duty

The `commit-risk` duty (hourly, `duties.json`) runs the advisory pass over recent commits:

- GREEN: the pass ran. With the daily cap spent, residual commits are held and reported, which is not a fault.
- UNKNOWN: the decision provider is not configured or unreadable, or residual commits stay unanswered while the daily cap has room (the service is failing).

The signal is advisory. A missing or failing decision service never stops work: the commit is read by a mind instead.

## Measured saving

In plain words: a commit can be settled three ways. A rule settles it (System 0, no model call), a model answers it (System 1), or it goes to a mind to read. This section counts how often each happens over the last 24 hours, so a rule that takes work off the minds can be seen. The one number to read first is `rule_share`, the fraction settled with no model and no mind.

Moves down a rung are only useful if the load they removed is visible. The `system1-savings` signal reports a `rules` block from `decision-log/commit-risk.jsonl` for the trailing 24 hours: `system0` (commits a path or subject rule settled, split into `path_rule` and `subject_rule`), against `system1` (model answers) and `escalate` (a mind read the commit). `rule_share` is the fraction settled without a model or a mind. A row with `source: system0` is one the model and the mind did not take.

Rows outside the window and rows stamped more than 5 seconds in the future are skipped, not counted. Rows with a torn or non-numeric timestamp, a non-object row, or a `source` other than `system0`, `system1` or `escalate` are counted as `malformed`. A missing ledger reports null counts (`ledger: missing`), never a zero saving. An empty ledger reports zero counts and no share. A missing commit-risk ledger does not turn the duty UNKNOWN, because the wake numbers remain measured.

The signal also reports, per trigger class over the trailing 24 hours, `changed_by_trigger` and `changed_by_signal` (a wake co-firing on several signals counts once per name). The changed rate uses wakes that settled as `yielded` with a recorded result, and is null when there are none. `changed_rate_all` keeps the whole ended population as its denominator. `retirement_candidates` lists signals with at least 20 ended wakes and a changed rate below 15%: a flag for review, never an auto-skip. For each flagged name, `retirement_review` shows how many changed wakes carried another independent wake reason, and `changed_all_cofired: true` means the signal added no changed wake on its own. A candidate with zero changed wakes needs the same judgment, not a vacuous pass. A ledger with no readable start reports UNKNOWN.

Read `sole_signal_receipts` alongside the claimed yield rates. For clean yields with a single signal reason, it counts known and unknown work receipts and how many wakes had an attributed commit or a registered artifact. Missing, malformed or mismatched receipts count as unknown. Before retiring a trigger, read the evidence behind its receipts and check for findings recorded only in walls or local notes.

## Docs-naming seam (repo-change, System 0)

`mishe_tauftauf.repo_change_docs.docs_naming()` answers which reader docs name a changed source path, from the changed paths alone. For each path it searches the reader corpus defined by `docs_tools.search_docs` (README.md and immediate `docs/*.md`, symlinks excluded) for three identifiers: the full relative path, the dotted module name for `src/` Python files, and the file stem. The stem is dropped when it is shorter than three characters or is a generic name (`__init__`, `readme`, `conftest`, `test`, `tests`, `docs`), because it would match nearly every doc. A hit must match the whole identifier, not a substring. A path with no hit is a complete negative, and an unreadable corpus raises rather than reporting a false no-hit.

This is evidence for a later rung, not a decision. The builtin `repo-change` duty (`duties.json`, every 900 s, `max_age` 2700 s) runs that seam for each changed path. It publishes the newest commit with per-subscriber deltas for `pub` and `docs`, calls no model and wakes no one itself.

`docs` is the one reader of the raw `repo-change` signal, so it wakes on every commit. `pub` listens on `repo-change-pub`, whose value is the newest commit in pub's delta that is neither internal-only nor docs-only (`pub_verdict`); a commit that is internal-only or docs-only does not wake pub. `system1` listens on `repo-change-system1`, whose value is the newest non-internal-only commit touching `src`, `tools` or `duties` (`system1_verdict`); `checks` does not count, so a docs-only or checks-only commit does not wake system1 (2026-11-21: 24 raw wakes in 4 h, all docs-only, all verified 'no rung impact').

The wake dossier carries the registered ack command for each signal the wake saw. Running it advances only the named subscriber's cursor to the current HEAD; a subscriber that reads the detail but never acks keeps receiving the same commit tail. A resolver watch that is not a cursor reader (`system1` on `repo-change-system1`) gets the same command as a read-only refresh that moves no cursor.
