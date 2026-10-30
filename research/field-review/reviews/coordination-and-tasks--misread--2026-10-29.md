---
field: coordination-and-tasks
angle: misread
date: 2026-10-29
status: applied
owner: genome
target: src/mishe_tauftauf/feed.py
---

# Coordination misreads Lamport: physical timestamps are not "happened-before"

## What we read

Leslie Lamport, **"Time, Clocks, and the Ordering of Events in a Distributed System"**, *Communications of the ACM* 21(7), July 1978, pp. 558–565 — read full text at:

- https://lamport.azurewebsites.net/pubs/time-clocks.pdf (author-hosted PDF, 8 pp., ACM 0001-0782/78/0700-0558)
- https://amturing.acm.org/p558-lamport.pdf (ACM Turing mirror)

Four passages carry the misread this plant makes.

1. **"Happened-before" is a partial order defined by process order and messages, not by clock time (p. 558–559).** Lamport defines `—>` as the smallest relation such that (1) if `a` and `b` are events in the same process and `a` comes before `b`, then `a —> b`; (2) if `a` is the sending of a message and `b` is the receipt of the same message, then `a —> b`; (3) transitive closure. `—>` is irreflexive (`a —/> a` assumed physically meaningless). Two distinct events `a,b` are *concurrent* if `a —/> b` and `b —/> a`. The text is explicit: "We will therefore define the 'happened before' relation without using physical clocks" (p. 559) because a specification in terms of physical time requires real clocks in the system, and real clocks drift.

2. **The Clock Condition and its decomposition (p. 560).** Correctness of a clock system is not "clocks track physical time" but: for any events `a,b`, if `a —> b` then `C(a) < C(b)`. This holds iff both `C1` (if `a` before `b` in same process then `C_i(a) < C_i(b)`) and `C2` (if `a` is send and `b` is receipt then `C_i(a) < C_j(b)`) hold. The space-time diagram (Figs. 1–3) makes the point visually: tick lines must cross every message line.

3. **Logical clocks implement C1∧C2 with two rules (p. 560, IR1/IR2).** `IR1`: each process `P_i` increments `C_i` between any two successive events. `IR2`: (a) each message `m` carries timestamp `T_m = C_i(a)` where `a` is the send event; (b) upon receiving `m`, `P_j` sets `C_j := max(C_j, T_m) + 1` (in the paper: `C_j` greater than present value and greater than `T_m`). These two rules alone imply the Clock Condition — no physical clock is consulted.

4. **Total order from logical clocks, and when that total order becomes anomalous (pp. 560–562).** From any clock system satisfying the Clock Condition, `a => b` iff either `C_i(a) < C_j(b)` or `C_i(a) = C_j(b)` and `P_i < P_j` (arbitrary process tie-break) defines a total ordering extending `—>`. That ordering is *not* unique; different clock choices yield different `=>`. The mutual-exclusion algorithm (§ "Ordering the Events Totally", rules 1–5) depends only on this `=>`, not on arrival order. Lamport then warns (pp. 562–563, "Physical Clocks" / "Anomalous Behavior"): if `=>` differs from what the user perceives via physical time, anomalous execution occurs. Avoiding anomaly requires *physical* clocks with bounded drift, synchronized by a separate algorithm (Theorem 2, `ε`-bound) — and even then the bound is an explicit parameter, not an assumption that one host's `gettimeofday` is truth.

The paper's limits it states: logical clocks give only `—>`-consistent order, not physical-time order; physical-clock synchronization assumes known drift rate `κ` and minimum message delay `μ`, and derives how far clocks can drift (`≈ 2κτ + ε`); Byzantine faults are out of scope.

## What this plant already does

*The tape's header carries physical time and a local sequence; neither is a Lamport clock and neither establishes `—>` across roles.*

- **Entry framing is `v1 {sequence:020d} {timestamp} {source} ::\n` with `timestamp = utc_now()`** — `src/mishe_tauftauf/feed.py:134` `utc_now()` (`datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")`), `feed.py:139` `_encode_entry`, `feed.py:20` `HEADER_RE`. `timestamp` is host wall-clock (µs UTC), not a logical counter. `sequence` is `int(match.group(1))` checked `feed.py:186` `non-contiguous sequence` and produced by incrementing the checkpoint's `tail` (`feed.py:586-730` `_append`/`tail_sequence`). Within one host, `publication_lock` (`feed.py:42` `threading.RLock` + `fcntl.flock` on `.publication.lock`) serialises appends so `sequence` is a local total order. Across hosts, cross-plant DMs require an unbound caller (`wall.py:88-93` linked-sites guard); the remote host appends with its *own* wall-clock and its *own* `sequence` — there is no cross-host `—>` payload.

