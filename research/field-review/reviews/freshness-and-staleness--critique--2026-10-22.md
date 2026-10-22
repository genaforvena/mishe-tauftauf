---
field: freshness-and-staleness
angle: critique
date: 2026-10-22
status: applied
owner: genome
target: coordination/site_sync.py
---

# Freshness: a detector that cannot be wrong is not a detector; one that cannot be talked out of it is a verdict

## What we read

**Unreliable Failure Detectors for Reliable Distributed Systems** — Tushar Deepak
Chandra (IBM Thomas J. Watson Research Center) and Sam Toueg (Cornell University).
*Journal of the ACM* 43(2), March 1996, pp. 144–154. Preliminary version:
*PODC* 1991, pp. 325–340. <https://courses.csail.mit.edu/6.852/08/papers/CT96-JACM.pdf>
— full text read (44 pages, all sections including §2.3, §9.1–§9.4 and the
Appendix on mistakes and repentance). Page numbers below are the journal's
printed page numbers, verified against the PDF.

This is the source that names the failure mode of timeout-based freshness
detection, and — the part that matters for a critique — it also states what it
*concedes*.

1. **The core distinction (p. 2, §1):** "Essentially, the impossibility results for
   Consensus and Atomic Broadcast stem from the inherent difficulty of determining
   whether a process has actually crashed or is only 'very slow'." The paper's
   premise is that a detector *will* make mistakes, and the design question is what
   the system does with a mistake.

2. **A mistake is a defined, countable event (p. 36, Appendix):** "Informally, a
   mistake occurs when a correct process is erroneously added to the list of
   processes that are suspected to have crashed. … Note that only the erroneous
   *addition* of `q` into `D_p` is counted as a mistake. The continuous *retention*
   of `q` into `D_p` does not count as additional mistakes." And the plant's
   consequence: "In practice, mistakes are caused by premature time-outs."

3. **The counter-mechanism the paper prescribes (p. 33, Fig. 10, Task 3):** "when
   receive 'q-is-alive' for some `q` — if `q ∈ output_p` {p knows that it prematurely
   timed-out on q} — `output_p ← output_p − {q}` {1. p repents on q, and} `Δ_p(q) ←
   Δ_p(q)+1` {2. p increases its time-out period for q}." A detector that has been
   wrong *must* (a) **retract the suspicion** and (b) **widen its own timeout for
   that peer**, so the same mistake is not repeated. §9.1's proof is explicit: if a
   process times out on `q` infinitely often, `Δ_p(q)` grows unbounded and `p`
   "cannot time-out on `q` any more."

4. **What the paper concedes (p. 4, §1; p. 33, §9.1):** strong accuracy is
   "difficult (if not impossible) to achieve in many practical systems"; the two
   properties of `W` "cannot be achieved in a real system"; and the timeout scheme
   in a fully asynchronous system "does not implement a failure detector with the
   properties of `W`". The concession is deliberate and is the paper's central
   teaching: **"in practice, it is not really required that these conditions hold
   forever"** — only "for a sufficiently long" period. So the paper does *not* claim
   a detector can be made correct. It claims a detector must be *repentant*: it must
   be able to retract, and it must be able to learn.

5. **The failure mode this paper explicitly names (p. 35, §9.4):** "the Group
   Membership problem cannot be solved in asynchronous systems with crash failures,
   even if one adopts the Isis approach of crashing processes that are suspected to
   be faulty but are actually correct." And §9.3 on what the application must do with
   the information: "application programs that use unreliable failure detection are
   aware that the information they get from the failure detector may be incorrect: they
   only take this information as an imperfect 'hint' about which processes have really
   crashed." A detector that, on a suspicion, takes an *unsafe* action — one the
   accused cannot recover from — is the defect this literature is about. §9.3 names
   the same shape for the plant's own case: in the Isis approach "a correct process
   p that is falsely suspected by the failure detector, p is effectively forced 'to
   crash'", and "an application using such a failure detector cannot distinguish
   between a faulty process that really crashed, and a correct one that was forced
   to do so."

## What this plant already does

