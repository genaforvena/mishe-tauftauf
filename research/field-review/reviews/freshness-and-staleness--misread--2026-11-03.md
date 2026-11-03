---
field: freshness-and-staleness
angle: misread
date: 2026-11-03
status: applied
owner: senses
target: checks/coverage-window-expiry.py
---

# Freshness: wall-clock subtraction is not happened-before

## What we read

**Time, Clocks, and the Ordering of Events in a Distributed System** — Leslie Lamport, *Communications of the ACM* 21(7), July 1978, pp. 558–565. <https://lamport.azurewebsites.net/pubs/time-clocks.pdf> — full text read (8 pages; local copy /tmp/lamport-time-clocks.pdf 854562 B via curl, extracted with `pdftotext` to /tmp/lamport.txt 1038 lines). The passages that bear on this plant's freshness check:

1. **The happened-before partial order (§The Partial Ordering, p. 559).** Definition: `a → b` is the smallest relation with (1) same-process order, (2) send → receive of the same message, (3) transitivity. "Two distinct events a and b are said to be concurrent if a ↛ b and b ↛ a." And: "a → b means that it is possible for event a to causally affect event b." A single timestamp comparison does not establish this relation; two timestamps with no causal link are concurrent by definition.

2. **The Clock Condition and its implementation (pp. 560–561).** Correctness is `if a → b then C(a) < C(b)` (stronger converse not required). Sufficient conditions C1 (per-process monotonic) and C2 (send timestamp < receive). Realized by IR1 (increment between successive events) and IR2 (message carries `Tm = Ci(a)`; on receipt set `Ci ≥ max(present, Tm)`). Without IR1/IR2, clock values do not satisfy the condition at all.

