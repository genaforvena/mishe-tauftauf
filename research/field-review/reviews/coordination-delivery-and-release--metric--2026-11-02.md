---
field: coordination-delivery-and-release
angle: metric
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Coordination, delivery and release — metric REVISIT: DORA instability metrics as the complement to change lead time (failed deployment recovery, change fail rate, deployment rework)

## What we read

- Primary source: DORA Guides, "DORA's software delivery performance metrics", https://dora.dev/guides/dora-metrics-four-keys/ — fetched 2026-11-02, HTML 19454 B, sha256 `00f7a12f296db54df7006f19badcfe668819c30fc50d386c471fe55531bcdff1`, text-extracted locally. Last updated on page: January 5, 2026. Authors byline on page: Nathen Harvey. License CC BY 4.0 (footer).
  - Definition block read directly (after tag stripping):
    - "DORA has identified five software delivery performance metrics that provide an effective way of measuring the outcomes of the software delivery process."
    - Throughput: "Change lead time: The amount of time it takes for a change to go from committed to version control to deployed in production."
    - "Deployment frequency: The number of deployments over a given period or the time between deployments."
    - "Failed deployment recovery time: The time it takes to recover from a deployment that fails and requires immediate intervention."
    - Instability: "Change fail rate: The ratio of deployments that require immediate intervention following a deployment. Likely resulting in a rollback of the changes or a 'hotfix' to quickly remediate any issues."
    - "Deployment rework rate: The ratio of deployments that are unplanned but happen as a result of an incident in production."
    - Functional framing: metrics as "Leading indicators for organizational performance and employee well-being" and "Lagging indicators for software development and delivery practices."
    - Throughput vs instability grouping verbatim: "DORA's software delivery performance metrics focus on a team's ability to deliver software safely, quickly, and efficiently."
    - Key insight: "DORA's research has repeatedly demonstrated that speed and stability are not tradeoffs. In fact, we see that the metrics are correlated for most teams. Top performers do well across all five metrics, and low performers do poorly."
    - Scoping caveat: metrics are "best suited for measuring one application or service at a time" and "Apply the metrics in the context of the application or service your team is delivering."
    - Pitfalls verbatim: "Setting metrics as a goal. Ignoring Goodhart's law and making broad statements like, 'Every application must deploy multiple times per day by year's end,' increases the likelihood that teams will try to game the metrics." "Having one metric to rule them all. Attempting to measure complex systems with the idea that only one metric matters. Teams should identify multiple metrics, including some with a healthy amount of tension between them."
  - Not re-read for this review: linked capability catalog, value-stream-management guide, DORA Quick Check, and the detailed history footnote beyond the companion page below — not relied upon for definitions.
- Companion source (same programme, read same day): DORA Insights, "A history of DORA's software delivery metrics", https://dora.dev/insights/dora-metrics-history/ — fetched 2026-11-02, HTML 14861 B, sha256 `62b6283823c97e085e90bb07e2fc8969272b5bf3a4a624a83ded8124b08893d7`, author Nathen Harvey, posted January 2, 2026. Read to distinguish the five-metric model from the four-keys label the 2026-10-31 predecessor cited. History passages read:
  - Origins 2014: "four variables: deployment frequency, lead time for changes, mean time to recover (MTTR), and change fail rate" with "change fail rate did not significantly correlate with the other variables to form a single latent construct".
  - 2015: "Throughput measures: Deployment frequency and deployment lead time. Stability measures: Mean time to recover (MTTR) and change fail rate."
  - 2016–2018: standardisation, then "availability as a measure of operational health" → "Software Delivery and Operational (SDO) performance".
  - 2021–2023: "availability to reliability" and the rename captured in the current guide as "Failed Deployment Recovery Time" (was MTTR). This page is the provenance for the rename the current guide uses; the metric definitions themselves are quoted from the guide above.