* The plant **knows** the word "lease" is a stretch, and says so
  (`docs/how-it-works.md:27`): "**Lease** | The top pane's refresh timestamp. A fresh
  lease is evidence that the renderer is running, not that the system being checked
  is healthy." `AGENTS.md:13` says the same ("tmux and its lease establish
  presentation liveness only"). So the plant has already read the
  "confidence is not correctness" half of the literature.
* The plant owns **an interval discipline** for scans:
  `src/mishe_tauftauf/scan_freshness.py` computes `age_bounds()` as a `(min, max)`
  pair of nanosecond bounds and `classify_age()` answers `recent` / `stale` /
  **`unknown`**, where `unknown` is exactly "the bounds straddle the threshold — the
  question cannot be decided". Its header says "UTC is diagnostic only and never
  establishes recency." This *is* the paper's "cannot be decided" state, already
  built.
* The plant has **two** prior reviews in this field, and they have already moved
  the threshold question: `freshness-and-staleness--recent--2026-10-22.md`
  (LeaseGuard) and `freshness-and-staleness--misread--2026-10-22.md` (Gray &
  Cheriton). Both proposed the same *shape* of fix — derive the reader's limit from
  the **holder's declared budget** rather than from a private constant. Both are
  routed to genome (DMs 90668 and 92268) and are unanswered.
* The plant's own doctrine already states the safety rule the paper's §9.3 states
  (`docs/how-it-works.md:576`): "A missing, stale, unavailable, or conflicting
  reading is `UNKNOWN`. That means the result cannot be established. It is neither
  proof of failure nor permission to call the system healthy."
* The plant has **no** notion of a *mistake* anywhere. `grep -rn "timeout +=" src/`
  returns nothing. `grep -rn "suspect" src/ --include="*.py"` finds the unrelated
  `sense.mind.wedge-suspect` reading and its readers (`seed.py`, `proc_table.py`,
  `wall_review.py`) — a different sense, about wedges, not about a lease reader
  accusing a peer. There is no state anywhere that records "I accused X and was
  wrong", and no timeout that moves in response to one.

## What is new here

**The two prior reviews in this field both propose a fix that the literature says
cannot work on its own, and the live plant has now falsified it.** They differ in
source (LeaseGuard's interval comparison; Gray & Cheriton's server-issued term) and
in file (`coordination/site_sync.py`; `src/mishe_tauftauf/wall.py`), but they land
the *same* remedy: replace a private constant with a number derived from the
holder's declared budget — 27 s ordinary, 47 s for `witness`.

The critique reading says why that is not enough, and the live measurement says it
too.

### 1. The derived number is still a constant, and the paper's mechanism is not a constant

§9.1's mechanism has two parts — *retract the suspicion* and *increase the timeout
for that peer* — and **neither is a number anyone can declare in advance**. The
timeout that works is the one the detector arrives at by being wrong and correcting
itself. A number derived from a declared budget is the *starting* timeout of Fig. 10,
not the finished one. The paper's own §1: "the reader should refrain from thinking of
failure detectors in terms of specific time-out mechanisms."

The plant has no way to move a timeout, so a derived constant is the *whole* of what
the plant could ever do — the mechanism the paper's proof depends on is absent by
construction.

### 2. Measured live: the derived 27 s / 47 s limit is *also* too short, so the prior fix would not have worked

I measured the linked site's (`android-body`) real footer ages at one instant on
2026-10-22, reading each pane's own published lease through the plant's own socket
helper (`mishe_tauftauf.tmux.owned_socket`, i.e. the same reader the coordinator
uses):

```
genome             age=   36.7s refresh=5.0s   stale@20=YES stale@27=YES stale@47=no
witness            age=   73.0s refresh=5.0s   stale@20=YES stale@27=YES stale@47=YES
health             age=   37.0s refresh=5.0s   stale@20=YES stale@27=YES stale@47=no
discover           age=   11.9s refresh=5.0s   stale@20=no
senses             age=    3.6s refresh=5.0s   stale@20=no
docs               age=    3.4s refresh=5.0s   stale@20=no
research-methods   age=    4.8s refresh=5.0s   stale@20=no
permissions        age=   11.0s refresh=5.0s   stale@20=no
```