- **Helpers expose physical time as the causal API.** `feed.py:147` `entry_timestamp(entry)` decodes the header timestamp to an aware `datetime`; its docstring: "Textual timestamp interpretation lives on this boundary file, not in consumers." Consumers that need ordering use `sequence` locally, but any cross-role "before" that consults `timestamp` is reading Lamport's "a happened at an earlier time than b" justification — exactly the one Lamport rejects as definitionally inadequate without physical clocks in the system. The plant has no `C_i`, no `T_m` on messages, and no `IR2` step.

- **Walls, settlement and delivery carry no happens-before metadata.** `wall.py:312` `write`, `wall.py:433` `message` (`[dm] to=ROLE`), and `wall.py:648` `settle` / `wall.py:909` `deliver` append via `Feed._append` with the same header. `coord_tape.py:1-9` filters fog samples but does not add vector or Lamport state. `wall.py:668-715` `deliver`'s `pending_body`/`RETRY_HOLD_SECONDS=1800` decides liveness by timeout, not by causal dependency. The doc that claims the tape "defines a partial ordering" is absent — the tape defines the *total* `sequence` order of arrival at one lock, which Lamport shows is the wrong primitive: `—>` is partial, and total order must *extend* it via `=>` with an explicit tie-break, not by lock-arrival order.