- Plant code read directly: `src/mishe_tauftauf/delivery.py` (submit 401ff, check, integrate 537ff, finish 574ff, line 603ff; `_sync` crash-safe projection 319ff; `_save`/`_path`/`_lock`), `src/mishe_tauftauf/release_readiness.py` (`report` READY/HOLD on candidate SHA + `ci/latest.json` pass+sha+0–300 s + `health/linked-sites.json` v1 sha==candidate), `src/mishe_tauftauf/ci_watch.py` for CI shape. No edit made to these files in this lane.
- Live evidence 2026-11-02: `.mishe-tauftauf/deliveries/` 13 paths (12 json + 1 docx); sampled json all lack any `*_at` time key, grep for `utc|datetime` in delivery.py finds only prose/timeout args, and all records lack integrated_at/blocked_at. States observed: 9 done, 2 integrated, 1 blocked (`checker-input-budget-20260930` blocked "fatal: cannot change to '/tmp/checker-budget-fix'"), 1 further blocked family via recovery paths. Dashboard: HEAD 84435cb2, RUNTIME pinned 054d4c64, STATE RED controller-shed LIMITED, CI PENDING 84435cb2, GRID EXHAUSTED next REVISIT is this cell.
- Relation to predecessor: the prior `coordination-delivery-and-release--metric--2026-10-31` (status applied, target same delivery.py) read the same guide under its old "four keys" framing and proposed exactly one DORA throughput metric — change lead time as `integrated_at - committer time` with `integrated_at` stored at integration. It noted limits (commit-to-integrate vs submit-to-integrate labelling, single-service scope, measurement-not-target) and predicted UNKNOWN for pre-existing records. This REVISIT reads the current five-metric text (fetched 2026-11-02, last updated 2026-01-05) and the companion history that explains the MTTR→Failed Deployment Recovery Time rename, and proposes the *complementary instability* half the guide requires be kept in tension with throughput — change fail rate, failed deployment recovery time, deployment frequency, and deployment rework rate — as pure, informational counters over the delivery record set. It does not repeat the lead-time definition or redo its commit-time method; it names the complementary metric family and the tension/pitfall the guide warns about when lead time is measured alone. The prior proposal remains complementary and is not superseded.

## What this plant already does

- `delivery.py` records no time. `integrate()` sets `phase="integrated"` and `integrated=<sha>` but no instant; blocked transitions (`record.update(phase="blocked", reason=str(exc))` in the `_push` failure path and recovery paths) similarly store no instant. `line(home)` renders only open candidates (`phase != done`) as `identity author= owner phase= head[:12]: reason`; closed rows leave no duration or rate. Duration therefore unknowable from the record alone.
- `_sync` projects facts to `task_state` (task-add / waiting / done, `[task-event]` signals for `integrate-` and `delivery-*-updated` author retry) but not to a duration or rate. The chat tape delivery lines observed 2026-11-02 contain no per-delivery elapsed time; CI freshness (`release_readiness` 0–300 s) is a gate, not a throughput measure. No file exposes change fail rate or recovery time.
- Throughput exists as commit timestamps in git (`git show -s --format=%cI <head>` readable via `_git`) but is unread by delivery code. Instability has a partial analogue in `phase="blocked"` and in `transition` counts (re-tries) and in `author_retry_wait_sequence` / `delivery-*-updated` retry events, but none are counted as a rate. A delivery that is blocked and later integrated after rebase (e.g. a candidate whose main advanced and was rebased) currently shows only its last `phase="integrated"`; the blocked episode is overwritten unless history is counted.
- DORA scoping ("one application or service at a time") is met by construction: one repository, one `main`. The pitfall the guide warns about — Goodhart's law and "one metric to rule them all" — would be reproduced if the plant added only the prior lead-time metric and then treated it as a target: it would incentivise risky fast integrates without observing instability. The guide explicitly says to keep metrics in tension and to share them across dev/ops/release rather than siloing.
- No DORA metric vocabulary appears in `src/mishe_tauftauf/delivery.py`; the sweep keywords (`delivery`, `release`, `coordination`) occur as occurrence only.

## What is new here

