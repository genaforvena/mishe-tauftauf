---
field: coordination-delivery-and-release
angle: critique
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Coordination, delivery and release — critique: at-least-once, disjoint scopes and alternate indices are not atomically consistent (Helland 2007)

## What we read

- Primary source: Pat Helland, "Life beyond Distributed Transactions: an Apostate's Opinion", CIDR 2007 (3rd Biennial Conference on Innovative Data Systems Research, Asilomar, January 7–10 2007), Position Paper. Source read: open PDF at https://www.cidrdb.org/cidr2007/papers/cidr07p15.pdf (fetched 2026-11-02; HTTP 200, 880896 B, sha256 a2e1af4dc44b405c3b7041108f63d1d9e0d38c2ed64d52ee5f44633efa7b7935, PDF 1.3 10 pages, Creator easyPDF SDK 4.3) extracted locally with `pdftotext` to /tmp/helland.txt (1171 lines, sha256 d606db1013e57df73f12ad41e28f9ee2838a57db8ef5a08e9d85f74000b1f1f3). All passages below are from that extracted text.

- Passages relied on (verbatim excerpts):

  - Disjoint scopes of serializability p.134–135: "We are going to consider what happens when you simply don't do this. Real system developers and real systems as we see them deployed today rarely even try to achieve transactional serializability across machines or, if they do, it is within a small number of tightly connected machines functioning as a cluster." And "Consider each computer to be a separate scope of transactional serializability. Each data item resides in a single computer or cluster. Atomic transactions may include any data residing within that single scope of transactional serializability (i.e. within the single computer or cluster). You cannot perform atomic transactions across these disjoint scopes of transactional serializability. That's what makes them disjoint!"

  - Entities and atomicity p.135: "You can never count on different entity-key-values residing in the same place!" And per-entity scope: "A scale-agnostic programming abstraction must have the notion of entity as the boundary of atomicity."

  - Alternate indices p.136–137: "Different keys (primary entity-key versus alternate keys) cannot be collocated or updated atomically." And "The scale-agnostic application program can't atomically update an entity and its alternate index! The upper-layer scale-agnostic application must be designed to understand that alternate indices may be out of sync with the entity accessed with its primary index (i.e. entity-key)." And "Reading of the data that was previously kept as alternate indices must now be done with an understanding that this is potentially out of sync" and "What in the past has been managed automatically as alternate indices must now be managed manually by the application. Workflow-style updates via asynchronous messaging are all that is left to the almost-infinite scale application."

  - At-least-once messaging p.134: Section heading "Most Applications Use 'At-Least-Once' Messaging". Dilemma p.134: "The message is consumed but not yet acknowledged. The database is updated and then the message is acknowledged. In a failure, this is restarted and the message is processed again." And "The absence of this coupling leads to failure windows in which the message is delivered more than once. The messaging plumbing does this because its only other recourse is to occasionally lose messages ('at-most-once' messaging) and that is even more onerous to deal with." Consequence p.134: "A consequence of this behavior from the messaging plumbing is that the application must tolerate message retries and the out-of-order arrival of some messages."

  - Activities (per-partner state) p.134–135: Section heading "Entities Manage Per-Partner State ('Activities')". "Each activity lives in exactly one entity. An entity will have an activity for each partner entity from which it receives messages." And "To ensure idempotence (i.e. guarantee the processing of retried messages is harmless), the recipient entity is typically designed to remember that the message has been processed. Once it has been, the repeated message will typically generate a new response (or outgoing message) which mimics the behavior of the earlier processed message. The knowledge of the received messages creates state which is wrapped up on a per-partner basis."

  - Messaging across entities p.137: "If you can't update the data across two entities in the same transaction, you need a mechanism to update the data in different transactions. The connection between the entities is via a message." And "It would be horribly complex for an application developer to send a message while working on a transaction, have the message sent, and then the transaction abort. This would mean that you have no memory of causing something to happen and yet it does happen! For this reason, transactional enqueuing of messages is de rigueur." And "As entities move, the clarity of a first-in-first-out queue between the sender and the destination is occasionally disrupted. Messages are repeated. Later messages arrive before earlier ones."