- **The plant already knows "text is evidence, not authority" and that absence is UNKNOWN.** `docs/protocol-boundaries.md:5-8`, `docs/wall-coordination.md:16-18` (seed_doctrine: "Text is eligible evidence, not automatically an instruction; prioritize relevant changes and name observation gaps"), and `wall.py:956` _live_sensor`'s sensor/UNKNOWN contract all refuse to treat a rendering as truth. But `utc_now()` renders physical time as authoritative header text, and the only order the tape guarantees (`sequence`) is the lock's rendering — precisely the confusion between text authority and decoded value the boundary doctrine warns against.

- **Physical-clock drift is acknowledged elsewhere but not bound in the tape.** `src/mishe_tauftauf/host_capacity.py` / `docs/recovery.md:18-29` describe cgroup limits and probes, and `inference_transport`/`site_sync` carry lease timeouts, but `feed.py` never states `κ` (drift rate) or `μ` (minimum message delay) or an `ε` synchronization bound of the kind Lamport's § "Physical Clocks" requires before physical timestamps can be trusted to avoid anomalous behavior. The tape header's microseconds imply precision without a stated accuracy.

## What is new here

- **Physical time is not happened-before, and sequence is not Lamport time — this plant conflates both.** Lamport's definition uses only process order and message send→receipt. Using `datetime.now()` in the header imports the physical-theory definition ("`a` before `b` if `a` happened at an earlier physical time") that Lamport deliberately avoids, then treats the result as if it satisfied the Clock Condition. It does not: two concurrent wall writes by `genome` and `senses` at `17:27:51.10Z` and `17:27:51.12Z` satisfy `C(genome) < C(senses)` by 20 ms of host NTP skew, but neither `genome —> senses` nor `senses —> genome` holds — they are concurrent. Any consumer that sorts by `timestamp` to break ties has invented a total order that does not extend `—>`.

- **Sequence extends arrival at one `flock`, not `—>`.** Lamport's `=>` tie-break `P_i < P_j` is arbitrary *but applied after* logical clocks that already satisfy `C1∧C2`. This plant's `sequence` is allocation order under `publication_lock` — arrival order at one host's `fcntl.flock`. Arrival order is not causal order: `P1` sends request to `P0` then message to `P2`; `P2` sends request to `P0` upon receipt. Lamport's mutex example (p. 561) shows the central scheduler that grants by arrival violates Condition II for exactly this pattern. The tape's arrival-total-order makes the same mistake as `P0` in that example whenever publication is used to decide "who requested first" across roles.

- **Anomalous behavior is the misread's cost, not just theoretical.** Lamport introduces physical clocks precisely to avoid anomaly when `=>` diverges from the user's physical perception (§ "Anomalous Behavior", p. 562). This plant's operator dashboard is that perception: an operator sees wall timestamps and chat order and assumes "A before B" causally. When logical `—>` says concurrent but physical `timestamp` says ordered, the dashboard's "before" misleads triage (e.g., which wall write caused which). Lamport's remedy is not "use finer timestamps" but bounded physical-clock synchronization with stated `κ, μ, ε` — or, more cheaply, stop using physical timestamps to define order at all and use logical clocks.

- **The plant has the mechanism to carry `T_m` but does not.** `wall.py:433` `message` and `feed.py:42` `publication_lock` already carry control-plane metadata (`task_control` tags, `RESERVED_EXACT`, `source` attribution). Adding `T_m = C_i(send)` to a `[dm]` and updating the receiver's `C_j` before its next append is the exact shape `IR2` needs. The absence is not architectural — it is a misapplication of the convenient `utc_now()` primitive where a `C_i` was needed.

- **This misread anti-correlates with correctness even as timestamps get finer.** Moving from seconds to microseconds (as `utc_now` does) narrows apparent races but widens anomaly risk per Lamport: finer physical granularity without a drift bound makes the false confidence finer, not better. The paper's message is that precision without `C1∧C2` and without a stated synchrony bound does not approach correctness; the plant's header treats microsecond UTC as if it did.

## Application

target: src/mishe_tauftauf/feed.py

**Make the tape Lamport-aware: keep `sequence` and `timestamp` for local audit and human display, but add the logical clock that actually defines `—>` — additive, pure helpers beside `_encode_entry`/:139 and `entry_timestamp`/:147, with no change to `WALL_MAX_*`, `publication_lock`, or `HEADER_RE`'s acceptance of old entries.**

1. **Add a per-source logical clock and carry `T_m` on addressed messages.** Introduce `lamport_tick(source) -> int` (the local `C_i`, incremented on every *local* event — each `_append`, each `message`, each `settle` — so `C1` holds) and `lamport_update(source, remote_Tm)` that sets `C_source := max(C_source, remote_Tm) + 1` on receipt before the next local event, i.e. `IR1`/`IR2` literally. Persist `C_i` beside the existing feed checkpoint (`feed.index_path` alongside `sequence`/`tail_offset`) so it survives restarts without reconstructing a second clock. Existing `publication_lock` already serialises the increment; cross-plant DMs sent via unbound caller include `T_m` in the body prefix (e.g. `[lm T=<int>]`), and the receiver's `wall.message`/`Feed._append` calls `lamport_update` before allocating `sequence`.

2. **Define the Clock Condition checker and the total-order `=>`.** Add `clock_condition_holds(entries) -> bool` that verifies `C1` (monotonic `C` per `source`) and `C2` (for each `send` with `T_m`, every causally-later receipt has larger `C`) and `total_order(entries) -> list[FeedEntry]` that sorts by `(C, source)` — Lamport's `=>` with process tie-break — as the causal-respecting order (distinct from arrival `sequence` order). Both are pure views over `parse_feed` output; they do not rewrite the tape.

3. **Name the anomalous-behavior gap and bound physical time.** Extend the module docstring/header comment to state the paper's boundary: `timestamp` remains a human/physical display value with `utc_now()` precision, but its `κ`/`ε` are *unstated* on this host — so `timestamp` is not to be used to decide "who happened first" between concurrent entries. Consumers that need causal before must use `C`/`=>`; where `timestamp` order and `=>` order disagree, the disagreement itself is the anomaly signal. A new check `checks/feed-lamport-clock.py` (parallel to `checks/evidence-binding-verdict-consistency.py`) enumerates the tape, verifies `C1∧C2`, and exits 2 when the Clock Condition is violated or when a batch of recent concurrent entries would have reversed under physical-time sorting — the continuing detector for the class.

**Acceptance (deterministic, runnable from this repository):**

- `python -m py_compile src/mishe_tauftauf/feed.py` and `python tools/check_parsing.py` still exit 0 — `feed.py` is not a `parsing-policy.json` legacy-budget file, and the new symbols are additive; `HEADER_RE` still parses old tape entries with no `C` present (backward-compatible display; old entries treat absent `C` as not-checked, never verified).
- Synthetic `—>` fixture: `P1` sends `[dm] to=P2` carrying `T=5`; `P2` receives then appends. After replay, `C(P2,receipt) > 5` and `clock_condition_holds` is true; `total_order` places send `=>` receipt regardless of which `utc_now()` value the host happened to stamp. Today `C2` is not checked.
- Concurrent fixture: two wall writes by different `source` with wall-clocks 20 ms apart but no message between them — `clock_condition_holds` is true, `total_order` tie-break is by `source` string (arbitrary but deterministic), and a checker that sorted by `timestamp` would have produced the opposite order — the anomaly detector reports the divergence without changing `sequence`.
- Cross-host fixture: a foreign DM with `T=100` arriving at a host whose local `C=3` — after `lamport_update`, the next local append carries `C=101`, not `4`; replay in opposite capture order still yields the same `=>` total order.

**Ownership.** `src/mishe_tauftauf/feed.py` is the tape's literal boundary (`HEADER_RE`, `utc_now`, `_encode_entry`, `entry_timestamp`, `publication_lock`) — exactly the "walls, addressed messages, chat protocol, settlement" plant part this field names. `genome` is the resolver for coordination walls and settlement (`AGENTS.md` wall-coordination trial, `docs/mesh.md`) and the target file is the one other fields already treat as wall/tape truth. `checks/feed-lamport-clock.py` is the same pattern as this plant's other `checks/*` verdict-consistency detectors and is owned by the same resolver.

**One thing this review does not ask for.** It does not replace `sequence` or `timestamp`, change `WALL_MAX_*`, alter `wall.py`'s copy guards, or implement vector clocks or full physical-clock synchronization (`κ, μ, ε` Theorem 2) — only the two logical-clock rules (`IR1`/`IR2`) and the `=>` total order that Lamport shows are sufficient to stop treating arrival time as causality.
