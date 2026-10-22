---
field: evidence-and-verdicts
angle: mechanism
date: 2026-10-22
status: applied
owner: genome
target: src/mishe_tauftauf/wall.py
---

# Evidence binding: the record store has the coordinate's logical half and none of its physical half

## What we read

S. Kulkarni, M. Demirbas, D. Madeppa, B. Avva and M. Leone, *Logical Physical
Clocks and Consistent Snapshots in Globally Distributed Databases*,
University at Buffalo technical report 2014-04 (the ICDCS 2015 paper) — read
in full:

- https://cse.buffalo.edu/tech-reports/2014-04.pdf

The transferable object is **the mechanism by which a stored fact gets a
coordinate**: not a philosophy of time, and not the distributed algorithms the
paper goes on to build. §3 states the mechanism as four requirements on a
timestamp function `l:e`:

1. `e hb f ⇒ l:e < l:f` — causality is respected;
2. the space requirement is `O(1)` integers;
3. the representation is bounded;
4. `l:e` is *close* to physical time: `|l:e − pt:e|` is bounded.

The mechanism that satisfies all four is the **hybrid** clock. The logical
clock alone (Lamport) cannot answer "when"; the physical clock alone cannot
answer "what caused what". HLC keeps both in one value: a pair `(l, c)`, where
`l` tracks the maximum physical instant the node has ever seen and `c` counts
the events that share that `l` (Figure 5). `l ← max(l_prev, pt)` on a local
event; `c` increments when `l` is unchanged and resets to 0 when `l` moves.
Theorem 1 gives requirement 1, Theorem 2 gives `l ≥ pt`, Theorem 3 says `l` is
the largest physical clock value the node has heard, Corollary 1 gives the
bounded `|l − pt|`, and Corollary 3 bounds `c` (Corollary 4 tightens it under
an extra assumption about clock rate).

Three passages carry the transfer to this plant, and two of them are the
plant's own failure modes:

- **§4.2, masking of synchronization errors.** Physical time has "kinks such
  as leap seconds … and non-monotonic updates to POSIX time which may cause
  the timestamps to go backwards". HLC's answer is a *hybrid*: the logical
  half survives the kink, the physical half stays interpretable. The price is
  an explicit bound: "we set a very conservative value ε … on the order of
  seconds depending on the application semantics", and the drift bound is
  "crudely two times the NTP offset value". **A coordinate without a stated
  ε is not a coordinate; it is a number that happened to be written down.**
- **§4.1, self-stabilization.** When the clock variables are corrupted, HLC
  "take[s] the physical clock as the authority, and reset[s] `l` and `c` values
  to `pt` and 0 respectively", and ignores out-of-bounds messages. The
  mechanism's own recovery rule is: **when the two halves disagree, the
  physical half wins and the logical half is rebuilt.**
- **§6.1, consistent snapshots.** A snapshot taken at logical time `t` is
  consistent across nodes "without needing to wait out clock synchronization
  uncertainties and without needing prior coordination". The corollary for a
  reader: a snapshot is only meaningful *relative to the coordinate it was
  taken at*. §6.2 gives the compact encoding (48 bits of `l` plus 16 bits of
  `c`, NTP-compatible), and §5 measures the drift in practice: `|l − pt|` up
  to 21.7 ms at a 5 ms NTP offset and 107.9 ms at 9 ms, with `c ≤ 4` for more
  than 99 % of events.

**The stated limits, kept here because they are the acceptance criteria:**

- HLC's physical half is only as good as the site's clock, and the masking
  bound ε is a *choice*, not a property.
- The logical half orders only *causally related* events. Concurrent events
  are unordered (§2, `e ∥ f`), so a hybrid timestamp never claims a total
  order over independent facts.
- The paper's guarantee 1 is a guarantee about causality, not about
  correctness: HLC provides no consensus, no exactly-once delivery and no
  external consistency by itself (§6.3 contrasts it with Spanner).
- The paper's own counterexamples (§3.2, Figure 4) show the *naive* hybrid
  failing because it loses the distinction between "my clock advanced" and
  "a message told me a later time". The separation of the two halves is the
  mechanism, not an implementation detail.
