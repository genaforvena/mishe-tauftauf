---
field: coordination-delivery-and-release
angle: misread
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Coordination, delivery and release — misread: timeouts make complete status impossible, so the status you return must not claim it (Akkoyunlu et al. 1975)

## What we read

- Primary source: Akkoyunlu, E. A., Ekanadham, K., and Huber, R. V., "Some Constraints and Trade-offs in the Design of Network Communications", Proc. 5th ACM Symposium on Operating Systems Principles, pp. 67–74, 1975, doi:10.1145/800213.806523. Source read: TU Wien course mirror PDF at https://dsg.tuwien.ac.at/linksites/teaching/courses/AdvancedDistributedSystems/download/1975_Akkoyunlu,%20Ekanadham,%20Huber_Some%20constraints%20and%20tradeoffs%20in%20the%20design%20of%20network%20communications.pdf (fetched 2026-11-02; HTTP 200, 787304 B, sha256 1f0ad3d5d95b068949b42b39fd4485e9dbe012551f31ee8494bbc5d162ad5fa5, PDF 1.3 8 pages) extracted locally with `pdftotext` to /tmp/akkoyunlu.txt (1056 lines, sha256 d5a989acd19d5e4b8adedde0881d542bf0d819983f83894812722c6c64b88e5c). ACM DOI https://dl.acm.org/doi/pdf/10.1145/800213.806523 was queried same date (Cloudflare challenge page 5477 B, not used as source).
- Passages relied on (verbatim excerpts from extracted text):

  - FACT 2 p.69: "In an arbitrary distributed facility, it is impossible to provide complete status." Proof sketches: network partition into two disjoint systems, or unreliable status channel requiring infinite acknowledgements.

  - FACT 3 p.69–70: "In a distributed system with timeouts, it is impossible to provide complete status (even if the system is absolutely reliable)." Example: Figure 3, P1 → I1 → … → P2 chain; "If after I1 has taken the data from P1 but before the status information has returned, P1's request times out, what status shall be returned to P1? IPCM1 has no knowledge of the final outcome ... Whatever status IPCM1 returns to P1 it may prove to be incorrect. Hence complete status to both parties is impossible. P1 has to be told of the uncertainty. Thus uncertain status is inevitable at times in a distributed facility with time-outs."

  - FACT 4 + insertion property pp.70–71: IPCM has insertion property if intermediate process P between P1 and P2 stays invisible — same status as direct connection. "In a distributed system with timeouts, the insertion property can be possessed only if the IPCM withholds some status information that is known to it." Mechanism: delayed status (`RECEIVE & DELAY STATUS` / `SEND STATUS`), and ambiguous status: "a deliberate suppression of what happened is introduced by providing the same status to cover a time-out which occurs while awaiting status and, say, a transmission error. If, in addition, it is stipulated that a RECEIVE request always delays status to the sender, then the insertion property may be achieved."

  - Appendix (gangster) pp.73–74: model of two groups needing (1) delivery and (2) mutual agreement that (1) occurred. "Fact The sequence cannot terminate successfully." Proof by shortest successful sequence n: last message is important, so sender needs acknowledgement → sequence at least n+1, contradiction. "Note also that the sequence is infinite even when none of the messages are actually lost." And "At first glance it would seem that if the two processes are in continuous communication, the problem can be solved by including a sequence number [8] ... At any time the process receiving the highest numbered message knows the complete state while the other lives in doubt. Thus in practice only sequential events can be controlled but simultaneity cannot be achieved."

  - Concluding set p.72: viable combined features are "(1) Time-outs (2) Weak insertion property and partial transfer (3) Buffer processes ... (4) Well-known ports" — weak insertion tolerates buffer-size leakage; strict insertion imposes too many constraints.

- Secondary distillations consulted only as locators (not sources for claims): https://readings.shubheksha.com/papers/constraints-and-trade-offs-network-comm/ and https://shubheksha.com/posts/2017/09/some-constraints-trade-offs-in-the-design-of-network-communications/ (each cites same TU Wien PDF URL; not cited below).

- Limits the source states for itself: all FACTs assume sequential processes, no system buffering, no interrupts (SBS context, p.67); FACT 1's reliable simulation requires connected graph + link-failure detection + mean time between failures >> transaction time — special cases the paper calls inefficient. The insertion property discussion is about an IPCM primitive set, not about Git branches. Strong vs weak insertion is normative choice, not theorem. This review does not rely on the centralized vs distributed taxonomy beyond status/timeout.