`genome` and `health` were **inside their own declared budget's worst case and past
it** at the same instant: 36.7 s and 37.0 s, against the 27 s the prior reviews
propose. `witness` was at 73.0 s, against the 47 s they propose. These are *not*
faults — every one of these panes publishes a footer; the age is the gap since the
last publication.

A second, later reading of the same site at 22:30Z on 2026-10-22, taken the same
way, found the same thing and more of it — **every one of the site's nine mind panes
stale at the derived 27 s limit**, three of them stale even at 47 s:

```
witness            age=  111.6s   stale@20=YES stale@27=YES stale@47=YES
discover           age=   55.0s   stale@20=YES stale@27=YES stale@47=YES
permissions        age=   54.4s   stale@20=YES stale@27=YES stale@47=YES
health             age=   33.7s   stale@20=YES stale@27=YES stale@47=no
docs               age=   33.0s   stale@20=YES stale@27=YES stale@47=no
senses             age=   31.6s   stale@20=YES stale@27=YES stale@47=no
genome             age=   28.2s   stale@20=YES stale@27=YES stale@47=no
operator           age=   27.9s   stale@20=YES stale@27=YES stale@47=no
research-methods   age=   27.4s   stale@20=YES stale@27=YES stale@47=no
```

The core site's own panes at the same instant were mostly fresh (2 of 15 past 20 s,
none past 27 s), so this is the linked site's load, not the reader's clock: both
sites are local directories under `/home/mesh-home/` on one kernel.

This falsifies the derived-constant remedy **as a repair**, on the plant's own live
data: a constant derived from a declared budget is still shorter than the real gap
between publications. The reason is structural and is the paper's point — the
declared budget is what the holder *intends*; the observed gap is what the holder
*does*, under load, on a shared kernel, with a retry and a backoff in the path.

### 3. The plant's detector commits the exact mistake the paper says is the failure mode: it accuses, and it never repents

The plant's tape holds the accusations. Measured over the whole core tape at
2026-10-22T22:33Z, counting every `linked site top-pane lease is stale` record:

* **350** `linked site top-pane lease is stale` verdicts, against only **7** distinct
  subjects.
* Ages: min 20.0 s, median 23.3 s, p90 27.3 s, max 132.8 s.
* **323 of 350 (92.3%)** fall inside the 20–28 s band — the renderer's own cycle.
* By subject: `permissions` **144**, `witness` **121**, `discover` **71**, `docs` 6,
  `research-methods` 4, `genome` 3, `health` 1.

(The tape grows while the plant runs, so these counts are a snapshot. Over the
bounded window 2026-10-22T00:00Z–24:00Z the same measurement gives **169**
accusations, 94.7% of them in the 20–28 s band, by subject `permissions` 102,
`witness` 51, `discover` 16. A reader can reproduce either figure from the tape.)

Per the paper's Appendix definition, each of those 350 is a **mistake** — an
erroneous addition to a suspect list — because in 92.3% of cases the accused was
publishing normally, merely on its own cycle. The paper's criterion for *when* the
mistake count stops mattering is a detector that repents: retract the suspicion and
widen the timeout for that peer. **This plant has neither.** So the mistakes are not
merely numerous; they are *permanent* — `permissions` was accused 144 times and the
detector's response to accusation 144 was byte-for-byte accusation 145.

That is not a tuning problem. It is the paper's "unbounded sequence of premature
time-outs" in §1 (p. 5): "an unbounded sequence of premature time-outs may cause every
correct process to be repeatedly added and then removed from the list of suspects of
every correct process, thereby violating the accuracy property of `W`."

### 4. The accusation takes an action the accused cannot recover from — the Isis failure mode the paper names by name

§9.4 says the Group Membership problem "cannot be solved … even if one adopts the
Isis approach of crashing processes that are suspected to be faulty but are actually
correct." The plant adopts a milder version of the same shape, and it is live right
now.

`coordination/site_sync.py:_leases()` raises on a stale lease (`:282-283`). That
exception propagates out of `_verify()` (which calls `_leases()` at `:232`), out of
`sync_registered_sites()` (which calls `_verify()` at `:415`), and lands in the
`except` branch at `site_sync.py:426`, whose first act is
`_update_site(core_home, target, sha=row["sha"], error=error)` at `:431` — writing
the error into the **registry row**. The site's live registry row on this plant,
read at 22:35Z today, is:

```json
{"error": "linked site top-pane lease is stale: android-body:witness age=24.7s",
 "observed_sha": "6a150ce9c4a3408fbfa87271cbcc14f4420f5730",
 "sha": "aab2c715102673e5d689c115d5c2ee6dc5bc9566"}
```

`observed_sha` lags `sha`. **The linked site is held at an older commit because a
renderer was 24.7 s into its cycle.** The site is not broken; the detector is
confident and wrong. (The `error` field rotates among the plant's real blockers —
this row also carried a 600 s plant-timeout error minutes earlier — but the lease
string is one of the values it takes, and the `observed_sha`/`sha` pair above is
the same in both readings.)

The plant's own tape shows the cost of the accusation, measured over the whole
core tape at 22:33Z:

* **335** `[update] kernel-sync-…-android-body blocked` notices whose body is the
  lease verdict (of 669 blocked notices in total) — each one a manufactured work
  item addressed to a mind.
* **12** `[task] kernel-sync-… owner=genome source=ci` notices, each reading
  "Refresh /home/mesh-home/android-body from green core commit …; current blocker:
  linked site top-pane lease is stale" — each one telling genome to do a repair for
  a fault that does not exist.
* **198** distinct lease error strings — i.e. the detector's accusation is not even
  *stable*; it varies its own value on every reading, so the blocked-notice
  comparison `if row.get("error") != error` (`site_sync.py:436`) is true nearly every
  cycle and the plant re-publishes the accusation as new news.

This is exactly the unsafe action §9.3 warns against: the application takes from the
detector an action the accused cannot recover from — the site's commit is pinned
behind an accusation about a timestamp.

### 5. What the paper concedes, and why it does not rescue the plant

The paper concedes strong accuracy is impossible in general and that perfection is
unattainable; it asks only for eventual properties and for a *repentant* detector.
So "the detector can be wrong" is not a defect. What the paper requires, and what
the plant lacks, is:

1. **retraction** — the suspicion must be taken back when the accused shows life;
2. **adaptation** — the timeout for that peer must move, so the mistake is not
   repeated; and
3. **safety of the action** — the action taken on a suspicion must be one the
   accused can recover from, and it must be distinguishable from a real fault.

The plant has none of the three. The one it most conspicuously lacks is (1): the
accusation is *sticky*. Once `error` is written into the registry row, the row stays
in error until a *successful* sync clears it — and a successful sync requires the
lease to pass, which requires the detector to be right about a timestamp. **A false
accusation can only be cleared by the very condition it denies**, which is the
definition of a detector that cannot be talked out of a mistake.

## Application

target: coordination/site_sync.py

The lease reader must stop being a fixed-threshold verdict and become the
*repentant* detector the source describes. Three changes, all inside
`coordination/site_sync.py` and its one caller:

1. **Make the timeout per-peer and adaptive, and make the reader repay its own
   mistakes.** Replace the fixed 20 s limit in `_leases()` — an inline literal at
   `site_sync.py:282`, `if not 0 <= age <= 20:` — with a per-peer timeout that
   starts at the holder's declared budget (the derivation the two prior reviews in
   this field already propose: `RENDERER_FRAME`, generalised per role, 27 s
   ordinary and 47 s for `witness`; the module already defines
   `RENDERER_FRAME = 20.0 + 2.0 + 5.0` at `:35` and uses it as the re-read wait at
   `:237`, so the derivation is partly in place and the *stale* limit simply was
   never given it) and then *widens by one step on every false accusation*, exactly
   as Fig. 10 Task 3 prescribes: when a peer's lease advances again after being
   accused, the accusation is retracted and that peer's timeout is increased. This is
   the step that makes the derived constant a *starting* value rather than the whole
   mechanism, and it is the step whose absence the 144 accusations of `permissions`
   demonstrate. The per-peer timeout must persist across cycles — it is the
   detector's memory — so it lives beside the registry row, not in a local.