3. **Physical clocks, the anomalous-behavior inequality (§Physical Clocks, p. 563).** Let `Ci(t)` be the reading of clock `i` at physical time `t`. PC1: bounded drift `|dCi/dt − 1| < κ, κ ≪ 1` (crystal `κ ≤ 10⁻⁶`). PC2: bounded skew `|Ci(t) − Cj(t)| < ε` for all `i,j,t`. Let `μ` be less than the shortest transmission time for interprocess messages. Then anomalous behavior (clock order disagreeing with observer's perceived order) is impossible only if `ε/(1 − κ) ≤ μ`. The theorem that follows bounds achievable `ε = d(2κτ + ξ)` for a strongly connected graph of diameter `d` with message period `τ` and unpredictable delay `< ξ` under IR1'/IR2' (set `Cj(t') = max(Cj(t'−0), Tm + μm)`). The bound is surprisingly difficult to prove (Appendix, p. 564) and assumes clocks are never set backwards.

This is the second founding paper for the field's misread angle. Predecessor 2026-10-22 (Gray & Cheriton 1989) read the lease as a server-issued term and found wall.tick's hard 30 s term invented per reader. Predecessor 2026-11-01 (Kleppmann 2016) read the lease gate as liveness not safety and found the wake number at `seed.settle` is the fencing token. Lamport answers the question both left open: what "fresh" must mean before either a lease or a fence can be judged.

## What this plant already does

*The plant knows one correct rendering of Lamport, for scans, and does not use it for freshness.* `src/mishe_tauftauf/scan_freshness.py:1-4` header: "UTC is diagnostic only and never establishes recency." `age_bounds()` at `:107-121` returns a `(min,max)` nanosecond pair bounded by acquisition endpoints; `classify_age()` at `:124-148` answers `recent`/`stale`/`unknown` where `unknown` is exactly "bounds straddle threshold" — the plant's implemented vocabulary for Lamport's concurrent case. `_compatible()` at `:62` refuses to compare across incompatible `boot_id`/`time_namespace`/clock domains, returning `None` rather than a point age. `discovery.py:8098-8109` uses this path via `scan_freshness.endpoint()`.

*Two other freshness verdicts use the misread shape.* `checks/coverage-window-expiry.py:128-136` `_fresh(path, max_age, now)`:

```python
payload = _read_json(path)
generated = _time(payload.get("generated_at") or payload.get("created"))
age = (now - generated).total_seconds()
if age > max_age: raise Undecidable("%s is stale (%ds old)" % (path.name, int(age)))
```

`now` is `datetime.now(timezone.utc)` at the checker; `generated` is the publisher's wall-clock instant. The subtraction assumes PC2 holds between publisher and checker — that the two readings of UTC are within `ε` with `ε/(1−κ) ≤ μ` — without ever establishing `κ`, `ε` or `μ`. The same shape is `src/mishe_tauftauf/observations.py:363` `pane_lease_age(lease)` parsing the pane footer's `-- pane live <utc>` and subtracting from the observer's `now`. Both are point-clock comparisons across processes (publisher vs checker, renderer vs reader) with no happened-before edge and no interval.

*The check already publishes the island where Lamport's picture would apply, and does not use it.* Since the revisit `freshness--recent--2026-11-03` (Li et al. 2026), `checks/coverage-window-expiry.py:240-281` prints Yates windowed AoI beside the point check — `current/mean_age/mean_peak` plus unfolded `mean_age_unfolded`/`mean_transport` — sourced from `scan_window_aoi` on the receipt. The AoI is computed from prior `created` stamps with `now = receipt.created` (one causal chain), but the staleness verdict that gates the check is still the point `age > max_age` between receipt time and checker time — the cross-process comparison Lamport says needs `PC2`.

## What is new here

**The misread: the plant treats `now − generated` as establishing freshness, when Lamport shows freshness without happened-before is undecidable and wall-clock comparison without bounded skew is anomalous.**

Three consequences are measurable in this repository now:

1. **A point age across processes is Lamport-concurrent, not ordered.** `generated` (publisher event) and `now` (checker event) have no IR2 message edge in `checks/coverage-window-expiry.py`; the checker never received a `Tm` from the publisher, it read a file. By the p. 559 definition they are concurrent unless a send→receive chain connects them — which a file read on the same host after publication is, but across hosts in `coordination/site_sync.py` it is not, and the code does not distinguish. The `age < 0` branch ("remote clock is ahead") that `site_sync._leases` folds into the same `stale` string as expiry is exactly the anomalous case Lamport's inequality is meant to prevent.

2. **The plant has one domain where `PC2` is plausible and one where it is not, and the check does not name which it is in.** `scan_freshness` compares BOOTTIME intervals within one boot domain and refuses cross-domain comparison (`_compatible` at `:62` → `None`). `coverage-window-expiry` compares UTC instants from two wall clocks (publisher vs checker) that may be the same host's clock (where `ε` is small and `μ` is filesystem latency) or two hosts' clocks (android-body vs core, where `ε` is NTP skew). The file's `DEFAULT_MAX_AGE = 900.0` and call sites with `max_age 900` are an order of magnitude above any plausible `ε` on one host, so the misread is harmless today on one host — but the code publishes the same `Undecidable("is stale")` for both cases without stating the `ε` assumption, and a future caller that lowers `max_age` toward `ε` will cross Lamport's `ε/(1−κ) ≤ μ` boundary without warning.

3. **The correct primitive already in the tree goes unused at the gate.** `scan_freshness.classify_age` returns `unknown` when `bounds[0] ≤ threshold < bounds[1]` — the honest answer for a comparison that straddles the decision threshold or crosses domains. `checks/coverage-window-expiry.py:135` returns only `fresh` (return payload) or `stale` (`Undecidable` with "is stale") or "carries no readable stamp" — it never returns `unknown` for a straddling or cross-domain case, even though its own `Undecidable` exception is the plant's `unknown` vocabulary (`docs/how-it-works.md:576` — missing/stale/unavailable → UNKNOWN). The Li et al. 2026 revisit added `sampling_frequency_threshold_hint` to `scan_freshness.py` to name where extra sampling stops buying effectiveness; Lamport adds the dual: where a timestamp comparison stops being decidable at all.

**Limits the reading keeps.** Lamport's theorem assumes a strongly connected graph, periodic messages every `τ`, bounded unpredictable delay `ξ`, and clocks never set backwards — none of which the plant's file-based publication satisfies; the file channel has no `Tm + μm` timestamp rule and no `κ` measurement. The `ε = d(2κτ + ξ)` bound is not portable to a site that reads a JSON file. What transfers is the *condition* (`ε/(1−κ) ≤ μ`) and the *distinction* (logical ordering vs physical clock agreement), not the numeric bound. This review does not propose implementing Lamport clocks or NTP discipline; it proposes that the file that decides staleness name the condition under which its point comparison is valid, and keep the `unknown` answer for the case where it is not — the same discipline `scan_freshness` already follows.

## Application

target: checks/coverage-window-expiry.py

Amend the module that decides "stale" by point subtraction to name Lamport's anomalous-behavior condition and to keep the third verdict `unknown` for the concurrent case. The change is additive, header + docstring, beside the Yates AoI note already at `:240`, and does not change the `age > max_age → Undecidable("is stale")` contract that existing callers and tests rely on:

1. **Header note beside the existing AoI comment (`:240-246`).** Add a paragraph: point `age = now − generated` is Lamport-concurrent without an explicit `ε/(1−κ) ≤ μ` bound (Lamport 1978 §Physical Clocks, p. 563); on one host with `DEFAULT_MAX_AGE 900 s` the bound is easily met (`ε` is local clock skew, `μ` is filesystem latency), but across hosts `age < 0` (publisher ahead of checker) is not `stale` — it is `unknown` in `scan_freshness.classify_age`'s sense. Reference `src/mishe_tauftauf/scan_freshness.py:62` `_compatible` / `:124-148` `classify_age` as the interval alternative when the receipt carries `acquisition` bounds.

2. **`_fresh` docstring.** State the validity condition: the `now − generated` comparison is valid only when publisher and checker share a clock domain with bounded skew `ε ≪ max_age`; otherwise the correct answer is `Undecidable` with `unknown` vocabulary rather than `stale`. When the receipt carries `acquisition`/`bounds_ns` (scan receipts do), prefer `scan_freshness.age_bounds` + `classify_age` interval check — additive helper ` _fresh_interval` stub or comment — rather than UTC subtraction. Keep `_fresh`'s current point logic as the fallback path for receipts without bounds (e.g. `checks/initiative.json`).

3. **No behavioral change now.** `Undecidable("is stale")` stays `stale`; a future `unknown` for `age < 0` or straddling bounds is a separate, additive path. The scan_window_aoi print path at `:248-281` stays display-only (no verdict consumes it), as documented.

**Acceptance.**

* `python3 research/field-review/field_review.py check` stays PASS — target file exists at `HEAD:checks/coverage-window-expiry.py` (verified `git cat-file -t HEAD:checks/coverage-window-expiry.py == blob`).
* `checks/coverage-window-expiry.py` header mentions Lamport 1978, `ε/(1−κ) ≤ μ`, and `scan_freshness.classify_age` / `unknown`; `_fresh` docstring states the single-host vs cross-host validity condition.
* Existing tests for `checks/coverage-window-expiry.py` (if any) and `tests/test_scan_freshness.py` keep passing; `python tools/check_parsing.py` adds no new boundary debt (`checks/coverage-window-expiry.py` is already parsed via the JSON-boundary pattern, no new parse site).
* A receipt with `generated` 10 s in the future (`age = −10 s`) read through the documented path must be described as `unknown` (clock disagreement), not `stale` (expired) — the doc states this; the future additive helper implements it.

**Ownership.** `checks/coverage-window-expiry.py` is a detector, not a sensor probe; the freshness field's plant part is `senses, scanners, pane leases, scan_freshness` and the detector is routed to `senses` as the freshness owner, with `health` as resolver for the scheduled check `checks/coverage-window-expiry.py`. This role is read-only: the review is input to that decision, not its execution.

**One thing this review does not ask for.** Lamport's full construction — logical clocks with IR1/IR2 per message, or the `ε = d(2κτ + ξ)` physical-clock sync algorithm — is not proposed for this file channel. The file is a JSON publication, not a message with `Tm + μm`. The transfer is the *misread* (timestamp order is not happened-before, and wall-clock agreement without a bound is anomalous), not the *mechanism*.