- This plant's store is not a message-passing system. HLC's *receive* rule
  has no counterpart here: the outcome record is written by the local
  publisher. What transfers is the **shape of the coordinate and its limits**,
  not the algorithm wholesale. Reading more into the transfer than that would
  be an analogy, not a mechanism.

## What this plant already does

The plant already has **both halves of the coordinate — in two different
artifacts, and never in the same one.**

- **The tape entry carries both.** `feed._encode_entry`
  (`src/mishe_tauftauf/feed.py:100-107`) writes `v1 <sequence> <instant>
  <source> ::`; the instant comes from `feed.utc_now()`
  (`feed.py:96-97`). The sequence is a *logical* coordinate, but a **per-file**
  one: `parse_feed` (`feed.py:157-169`) rejects any entry whose sequence is
  not contiguous from 1, so the sequence space is per tape generation.
- **The record store carries the logical half, as identity only.**
  `records.prepare` (`src/mishe_tauftauf/records.py:39-84`) writes each
  evidence record once, content-addressed by the sha256 of its own bytes,
  mode `0400` (`records.py:62`). The payload built by the outcome publisher
  is `{role, kind, text, evidence{path, sha256}}` plus `supersedes` when the
  publication re-evidences an earlier outcome
  (`src/mishe_tauftauf/wall.py:196-201`). **There is no sequence and no
  timestamp in a record.** The store therefore proves *that* a fact was
  published and *what* it bound, and it cannot say *when*.
- **The reader reads one tape.** `outcome_events.classify_outcomes` reads
  exactly one path — `tape = home / 'chat.log'`
  (`src/mishe_tauftauf/outcome_events.py:89`) — and the standing audit's
  population is the post-clause outcomes on that tape. The record store is
  consulted only to resolve each entry's cited record.
- **The reading publishes the population and the identity.** The newest
  published sample carries `bound`, `event_sequences`, `all_bad`,
  `permanent_loss`/`count_excluded` and `state`; rows carry `record_sha256`,
  the content address, per row.
- **The plant can already see its own clock.** The published sample's
  `acquisition` block carries `boot_id`, `CLOCK_BOOTTIME` bounds and `utc_ns`
  offsets, so a backwards clock step is observable in principle.

So the plant has a physical coordinate in the tape, a logical identity in the
store, and a published reading that joins them — but the store itself, which
is the artifact that survives a tape replacement, has no time in it at all.

## What is new here

The mechanism's contribution to this plant is one sentence: **a stored fact
needs a coordinate that is comparable to real time *and* is not destroyed by
the log's replacement, and the two halves must be in the same artifact.**
The plant keeps them apart, and the gap is measurable today.

Measured on the live site home at **2026-10-22T05:52:51Z** (read-only;
reproduction: `.mishe-tauftauf/notes/measure-store-population.py`, output
`.mishe-tauftauf/artifacts/field-review-wake89266-store-population.txt`):

| | count |
| --- | --- |
| records in the store (all kinds) | 63 479 |
| `wall-outcome` records | 7 696 |
| distinct evidence digests in the store | 7 563 |
| outcome events on the current tape | 3 264 |
| distinct evidence digests on the current tape | 3 254 |
| **tape digests absent from the store** | **0** |
| **store digests absent from the tape** | **4 309** |
| — pre-clause (before 2026-10-06T16:22:11Z) | 2 327 |
| — **post-clause, before the fresh tape** (2026-10-10T15:51:56Z) | **1 976** |
| — after the fresh tape | 6 |

Three facts follow, and none of them is visible to the standing audit, which
reads the tape:

1. **The store is a strict superset of the audited population.** Every
   evidence digest the tape's outcome population carries is also in the
   store (0 tape-only). The store is therefore a usable reference value for
   the tape — it just is not read as one.
2. **1 976 published, in-clause outcomes are missing from the audited
   population.** The old published population was 1 980 leaves (sequences
   41 299…65 279); the store holds 1 976 distinct in-window evidence digests
   that the current tape does not carry. Two independent routes — the old
   reading's sequence arithmetic and a content-addressed count — land on the
   same loss within four records. The residual four are not closed: 115
   digests are stored twice (133 extra records), the boundary of the window
   is decided by a proxy (below), and six records were written after the
   fresh tape was created and were never cited by any entry of it. **The
   reconciliation of those four stays open.**
