---
field: engine-model-routing-and-usage
angle: transfer
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/engine_policy.py
---

# Circuit breaker transfer: failure-count open with timed half-open probe for engine alias

## What we read

- Fowler, Martin, "Circuit Breaker", 6 March 2014, https://martinfowler.com/bliki/CircuitBreaker.html (fetched 2026-11-02, HTML 22433 B sha256 73239948ec03887430a4d139e384e94bc640fec894fb1dce53b56abe579c5da4). Primary source is Fowler's bliki page; Nygard, Michael, *Release It!* (Pragmatic Bookshelf, 2007) is cited inside as the pattern's popularization. Retained at /tmp/circuit-breaker.html.
- Pagination/sections below are as printed on the page; verbatim quotations via raw HTML strip of the fetched article body (7646 B plain-text after tag strip). Headings as rendered: the page uses prose sections with inline code labels `class CircuitBreaker...` / `class ResetCircuitBreaker...`.
- Points used (quoted verbatim from that strip):
  - Framing: "It's common for software systems to make remote calls to software running in different processes, probably on different machines across a network. One of the big differences between in-memory calls and remote calls is that remote calls can fail, or hang without a response until some timeout limit is reached. What's worse if you have many callers on a unresponsive supplier, then you can run out of critical resources leading to cascading failures across multiple systems."
  - Basic breaker: "You wrap a protected function call in a circuit breaker object, which monitors for failures. Once the failures reach a certain threshold, the circuit breaker trips, and all further calls to the circuit breaker return with an error, without the protected call being made at all. Usually you'll also want some kind of monitor alert if the circuit breaker trips."
  - Simple state: `cb = CircuitBreaker.new {|arg| @supplier.func arg}` with `attr_accessor :invocation_timeout, :failure_threshold, :monitor; @invocation_timeout = 0.01; @failure_threshold = 5; reset` and `def state; (@failure_count >= @failure_threshold) ? :open : :closed; end` — closed calls through `do_call` via `Timeout::timeout(@invocation_timeout)`, success `reset` (failure_count=0), timeout `record_failure` (`@failure_count += 1; @monitor.alert(:open_circuit) if :open == state`).
  - Self-resetting third state: "This simple circuit breaker avoids making the protected call when the circuit is open, but would need an external intervention to reset it when things are well again. ... We can implement this self-resetting behavior by trying the protected call again after a suitable interval, and resetting the breaker should it succeed." -> `ResetCircuitBreaker` adds `@reset_timeout = 0.1; @last_failure_time = nil` and `def state; case; when (@failure_count >= @failure_threshold) && (Time.now - @last_failure_time) > @reset_timeout then :half_open; when (@failure_count >= @failure_threshold) then :open; else :closed; end; end` with `when :closed, :half_open` -> trial call that either `reset` on success or `record_failure` (`@last_failure_time = Time.now`) plus `@monitor.alert`.
  - Parameterization note: "Often they will protect against a range of errors ... Not all errors should trip the circuit, some should reflect normal failures"; "A more sophisticated approach might look at frequency of errors, tripping once you get, say, a 50% failure rate. You might also have different thresholds for different errors, such as a threshold of 10 for timeouts but 3 for connection failures."
  - Thread-pool/bulkhead alternative: "With lots of traffic, you can have problems with many calls just waiting for the initial timeout. ... By drawing these threads from a thread pool, you can arrange for the circuit to break when the thread pool is exhausted." and "A common technique ... is to put all requests on a queue ... In this case the circuit breaks when the queue fills up."
  - Resilience scope: "On their own, circuit breakers help reduce resources tied up in operations which are likely to fail. You avoid waiting on timeouts for the client, and a broken circuit avoids putting load on a struggling server." plus monitoring/ops: "Any change in breaker state should be logged and breakers should reveal details of their state ... Operations staff should be able to trip or reset breakers."
  - Limit retained: page ends with bulkheads section truncated in fetch; the mechanism above is the self-contained breaker without the further bulkheads detail.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:338-558` `decide()` is pure per-wake quota-window classification via `_classify_account()` : `healthy` if `0 < eff < reserve_pct` unavailable, `reserve` if `0 < eff < 0.15`, `depleted` if `eff <= 0`, `hold` on `UNKNOWN/STALE/UNATTRIBUTED/NOT_CONFIGURED`; plus `_effective_reserved()` offset. Provider-bound allowlist via `_route_compatible` / `_is_route_in_allowlist`, hysteresis via `next_reconsider_ms = now_ms + min_hold_ms (600000)`, binding-reset ISO for queue, no failure-count memory.
- No breaker memory across wakes: no per-alias `failure_count`, no `failure_threshold`, no `invocation_timeout`, no `last_failure_time`, no `reset_timeout`, no `half_open` trial. Once UNKNOWN/hold resolves, the alias is retried next wake at full rate; a repeatedly timing-out supplier is probed every wake without back-off.
- Fallback is ordered list scan (same-provider first, then list order) with no load-aware randomization; queue/hold are policy outputs but not breaker states observable to monitoring or ops override.
- Monitoring path is separate (`engine_usage` last-good snapshot, `discovery` scans) — breaker-style `monitor.alert(:open_circuit/:reset_circuit)` and state-reveal for deeper monitoring per Fowler p.4 is not embodied.

## What is new here

Circuit breaker is not quota-threshold admission; it is failure-rate admission with memory and timed self-reset. Transferred to engine routing, it adds three mechanisms this plant lacks:

1. **Failure-count open without calling the supplier.** Fowler's `failure_threshold = 5` with `reset` on success maps to per-alias consecutive failure count that trips to `open` and returns error without inference dispatch — distinct from `depleted` (quota zero) or `hold` (snapshot missing). Our `depleted` is resource-exhaustion; `open` is supplier-unresponsiveness. Today a quota-healthy but hanging alias is `healthy` and will be selected; a breaker would have opened after N timeouts and avoided the hang and queue exhaustion.

2. **Timed half-open single probe instead of every-wake retry.** `ResetCircuitBreaker` re-enters `:half_open` only when `Time.now - @last_failure_time > @reset_timeout (0.1s scaled)` and allows one trial call; success resets, failure restarts timeout. Our `next_reconsider_ms = now+600s` for hold/queue is fixed and applies to quota windows, not to unresponsive-supplier back-off. Half-open caps probe load to one trial per timeout and proves recovery before restoring full traffic — preventing the cascade Fowler warns about ("many callers on a unresponsive supplier ... cascading failures").

3. **Breaker as queue/thread-pool guard and monitor point.** Fowler's bulkhead note ("circuit to break when the thread pool is exhausted" / "when the queue fills up") plus "Any change in breaker state should be logged ... Operations staff should be able to trip or reset" gives an observable failure-mode signal that `engine_policy` does not emit. A per-alias breaker state (`closed/open/half_open`, failure count, last failure time) is the place to surface "supplier struggling" independent of `remaining_fraction`, with different thresholds per error class (Fowler's 50% rate or 10 timeouts vs 3 connection failures).

Predecessor distinctness: 2026-10-31 transfer = Mitzenmacher 2000 abstract + 2001 TPDS 12(10) `tpds2001.pdf` power-of-two-choices / stale-queue herd (randomized two-choice, herd when many pick least-loaded on old snapshot), discarded because live `default` role + unwired `reservations=None` removed herd precondition. This = Fowler 2014 bliki HTML 22433 B sha 732399... + Nygard 2007 citation, circuit-breaker object `failure_threshold 5 / invocation_timeout 0.01 / reset_timeout 0.1`, closed/open/half_open + monitor.alert/queue-break mechanism, different venue/year/pages/equations/code, transfer family disjoint; only shared target file.

## Application

target: src/mishe_tauftauf/engine_policy.py

Proposed to genome as additive header/docs note in `engine_policy.py` (no behavioral change in this review), citing Fowler 2014 bliki HTML 22433 B sha 732399... at https://martinfowler.com/bliki/CircuitBreaker.html (Nygard Release It! via that page), carrying the three gated transfer claims verbatim:

- Distinguish quota admission (`healthy/reserve/depleted/hold` on `remaining_fraction` + `_effective_reserved`) from breaker admission (`closed/open/half_open` on consecutive failures/timeouts per alias). Do not conflate depleted quota with open circuit; breaker trips on failure count/rate, not on quota.
- Gating for any future breaker lane (all required before behavior lands): per-alias persisted failure count with `failure_threshold` (e.g. Fowler's 5) resetting on success, `invocation_timeout` for hang detection, `reset_timeout` + `last_failure_time` for half-open single probe, `monitor.alert(:open_circuit/:reset_circuit)` state logging, and per-error-class thresholds (Fowler's 50% rate / 10 vs 3) plus thread-pool/queue guard (`break when queue fills`). Prove half-open caps probes to one per timeout before full restore.
- Observability/ops gate: breaker state (`closed/open/half_open`, failure_count, last_failure_time) is logged and revealable for monitoring, and trippable/resettable by ops, independent of quota window. No change to constants, allowlist ordering, or `next_reconsider_ms` in this review; measurement gap retained.

## Limits

- Fowler's numbers (0.01s timeout, 5 failures, 0.1s reset) are illustrative for in-process timeouts, not sized for LLM inference hangs (seconds to minutes) or for this plant's quota windows; any adopted threshold/timeout must be re-derived from live engine tail/timeout distribution, not copied.
- The page's thread-pool/queue guard assumes shared resource exhaustion in the caller; this plant's single dispatch via `inference_effects.py` does not have that pool — mapping is conceptual until concurrency model is fixed.
- Frequency-based tripping ("50% failure rate") and differentiated thresholds per error class need a defined error taxonomy (timeout vs connection vs quota) this plant does not yet emit.
- This review does not edit `engine_policy.py` or add tests; the owning role (genome) lands the guard. Bulkheads section after "On their own..." was truncated in fetch and is not relied upon.