DORA's five-metric model is not five independent numbers — it is a throughput-vs-instability tension with explicit cautions. The current guide (Jan 2026) replaces MTTR with **Failed Deployment Recovery Time** and adds **Deployment rework rate** alongside **Change fail rate**, keeping **Change lead time** and **Deployment frequency** on the throughput side. The companion history shows why: the original 2014–2015 model mixed MTTR and change fail rate under "stability", and the rename marks a shift from "mean time to recover from any incident" to "time to recover from a *deployment that required intervention*". For this plant, that is the precise distinction between "time to recover the delivery attempt that became blocked" and any broader incident.

Transfer to this plant's coordination path:

- **Deployment** = a delivery record's integration attempt (`submit` → `integrate` → `finish`). It is the plant's unit that reaches `main`.
- **Change fail rate** = `blocked / attempted` over a window, where `attempted` counts distinct identities that reached at least `integrate` or `blocked` (or all submitted, owner chooses; the definition must be fixed in the helper's docstring and held stable). In the current live set (12 identities) that ratio is 1/12 by last-phase counting, higher if episodes are counted — which is exactly why the helper must declare its denominator.
- **Failed deployment recovery time** = `integrated_at - blocked_at` for identities that were blocked and later integrated (rebase path), else UNKNOWN until recovered. This is MTTR's renamed form, scoped to the delivery that failed — matching `integrate()`'s own failure path that writes `phase="blocked"` after a failed `_push`.
- **Deployment frequency** = `attempted / window_days` (DORA throughput complement). Together with lead time (prior proposal), throughput is two-dimensional.
- **Deployment rework rate** = `unplanned re-tries / planned attempts` ≈ `records where transition>1 or where phase cycled blocked→integrated / attempted` (or where `delivery-*-updated` author retry was emitted). This counts the rebases/re-submits the guide calls "unplanned but happen as a result of an incident (the blocked push / CI fail)".

In DORA's framing these are lagging indicators of the delivery practice and leading indicators of organisational performance — for this plant, the lagging signal is delivery hygiene; the guide's correlation claim ("Top performers do well across all five metrics, and low performers do poorly") becomes testable by watching throughput and instability jointly rather than gaming one.

Why this is not a duplicate of the predecessor: the predecessor measured only throughput duration (one interval, one new key `integrated_at`, git commit time source). This adds the orthogonal instability axis the guide says must be held in tension with throughput, the windowed rate + frequency view, and the MTTR-rename distinction that reframes recovery as per-deployment rather than per-incident. Either metric alone would invite Goodhart per the guide's pitfall; together they satisfy "identify multiple metrics, including some with a healthy amount of tension".

## Application

target: src/mishe_tauftauf/delivery.py

Proposal for genome (owner; pushes `main`, checks exact-SHA CI and live consumers, so delivery semantics are theirs to author or decline). The predecessor's `integrated_at` proposal remains complementary; this proposal adds the instability side without changing any gate. All helpers are pure and informational: no new HOLD, no new integration block, no tape event.

1. **Persist blocked time (optional, additive).** In the same pattern as the predecessor's `integrated_at`, when a path sets `phase="blocked"` (currently `record.update(phase="blocked", reason=str(exc))` after failed `_push`, and any other blocked-writing path), also store `record["blocked_at"] = <UTC now ISO8601>` in the same atomic `_save` that writes the phase. It is an optional key so the 12 existing records remain valid; older blocked rows read UNKNOWN for recovery time.

2. **Add pure, deterministic metric helpers (no I/O except an optional loader wrapper).** Keep computation separate from the gate:

   - `delivery_counts(records: list[dict], *, since: datetime|None) -> dict` returning `{"attempted": int, "succeeded": int, "failed": int, "rework": int}` where `attempted` counts records with `phase in {"blocked","integrated","done"}` whose `blocked_at` or `integrated_at` or `head` commit time falls in `since` window (or calendar window; owner fixes one definition and documents it), `failed` counts `phase=="blocked"` last-phase plus optionally episodes where `blocked_at` exists, `rework` counts `transition>1` or records that ever had `blocked_at` then `integrated_at`.
   - `change_fail_rate(records, *, since) -> float|None` returning `failed/attempted` or `None` when `attempted==0`; caller formats as `UNKNOWN (no attempts in window)` rather than 0.
   - `deployment_frequency(records, *, since, window_days) -> float|None` returning `attempted / window_days` (deployments per day) or per week if owner prefers; window is the same `since` used above.
   - `failed_deployment_recovery_seconds(record: dict) -> int|None` returning `integrated_at - blocked_at` seconds when both keys exist and `integrated_at > blocked_at`, else `None` (UNKNOWN). A batch helper `median_failed_recovery_seconds(records)` over recovered identities in the window is the plant analogue of DORA's Failed Deployment Recovery Time statistic.
   - `deployment_rework_rate(records, *, since) -> float|None` returning `rework/attempted` or None.

   Each helper is unit-testable from in-memory dicts without reading git or `SITE` files; age/window arithmetic uses `datetime.fromisoformat(... replace Z)`.

3. **Surface as an informational footer, not a gate.** Extend `line(home)` (or add `delivery_metrics_line(home, *, since_days=30)`) to print, after the per-candidate rows, an instability summary:
   `METRICS window=30d attempted=N succeeded=M failed=F rework=R rate=fail_rate rework_rate=rework_rate freq=FREQ/d p50_recovery=RECOVERY`
   When no attempts in window, read `METRICS window=30d UNKNOWN (no attempts)`. When older records lack `blocked_at`/`integrated_at`, individual recovery reads `UNKNOWN (blocked_at missing)` and does not count toward the median. The summary never gates `integrate`/`finish`; it only reports.

Tests (deterministic, no network, no wall I/O in helpers):

- Golden records with `blocked_at` and `integrated_at` 600 s apart: `failed_deployment_recovery_seconds == 600`, `change_fail_rate` counts that record as attempted+failed, median recovery counts it.
- Window with 4 attempted (3 integrated/done, 1 blocked) and 1 rework (transition 2): `change_fail_rate == 0.25`, `deployment_rework_rate == 0.25`, `deployment_frequency == 4/30`.
- Record without `blocked_at`: recovery `None`, row reads UNKNOWN with reason `blocked_at missing`; rate denominator unchanged.
- Blocked record never recovered within window: recovery `None`, rate denominator includes it, median excludes it.
- Empty window: all rates `None`, summary reads UNKNOWN, no exception, no zero-as-healthy.
- Backwards clock (`integrated_at < blocked_at`): helper returns `None` / UNKNOWN, never negative.

Live verification after activation (running-code change, no new gate):

- Run `delivery line` (or `delivery_metrics_line --since 30d`) against the live `.mishe-tauftauf/deliveries/` after the blocked helper is active: the 1 blocked record still shows UNKNOWN recovery (no `blocked_at` yet); the 30-day summary shows `attempted` and `change_fail_rate` consistent with current phases (today dominated by pre-key records). Confirm no new HOLD or integration gate.
- Exercise a controlled failure near a future delivery (separate candidate, not the plant's HEAD): submit a branch, force a blocked integration (e.g. divergent main or failing `_push` lease), observe `blocked_at` written and metric summary increments `failed` and `change_fail_rate` for that window; then rebase and integrate, observe `integrated_at` written and `failed_deployment_recovery_seconds` becomes a positive number and median updates. Verify the recovery interval equals wall-clock within skew and that throughput lead time (predecessor) and instability rates move in expected tension.
- Confirm Goodhart guard: show that a fast lead-time integrate still increments `attempted` and, if it fails, increments `failed` and lowers stability — demonstrating the tension the guide requires.

Limits. The mapping from DORA "deployment" to delivery record is an analogy the owner must ratify; the guide's "one service at a time" scoping is met but the `since` window and denominator (attempt = submitted vs attempted-integrate) are plant choices the guide does not prescribe. Recovery time scoped to the delivery's blocked→integrated cycle is narrower than operational MTTR (rolled-back site, host loss) and does not measure consumer rollout convergence — that is `release_readiness`/`wall_patch --observe` work, out of scope here. The guide warns against setting metrics as goals; these helpers must remain measurement, not targets, and must not be read by `integrate()`/`finish()` to decide admission. The history page's evolution narrative is provenance for the rename, not an independent metric definition.