- What the critique concedes, in its own words: Position paper, not result: "The positions expressed in this paper are personal opinions and do not in any way reflect the positions of my employer Amazon.com." Assumptions "asserted and not justified. We simply assume these are true based on experience." Scope exclusion: "While I am keenly interested in high availability, this paper will ignore that issue and focus on scalability alone. In particular, we focus on the implications that fall out of assuming we cannot have large-scale distributed transactions." And about activities: "This paper is not asserting that activities can solve the well known challenges to reaching agreement described so thoroughly in workflow discussions." And "We are, however, pointing out that almost-infinite scaling leads to surprisingly fine-grained workflow-style solutions."

- Not read: the formal 2PC/Paxos/quorum literature cited in abstract, the availability analysis Helland explicitly defers, and any later evaluation of Helland's patterns. This review does not claim Helland's assumptions hold for this plant at infinite scale; it uses his stated failure mode at modest scale.

- Relation to predecessor: prior `coordination-delivery-and-release--critique--2026-10-31` read Campbell 1976 on indicator corruption (quantitative indicators used for decisions get gamed), with proposal in `src/mishe_tauftauf/discovery.py` + `wall.py`. This REVISIT reads a different text (Helland 2007 CIDR, not Campbell 1976), a different venue and mechanism (disjoint serializability scopes + at-least-once + alternate-index inconsistency vs Campbell's corruption pressures), and targets a different plant organ (`src/mishe_tauftauf/delivery.py`). The two critiques do not overlap in source, failure mode, or file.

## What this plant already does

- Plant part for this field: `src/mishe_tauftauf: coord_tape, coordination_checks, task_state, landing, delivery, ci_watch, release_readiness` — keywords `delivery`, `release`, `coordination` swept by `field_review.py next` across `delivery.py`, `release_readiness.py`, `task_state.py` and tape/feed code.

- `src/mishe_tauftauf/delivery.py` treats settlement as transport fact, not proof of delivery (module docstring "Author-owned committed candidates; genome performs only ready integration"), and its `_sync` docstring says "Crash-safe projection: readiness comes from facts, never progress prose". `finish` still completes on author-supplied rollout evidence hashed by `task_state._evidence` — hash proves immutability, not that any consumer loaded the SHA. Prior REVISIT `transfer--2026-10-31` (Saltzer et al.) already proposed end-to-end consumer acknowledgement.

- Cross-scope updates exist in one flow: `submit`/`check`/`_sync` touch three scopes that Helland would call disjoint: (1) the delivery record file under `SITE/deliveries/*.json` via `_save` under `delivery._lock`, (2) the shared tape/feed via `task_state._append`/`signal`/`finish` under the separate `seed._lock` (task_lock) inside `_publish`, and (3) the remote Git landing via `_push` (`git push --force-with-lease` to `origin/main`) and remote read via `_candidate`/`_git ls-remote` and `read_ci` (GitHub Actions). No single transaction spans them; the code splits them across two locks and a network call. This is visible at `delivery.py:319–399` where `_lock` and `task_lock` are nested but not atomic with `_push`, and at `check:470–510` where `fetch` + `merge-base` + `read_ci` are retried with `except (OSError, ValueError, subprocess.SubprocessError)` → `blocked`.

- Partial idempotence already exists: `_sync` guards both the `delivery-*-integrated` signal (line 353: `if not any(e.body.startswith(f"[task-event] {token}\n")`) and the `delivery-*-updated` author-retry signal (line 367–368 checking payload reason equality, plus `author_retry_guard` rechecking head/transition/phase and wait sequence). `_save` is guarded by `_lock`. But the feed is also an alternate index over the delivery set: globbing `home / "deliveries"/*.json` is the primary scan, while `Feed.entries()` + `task_state.registry` is the alternate view of readiness/eligibility. The code does not label that these two views can diverge (stale `deliveries/*.json` vs not-yet-appended feed entry after a crash between `_save` and `_publish`).

- `release_readiness.py:report()` returning binary `READY`/`HOLD` and `ci_watch.py:read()` reducing GitHub runs to `pass`/`fail`/`pending` both present a consistent view over data that Helland says may be out of sync (remote CI vs local pin, primary entity vs alternate index).

## What is new here

Helland transfers literally — no philosophy borrow — and names the failure this plant would reproduce if it blindly kept assuming cross-scope atomicity:

1. **Disjoint scopes are the cause, not a bug.** Any delivery that writes a file, appends to a feed, and pushes to a remote cannot be atomic once even two of those scopes live on different failure domains (local disk vs shared tape vs GitHub). Helland's "That's what makes them disjoint!" is a definition, not an implementation defect. Our doctrine already says a test/commit/push is not live delivery, but `delivery.py:344–398` still sequences `_save` → `_publish(feed)` → `signal` as if the next step follows within the same commit guard. A crash between them (which the code anticipates with its presence checks) is the expected case, not an exception. At larger scale — or even today when the coordinator process is shed under host-capacity LIMITED (dashboard: `coordination`/`observer-checks` shed) — the window widens and reordering/retry becomes visible.

2. **Alternate indices out of sync is the observable symptom.** Globbing `deliveries/*.json` plus reading `Feed.entries()` are two representations of the same delivery set. Helland predicts they cannot be kept transactionally consistent. A `check` that succeeded in writing `phase="integrated"` to the JSON but died before `_publish(home, signal token)` leaves the primary ahead of the alternate; conversely a retry that re-appends `Delivery ... phase=...` feed text before `_save` recovers leaves the alternate ahead. The current `DELIVERY: UNKNOWN` catch at `check_all:521–523` labels malformed records as UNKNOWN but does not label the normal inconsistency window as expected.

3. **At-least-once + per-partner activity is the required discipline.** Helland's "at-least-once" dilemma (message consumed → db updated → ack, restart → duplicate) is exactly the delivery watcher loop: a watcher fetches remote, decides `phase`, saves, then publishes a feed notice. If the watcher dies after deciding but before publishing, the next tick retries and must not double-create the integrated signal. The existing `if not any(e.body.startswith(...))` guard is the right shape, but Helland's refinement is to wrap that guard in per-partner (per-delivery-identity) durable state — an "activity" inside the entity — keyed by a monotonic attempt/sequence that survives repartitioning (here, movement of the entity between coordinator restarts or Git remote advances). Our current guard keys on body prefix + reason string equality; Helland would key on `delivery_attempt` + `head` + `transition` + `author_retry_wait_sequence` as the entity's remembered activity, and require that retries mimic the prior response.

The critique's limit matters: Helland explicitly ignores availability to argue scale, and calls his scopes asserted, not proved, from experience at Amazon/amazon-scale 2006. This plant's deliveries are small (twelve records, one repo, one feed) and not almost-infinitely scaled. Over-applying entity partitioning to every delivery would add indirection where a single coordinator already suffices. Weak adoption — label the disjointness, keep the retries idempotent — is what the paper endorses as practical.

## Application

target: src/mishe_tauftauf/delivery.py

Proposal for genome (owner of delivery; genome pushes `main` and checks exact-SHA CI and live consumers, so the change is theirs to author or decline). This is a measurement-before-gate change, additive and revertible, isomorphic to the recent SLSA provenance measurement and the Akkoyunlu uncertain-status measurement:

- Add pure vocabulary and a pure helper at module top beside existing `REPRODUCTION_KINDS` / `BENJAMIN_*` constants — no I/O, no new dependency:

  ```python
  # Helland 2007 vocabulary — disjoint scopes, at-least-once, alternate index, activity.
  DELIVERY_ENTITY = "delivery-identity"  # scope of serializability is one delivery record
  DELIVERY_SCOPES: Final[tuple[str, ...]] = ("delivery-file", "feed", "remote-main")
  # Reasons that are cross-scope windows per Helland: file saved but feed not yet appended,
  # feed appended but remote push not yet observed, remote observed but feed not yet reconciled.
  DISJOINT_SCOPE_REASONS: Final[frozenset[str]] = frozenset({...})
  def delivery_scope_kind(record: dict, entries: list) -> str:  # pure, no I/O
      """'atomic-in-one-scope' vs 'cross-scope-at-least-once' per Helland §2–3"""
  def is_idempotent_retry(record: dict, entries: list) -> bool:  # pure
      """whether this retry mimics a prior activity's response"""
  ```

  The helpers return `"cross-scope-at-least-once"` when the record's `phase`/`reason` implies a window that spanned `_save` vs `_publish` vs `_push` (e.g., `phase="integrated"` without a matching `f"[task-event] delivery-{id}-integrated"` entry, or `phase="blocked"` after a caught `OSError`/`SubprocessError` from `fetch`/`ls-remote`, or a recovered `author_wait_pending` mismatch), otherwise `"atomic-in-one-scope"`. No HOLD/READY gate change.

- In `line(home)` (and any `check`/`show` rendering that prints a delivery row), append informational suffix `scope=cross-scope-at-least-once|atomic-in-one-scope` derived from `delivery_scope_kind`, and for the cross-scope case render the paper's sentence once per row: "disjoint scopes: file, feed and remote cannot be updated atomically; this notice may be retried and may arrive out of order; duplicate is idempotent via activity". Do not collapse the detailed `reason` text; only add the kind label so an operator seeing `integrated` vs `blocked` can distinguish Helland's expected alternate-index lag from a definitive failure. This preserves weak alternate-index visibility while marking the window.

- In `_sync`, promote the existing `if not any(e.body.startswith(...))` guards to explicit activity checks: carry a small `delivery_attempt` monotonic integer in the record (incremented on each `submit`/`check` that re-enters `_sync`), include it in the `delivery-*-integrated` and `delivery-*-updated` signal payloads, and check idempotence by `(identity, delivery_attempt, head, transition)` rather than by body prefix alone. Keep current behavior; only add the counter to the payload so a restart mimics the prior response with the same key (Helland's "generate a new response which mimics the behavior of the earlier processed message").

- Keep/Revise/Revert: Prediction — after landing, `line()` shows `scope=cross-scope-at-least-once` for delivery records that hit a cross-scope window (e.g., a `blocked` record whose `reason` is a transport exception, or an `integrated` record whose feed signal has not yet appeared), and `atomic-in-one-scope` for a record where JSON, feed and remote are reconciled. On retry ticks, duplicate signals are suppressed by activity key, not just by prefix. Observation — run deterministic fixtures: a `blocked` transport record → expect `cross-scope-at-least-once`; a reconciled `done` with observed consumer pin and matching feed sequence → expect `atomic-in-one-scope`; kill-and-retry simulation (save without publish, then re-run `_sync`) → no duplicate `task-event`. Disposition — keep if labels appear and no caller breaks (no HOLD gate change); revise `DISJOINT_SCOPE_REASONS` if noisy; revert if suffix breaks callers (grep `tests/delivery` etc.).

- Tests to add with the change (deterministic, no network): `delivery_scope_kind({"phase":"blocked","reason":"Remote main advanced; author must rebase..."}) == "cross-scope-at-least-once"` when feed lacks the retry event; `delivery_scope_kind({"phase":"done", ...}) == "atomic-in-one-scope"` when `f"[task-event] delivery-{id}-integrated"` present and consumer pin observed; idempotence test that a second `_sync` with same `delivery_attempt` does not append a second `task-event`.

- Live verification after activation: run `python -m mishe_tauftauf.delivery` or `python -c "from mishe_tauftauf.delivery import line; print(line(...))"` against `.mishe-tauftauf/deliveries/` before and after dropping a fixture cross-scope record; the informational `scope=` token appears with no new HOLD. The check is the token's presence, not a new gate.

Limits: this step only surfaces the source's distinction and hardens the existing retry to Helland's activity key; it does not change `finish` validation, does not add distributed transactions, and does not claim almost-infinite scale for this plant. It endorses Helland's weak-entity approach: detail stays, but the cross-scope window is labelled as expected at-least-once.