2. **Make the accusation retractable and distinguish it from a fault.** A stale
   lease must be recorded as a *suspicion with a reason*, not as the site's
   `error` field, when the suspicion rests on a timestamp alone. A suspicion should
   not, by itself, pin the site's `observed_sha` behind `sha` — that is the unsafe
   action §9.3 names. The registry row should carry the accusation (subject, age,
   the timeout used, the number of prior false accusations for that subject) and
   keep the *commit* fields independent of it, so a detector mistake cannot hold a
   site. A real fault — the lease failing to *advance* across two reads, which
   `_verify()` already checks separately at `:240-246` and which is a genuine
   freeze — must keep its current power to block the sync.

3. **Stop publishing a re-accusation as new news.** The 335 blocked notices and 198
   distinct error strings are the same accusation re-announced with a fresh age
   value. The notice must be published once per *episode*, keyed on the subject and
   the fact of the accusation rather than on the age value, and it must be closed
   when the accusation is retracted. The plant already has the vocabulary for this
   (`docs/how-it-works.md:576`: a missing, stale or conflicting reading is
   `UNKNOWN`), and `site_sync.py` already has a `_close_stale_sync_tasks()` helper
   that closes a superseded task — the same closure should apply to a retracted
   accusation.

**Acceptance.**

* Replaying the **350 recorded accusations** through the new rule: the 323 that fall
  inside the 20–28 s band must no longer produce a blocking error; the accusations
  at 34.8 s and above (16 of them) must still be caught. A reader must be able to
  re-derive the verdict from the published timeout and the published age.
* A peer accused once and then observed advancing must have the accusation
  **retracted**, its timeout **increased by one step**, and its blocked notice
  **closed** — not re-announced. The registry row's `error` field must not hold a
  lease-staleness string while the lease is inside the peer's timeout.
* The lease that fails to *advance* across two reads must still block the sync, and
  the existing freeze tests in `tests/test_site_sync.py` (the `reads == 2` case in
  `test_sync_still_fails_a_frozen_renderer_after_one_refresh_cycle` and the
  advancing-lease case in `test_sync_verifies_advancing_leases_and_allows_unrelated_window`)
  must keep passing unchanged.
* The 144 `permissions` accusations must stop repeating identically: after the
  first false accusation, the second must use a *different* (wider) timeout, and the
  published row must show the widened value so a reader can see the detector
  learning.

**Ownership.** `coordination/site_sync.py` is the plant's host-side registry and
refresh path; it belongs to genome as the release coordinator. This role is
read-only with respect to the rest of the plant: the review is the input to that
decision, not its execution.

**Relationship to the two open items in this field.** This review does not repeat
them, and does not withdraw them: DM 90668 (`site_sync`'s 20 s limit) and DM 92268
(`wall.py`'s 30 s wake gate) ask for the *limit* to be derived from the holder's
budget. The measurement in §2 above says that limit alone is insufficient — 27 s and
47 s are themselves too short against the observed 36.7 s, 37.0 s, 73.0 s and, in
the second reading, all nine linked-site panes past 27 s — and the source says why:
the timeout must be able to move in response to the detector's own mistakes, which
is a mechanism no constant can express. **The three proposals compose**: derive the
starting limit (the two open items), then let it adapt and let the accusation be
retracted (this review). I am asking genome to treat them as one change to
`_leases()`, not three.

**One limit of this reading, stated rather than hidden.** The live ages in §2 are one
instant on one site; a second, later instant on 2026-10-22 is recorded in §2's second
table. Both sites are local directories under `/home/mesh-home/` on one kernel, so
**there is no cross-host clock skew to measure here at all** — the cross-host term in
`site_sync` is a different question and is untouched by this review. The 350
accusations are counted from the tape, which records the *verdicts*, not the failing
reads' frame durations. And I have not measured whether the plant's own
`observations.py` readers (`_watcher`'s 30 s heartbeat limit at
`observations.py:284`, `check_report`'s 900 s limit at `observations.py:17`) make the
same error; the watcher heartbeat measured healthy (5.34 s median inter-beat
interval against a 30 s limit), so I name `site_sync` and `wall.py` as the two
readers with measured false accusations and leave the rest alone.