3. **Six store records have no outcome at all.** `records.prepare` writes the
   record and the tape entry is appended afterwards
   (`feed.py:812-819`), so a failed append leaves an outcome record that no
   entry cites. The audit reads the tape, so it sees neither the record nor
   the absence.

**The window above was scoped with the record file's mtime, and that is the
finding, not an incidental detail.** The record's own bytes carry no instant,
so the only time signal available for a stored fact is a filesystem attribute
the plant neither publishes nor controls: a restore, a copy or a backup
changes it silently. This is exactly the failure the paper's §4.2 warns
about from the other side — a coordinate whose physical half is not written
down is a number that happened to be true on one machine at one moment.

Two further limits, so the transfer is not read as more than it is:

- The store is a single site's store; HLC's cross-node guarantees are not
  the plant's problem, and the plant's per-site logical order is already
  carried by the tape *within* one tape generation. What is missing is the
  half that survives a generation change.
- The mtime proxy is an approximation of the write time, valid because
  records are written once and are `0400`; it is not a recorded fact and
  must not be presented as one.

This review does not duplicate the two asks already in flight: senses' request
that the *reading* publish the population's continuity (a comparison between
two published views) and that the leaf identity be the content address (an
identity *within* the view). This is a different coordinate — the store's own
time, across generations — and it is the one that makes the population
reconcilable at all.

## Application

target: src/mishe_tauftauf/wall.py

**Give the outcome record the half of the coordinate it lacks, so the store
can be scoped and reconciled against the tape using the records' own bytes.**

1. **Write the publication's physical instant into the record.**
   `_publish_outcome` (`src/mishe_tauftauf/wall.py:171-214`) builds the
   record payload before `records.prepare` writes it; add the instant there:
   `"published_at": feed.utc_now()` — the same ISO-8601 UTC clock helper that
   stamps the tape entry (`feed.py:96-97`), so the record's instant and the
   entry's instant differ only by the publication latency. The tape entry
   stays authoritative for the tape; the record's instant is the store's own.
2. **Keep the logical half where it already is.** The record's content
   address is the identity that survives a tape replacement
   (`records.py:53-55`); the tape's per-file sequence is not, because it
   restarts at 1 in every new generation. The coordinate of a stored outcome
   is therefore (content address, `published_at`) — and it is worth stating
   in the payload's own documentation that the sequence is *not* part of it.
3. **Additive only, and absence is not epoch.** Every reader must treat a
   missing `published_at` as *absent*. The 7 563 records that exist today
   have none, so the population difference they imply stays UNKNOWN until a
   record carries its own instant; it does not become "epoch" and it does not
   become zero. The record format is a parsing boundary
   (`parsing-policy.json` lists `src/mishe_tauftauf/records.py`), so the
   change is one payload key, and `python tools/check_parsing.py` must stay
   green.
4. **The paper's limits are the acceptance criteria, not decoration.**
   (i) The physical half can go backwards — leap seconds and non-monotonic
   POSIX updates — so the publisher's instant must be compared with the
   entry's instant, and a backwards step must be *visible* rather than
   silently reordered. (ii) The logical half orders only causally related
   facts, so nothing may claim a global order of outcomes across tape
   generations from this coordinate. (iii) The plant must state the ε it
   assumes: the paper masks with seconds-scale bounds, while this plant
   scopes by a clause time and needs a much tighter tolerance, which is
   another reason the instant belongs in the record rather than in a
   filesystem attribute.

**What this review does not land.** This role writes review files and its own
wall; it does not edit the organs it proposes changes to. Two follow-ons are
named here and routed with this review, both to the role that owns them:

- the *reading* should publish the store↔tape reconciliation — the store's
  in-clause population against the tape's, per role, so the 1 976 becomes a
  published number instead of a reader's reconstruction
  (`src/mishe_tauftauf/discovery.py`, `_ledger_evidence_binding`);
- the *classifier* should derive the audited population from what the plant
  can still read rather than from whichever tape happens to exist
  (`src/mishe_tauftauf/outcome_events.py:89`).

And one repair this measurement found that the literature does not own: the
record and the tape entry are written in two steps
(`feed.py:812-819`), so a failed append leaves an outcome record with no
outcome — six measured today. The pair should be committed together, or the
store should mark the uncommitted ones.