- Relation to predecessor: prior `coordination-delivery-and-release--misread--2026-10-31` (status discarded, owner field-review, no target) reported ACM 403 + TU Wien socket-closed and discarded for lack of primary text. This REVISIT reads the primary PDF itself (787304 B, verified), a different mechanism (FACT 3/4 uncertain/ambiguous status + Appendix common-knowledge impossibility vs prior's assumed Two Generals citation), and targets `src/mishe_tauftauf/delivery.py` with a concrete measurement, not a discard.

## What this plant already does

- Plant part for this field: `src/mishe_tauftauf: coord_tape, coordination_checks, task_state, landing, delivery, ci_watch, release_readiness` — keywords `delivery`, `release`, `coordination` occur across `delivery.py`, `release_readiness.py`, `task_state.py`, `checks/pub-devto-watch-delivery.py` and tape code (occurrence sweep from `field_review.py next`).

- `src/mishe_tauftauf/delivery.py` already treats settlement as transport fact, not proof of delivery: module docstring "Author-owned committed candidates; genome performs only ready integration", `_sync` docstring "Crash-safe projection: readiness comes from facts, never progress prose", and `_recover_owner_wait` preserves the author's actual waiting feed entry when the projected checkpoint mismatches ("preserve the author's actual wait. Only a real later delivery transition may release it"). `finish` still completes on author-supplied rollout evidence hashed by `task_state._evidence` — the hash proves immutability, not that any consumer loaded the SHA (see prior `transfer--2026-10-31` Saltzer et al. review, same target). `line()` enumerates open phases with definitive phrases like "phase=blocked: ..." — including timeout-adjacent reasons — without labeling which rows are complete knowledge vs uncertain.

- `src/mishe_tauftauf/release_readiness.py:report()` returns binary `READY`/`HOLD` with reasons; staleness (`checked` age not 0–300 s) yields `HOLD` with "CI evidence is stale..." — a definitive HOLD for a timeout condition the paper classifies as uncertain.

- `src/mishe_tauftauf/task_state.py` models timeouts via `retry_at` deadlines and `retry_event` gates; `eligible()` flips from ineligible to eligible at the deadline, i.e., treats timeout expiry as a definitive state change. Doctrine already says "Settlement is a transport fact, not proof a patch succeeded" (`AGENTS.md`), but the status strings the code emits still read as complete.

- Prior recent REVISIT `2026-11-02` added SLSA vocabulary as informational measurement in `release_readiness.py` without gating. This misread cell needs the analogous status-knowledge measurement for delivery.

## What is new here

The paper transfers narrowly and literally — no philosophy borrow:

1. **Timeout → uncertain, even when the network is reliable (FACT 3).** The paper proves that once a timeout fires while awaiting downstream status, the upstream IPCM cannot know the final outcome (data may or may not have reached P2). "Whatever status IPCM returns to P1 it may prove to be incorrect." This plant's `delivery.py` and `release_readiness.py` both have timeout exits (retry_at expiry, CI freshness window, `author_wait_pending` recovery) but report them as definitive phases/reasons. Per the source they must be reported as uncertain — the holder timed out while awaiting status, so delivery may have succeeded or not, and the receiver may or may not know.

2. **Insertion invisibility requires deliberate withholding (FACT 4).** Strict insertion property (P invisible) is achievable only by suppressing known detail — using one ambiguous status to cover both "transmission error" and "timed out while awaiting status", and by always delaying status to the sender via `RECEIVE & DELAY STATUS` / `SEND STATUS`. The plant exposes fine-grained internal distinctions that make insertion visible: `phase` values (`branch-ci`, `ready`, `blocked`, `integrated`, `done`), `transition` tokens, `author_wait_pending` and per-reason strings like "blocked: CI not PASS" vs "blocked: author wait". A reader can distinguish an intermediate P's timeout from a direct-commit failure — precisely what the paper says must be hidden to preserve the property. Weak insertion tolerates buffer-size leakage; strict does not, and the paper warns strict "imposes far too many constraints".

3. **No finite user protocol fixes common-knowledge gap (Appendix gangster proof).** Adding one more acknowledgement (sequence number, another `delivery-*-integrated` signal, another `retry_event`) still leaves the highest-numbered receiver knowing and the other in doubt. In plant terms: `integrate` writing `phase=integrated` + `task_state.signal("[task-event] delivery-…-integrated")` + `author_retry` event does not give both author and genome common knowledge that the other knows delivery completed. The signal is another message in the infinite exchange. The paper's falsifier — infinite even when no message is lost — translates to: even with zero feed loss, a delivery signal cannot make the observer and the author simultaneously know they agree; only one side knows at each step. Plant doctrine already separates transport settlement from common knowledge, but no status helper names which delivery observations are uncertain.

The misread the angle warns about is thus present: the plant acts as if exposing a precise timeout reason, or appending one more delivery signal, upgrades uncertain status to complete status. The source says the opposite — with timeouts, uncertain status is inevitable, and more signals lengthen the chain but do not close it.

Critique kept in view: the paper's FACTs are proved for an IPCM without buffering or interrupts and with specific SBS primitives; the mapping to Git deliveries is analogical, not deductive. The viable set the paper endorses includes weak insertion + timeouts — the paper does not demand that every system hide all timeout detail, and it notes "in a well-designed system ... the above situations should not occur with any frequency, and it is still possible to provide meaningful status most of the time." Over-applying strict insertion to deliveries would suppress useful debugging detail.

## Application

target: src/mishe_tauftauf/delivery.py

Proposal for genome (owner of delivery; genome pushes `main` and checks exact-SHA CI and live consumers, so the change is theirs to author or decline). This is a measurement-before-gate change, additive and revertible:

- Add pure vocabulary and a pure helper at module top (beside existing `REPRODUCTION_KINDS` / `BENJAMIN_*` constants already in file) — no I/O, no new dependency:

  ```python
  # Akkoyunlu et al. 1975 FACT 3/4 vocabulary — timeouts force uncertain status.
  DELIVERY_STATUS_KINDS: Final[tuple[str, ...]] = ("complete", "uncertain")
  # Reasons that are timeout-while-awaiting-status per FACT 3: retry deadline expiry,
  # CI freshness expiry, author-wait mismatch. Transitive: any reason that is
  # only knowable as "timed out while awaiting downstream status".
  UNCERTAIN_DELIVERY_REASONS: Final[frozenset[str]] = frozenset({...})
  def delivery_status_kind(record: dict) -> str:  # pure, no I/O
      """complete vs uncertain per FACT 3; timeout-awaiting-status → uncertain"""
  ```

  The helper returns `"uncertain"` when `record["phase"]` is `blocked` with a timeout-await reason, or when `author_wait_pending` recovery preserved a mismatched wait (paper's "await-status timed out" case), or when `integrated_at` is present but consumer pin not yet observed (Appendix gap); otherwise `"complete"`. Keep `HOLD`/`READY` and phase machine unchanged.

- In `line(home)` (and any `check`/`show` rendering that prints a delivery row), append informational suffix `status=complete|uncertain` derived from `delivery_status_kind(record)`, and for `uncertain` render the paper's sentence once per row: "holder timed out while awaiting status; final delivery may or may not have occurred; peer may or may not know". Do not collapse the detailed `reason` text; only add the kind label so an operator seeing `blocked` can distinguish the ambiguous case (timeout-await vs transmission error per FACT 4's deliberate suppression — here labelled, not conflated). This preserves weak insertion: detail stays but is marked as uncertain.

- Keep/Revise/Revert: Prediction — after landing, `line()` shows `status=uncertain` for delivery records that hit a timeout-await path (e.g., a `blocked` record whose `reason` is a freshness/eligible deadline expiry, or a recovered mismatched wait), and `status=complete` for direct `integrated`/`done` with observed consumer pin and matching feed sequence. Observation — run `python -m mishe_tauftauf.delivery --help` / `line()` twice: once with fixture `blocked` timeout record → expect `uncertain`; once with normal `ready` → expect `complete`. Disposition — keep if labels appear and no existing consumer breaks (no HOLD gate change); revise the `UNCERTAIN_DELIVERY_REASONS` set if field reports noise (over-marking); revert if suffix breaks callers (check `tests/`).

- Tests to add with the change (deterministic, no network): `delivery_status_kind({"phase":"blocked","reason":"CI evidence is stale or has no valid timestamp"}) == "uncertain"`; `delivery_status_kind({"phase":"ready"}) == "complete"`; `line()` fixture with `author_wait_pending` mismatch → row contains `status=uncertain`.

- Live verification after activation: run `python -m mishe_tauftauf.delivery` or `python -c "from mishe_tauftauf.delivery import line; print(line(...))"` against `.mishe-tauftauf/deliveries/` before and after dropping a fixture timeout record; the informational `status=` token appears with no new HOLD. The check is the token's presence, not a new gate.

Limits: this step only surfaces the source's distinction; it does not add another acknowledgement signal to chase common knowledge (the Appendix warns that would not close the gap), does not change `finish` validation, and does not conflate transmission error with timeout into one code — it labels the timeout-await case as uncertain while leaving the detailed reason visible, i.e., weak insertion, which the source endorses as practical.

