---
field: evidence-and-verdicts
angle: transfer
date: 2026-10-22
status: applied
owner: genome
target: checks/evidence-binding-verdict-consistency.py
---

# Evidence binding: a verdict that measured 1/990th of the ledger still said "verified"

## What we read

B. Laurie, E. Messeri and R. Stradling, *Certificate Transparency Version 2.0*,
RFC 9162, IETF, December 2021 (obsoletes RFC 6962) — read in full at the RFC
Editor:

- https://www.rfc-editor.org/rfc/rfc9162.txt

The transferable object in this document is **not** TLS and **not** PKI. It is
the *auditor* of an append-only log. Four passages carry the transfer, and the
document itself licenses the transfer in its Introduction: the CT mechanism
"is a general mechanism that could be used for transparently logging any form
of binary data, subject to some kind of inclusion criteria."

1. **§2.1.4, Merkle Consistency Proofs.** "Merkle consistency proofs prove
   the append-only property of the tree. … a consistency proof must contain a
   set of intermediate nodes (i.e., commitments to inputs) sufficient to
   verify MTH(D_n), such that (a subset of) the same nodes can be used to
   verify MTH(D[0:m])." The claim is exact: **the later tree must contain the
   earlier tree's leaves.** A later view that is smaller is not a later view;
   it is a different log wearing the same name.
2. **§8.2, Monitor, step 5.** After each new tree head the monitor either
   re-derives the whole tree from the entries it fetched, or — if it keeps no
   entries — "Fetch a consistency proof for the new STH with the previous STH
   … Verify the consistency proof … Verify that the new entries generate the
   corresponding elements in the consistency proof." The monitor's job is not
   to read the current head; it is to read the current head *against the
   previous one*.
3. **§8.3, Auditing.** "Auditing ensures that the current published state of
   a log is reachable from previously published states that are known to be
   good." Four properties "should be checked": the Maximum Merge Delay, the
   STH frequency count, **"the append-only property"**, and **"the
   consistency of the log view presented to all query sources"**. And the
   consequence: "if an audit fails, the auditor is in possession of signed
   proof of the log's misbehavior."
4. **§11.3, Misbehaving Logs.** Misbehavior includes "presenting different,
   conflicting views of the Merkle Tree at different times and/or to
   different parties", and "Violation of the append-only property … can be
   detected by multiple clients comparing their instances of the STHs."

The limits of the source, which this review keeps: RFC 9162 is an
*Experimental* protocol, its claims are about certificate misissuance, and its
monitors are **independent parties** holding their own copies. The plant's
check is the plant auditing itself, which is strictly weaker. What survives
the transfer is the auditor's *question* and the two properties it checks.

## What this plant already does

- **The ledger is an append-only tape with content-addressed records, and the
  code says so.** `parse_feed` (`src/mishe_tauftauf/feed.py:157-169`) rejects
  any entry whose sequence is not the expected contiguous one, and the record
  store `records/` is content-addressed and read-only. `feed.py:287-290`
  names the two tape generations the code supports: "Existing sites retain the
  old tape until their active writers exit. New and migrated sites use their
  own local chat.log."
- **The published reading already carries a population and a leaf set.** The
  `sense.ledger.evidence-binding` observation publishes `bound` (the number of
  post-clause outcomes it classified) and `event_sequences` (the tape
  sequences of those outcomes). Verified in the newest published sample
  (`discovery/scan-20261022T043406Z-11ed0a825eb44a8ea36647bce3914249.json`,
  created 2026-10-22T04:34:06Z): `bound=3249`, `event_sequences` n=3249,
  sequences 23…88869, `all_bad=199`, `state=unknown`.
- **Rows already carry a content address.** HEAD's reading at
  2026-10-22T05:06:08Z publishes 78 `owed_detail` rows, each with
  `sequence` *and* `record_sha256` (first row: sequence 285, role `witness`,
  `record_sha256=dace5789…`). CT's leaf hash already exists here, per row.
- **The verdict is composed, and its words claim different things.**
  `_ledger_evidence_binding` (`src/mishe_tauftauf/discovery.py:5644`) composes
  the state from a gate and a standing audit with four words — `verified`,
  `drift`, `settled`, `unknown` — and publishes seven bands that partition
  the standing audit, each with the relation it decides. The docstring is
  explicit that `verified` means "no violating row and no binding-relevant
  gap": every post-clause outcome binds an eligible, unedited, retained,
  singly-cited evidence file.
- **A verdict-consistency check exists and is running.**
  `checks/evidence-binding-verdict-consistency.py` refuses a published
  verdict that certifies a damaged ledger, refuses one resting on a gap that
  binds nothing, refuses one sitting over its own gate, and prints the
  exposure. It is exactly the auditor RFC 9162 §8.3 describes — with one
  difference that is the whole of this review: **it reads one sample.**
  `newest_sample()` (`:72-78`) takes `samples[-1]`.

So the plant has the leaves, the population, the content address, a composed
verdict and an auditor. What it does not have is the auditor's second
question: *is this view the same log as the last one?*

## What is new here

CT contributes one idea the plant does not embody: **the published view of an
append-only log must be a superset of the previously published view, and the
comparison is the auditor's job, not the log's.** §8.3 names it as a property
to check; §11.3 names a violation of it as misbehavior; §2.1.4 gives the
mechanism. The plant checks the *contents* of the current verdict three ways
over and never compares the current view with the previous one.

The measurement below shows the property is not decorative here: **the
plant's published view of its own ledger was replaced, not extended, on
2026-10-10, and the replacement was published as `verified`.**

### The published population was replaced (2026-10-10T15:50:11Z → 15:59:55Z)

Two adjacent published readings, both carrying the same observation id, ten
minutes apart, in the retained sample series:

| published reading | `bound` | leaf sequences | `all_bad` | `count_excluded` | `state` |
| --- | --- | --- | --- | --- | --- |
| 2026-10-10T15:50:11Z | **1980** | 41 299…65 279 (n=1980) | **462** | 77 | `drift` |
| 2026-10-10T15:59:55Z | **2** | 1…2 (n=2) | **0** | 0 | **`verified`** |

1 978 leaves left the published view in ten minutes, 462 violating rows with
them, and the verdict over the remaining two was `verified` — a clean ledger
certified over one part in 990 of the ledger it had just published. The
count that fell is the count that certifies: exactly the failure mode the
previous cell in this field measured from the other side.

### The cause is legible in the bytes

The site's tape begins

```
v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::
```

— a file created fresh at 15:51:56 on 2026-10-10, sequence 1, between the two
readings above. `parse_feed` requires contiguous sequences from 1, so the
sequence space is per-file and a resumed tape is not a form this code
supports: the replacement was necessarily a whole-file one. `feed.py:287-290`
documents a mechanism that fits the timestamp exactly ("New and migrated
sites use their own local chat.log"), and the classifier reads exactly one
path — `tape = home / 'chat.log'` (`src/mishe_tauftauf/outcome_events.py:89`)
— so a change of tape generation silently redefines the audited population.

**The cause of the replacement itself is UNKNOWN from the surviving bytes.**
The legacy `feed/` directory is gone, no backup tape exists in the site home
or elsewhere under `/home/mesh-home`, and no published record announces the
discontinuity. "Fits a documented migration" is not "is a documented
migration".

### What today's audit covers

HEAD's own classifier (`PYTHONPATH=src`, over the live site home, read at
2026-10-22T05:06:08Z) classifies **3 253** post-clause outcomes whose earliest
timestamp is **2026-10-10T15:54:09Z**. The clause time is
**2026-10-06T16:22:11Z** (`EVIDENCE_BINDING_CLAUSE_TIME`,
`discovery.py:5297`). So the standing audit, the bands and the verdict begin
**3 d 23 h 32 min** after the clause they exist to enforce:

- post-clause outcomes ever published: 1 980 (pre-migration) + 3 253
  (post-migration) = 5 233;
- post-clause outcomes inside today's audit: **3 253 (62.2 %)**;
- post-clause outcomes outside it: **1 980 (37.8 %)**, of which the last
  reading that counted them published **462 violating rows and 77 permanent
  losses**.

### The history is on disk but not audited

`records/` holds **4 418** `wall-outcome` records written before the tape
restart, every one carrying its `evidence.path`, `evidence.sha256`, `role`
and `kind` — everything the standing audit needs to re-admit a row, except
the two things that lived only in the tape: the **tape sequence** and the
**outcome timestamp**. The migration did not destroy the evidence bindings;
it removed them from the population the verdict is computed over, without
saying so.

### The leaf identity is a per-file counter

The old post-clause leaves were sequences 41 299…65 279. Today's tape has
reached sequence 89 057, and its entry 65 279 is a *different* outcome
(`2026-10-19T13:24:01.510483Z seed`). Every number in the old range now
names two different outcomes in the plant's published history. CT's answer
is §4.7's leaf hash: the identity of an entry is its content, not its
position in one generation of one file. The plant already has that hash —
`record_sha256`, published per row — but the published *leaf set* is
`event_sequences`, a counter that restarts.

### Nothing in the plant notices

Over the **1 565** published readings that carry the sense
(2026-10-06T20:28:23Z → 2026-10-22T04:34:06Z), computed over the retained
sample series:

- adjacent pairs where the later reading's leaf set is **not** a superset of
  the earlier's: **1** (the pair above);
- times `bound` decreases: **1** (1980 → 2).

And no check compares two readings at all: `evidence-binding-verdict-consistency.py:77`,
`ledger-band-second-reader.py:141` and `coverage-window-expiry.py:143-145`
all take `scans[-1]`; `seed.py:38` walks the series only to pick the newest.
The plant publishes the leaves and the population every scan and never once
asks whether this scan's leaves contain the last scan's.

### Limits, stated so the proposal is not read as a defect claim

(a) The cause of the tape replacement is UNKNOWN; the migration mechanism in
`feed.py:287-290` fits the timestamp but is not established. (b) The numbers
are one snapshot of a ledger that moves: `bound` was 3 249 in the 04:34:06Z
sample and 3 253 four minutes later. (c) The 1 980 missing outcomes cannot
be re-admitted mechanically — no sequence, no timestamp — so restoring
coverage is an owner decision, not a reader's repair. (d) The plant's auditor
is the plant auditing itself; CT's monitors are independent parties. The
plant's own discipline of independent re-derivation (witness and discover
re-derive these numbers separately) is the partial counterweight, and it does
not cover the view question, because both re-derive the *current* view.
(e) RFC 9162 is Experimental and its subject is certificate misissuance; what
is transferred is the auditor's question, not a theorem.

## Application

target: checks/evidence-binding-verdict-consistency.py

**Add the consistency of the published view to the plant's auditor.** CT
§8.3 names two properties the check does not test — the append-only property
across published views, and the consistency of the view presented to all
query sources. §2.1.4 gives the comparison, §11.3 gives its name. The check
already has the readings in hand (`:72-78` globs `scan-*.json` and takes the
last), and the reading already publishes the two fields the comparison needs
(`bound`, `event_sequences`). Concretely:

1. **Read the retained published readings in creation order** and compare each
   adjacent pair that carries the sense: the later reading's leaf set must be
   a superset of the earlier's, and `bound` must not decrease. Print the
   number of pairs compared and the number of violations. The comparison is
   over two published integers and one published list — no new byte is read
   beyond the sample files the check already opens, and no new file is
   written.
2. **A violation is a split view, and it is RED.** Print both timestamps,
   both populations, how many leaves disappeared, the verdict the later
   reading published, and exit 2 — because that verdict certified a ledger it
   did not measure. Against today's retained series this fires exactly once:
   2026-10-10T15:50:11Z → 15:59:55Z, 1 978 leaves lost, `verified` published
   over 2 outcomes, with 462 violating rows leaving the audit. The plant's own
   rule is that a published false green stays open until its cause is named;
   the check should not go green again because twelve days have passed.
3. **The live half, which is the recurrence detector.** The newest reading
   must be a superset of the reading before it. A second reset is then caught
   within one scan rather than never, and the finding routes to the same
   place the 2026-10-10 incident routes to.
4. **UNKNOWN, never OK, when the question cannot be decided.** Fewer than two
   published readings, or a neighbouring reading that cannot be parsed, gives
   exit 3 — the same "missing, stale or broken reading is UNKNOWN" rule the
   check already applies to its other three questions.
5. **Change nothing else.** The three existing questions, the exposure line
   and the exit codes (0 / 2 / 3) stay as they are; the new question adds a
   verdict line, not a new state word, and it does not touch
   `discovery.py`'s verdict, the bands, the clause or the refusal.

**The cost, stated honestly.** The check will be RED on the retained history
until the 2026-10-10 discontinuity has a disposition. That is the intended
behaviour of an auditor and it is a change in the plant's published state:
the owner may record the incident as an *explained historical discontinuity*
— a disposition with its evidence, not a deletion — after which the check
reports it as history and judges the live property alone. What the owner
should not do is narrow the comparison until the incident disappears; that
would be the "narrowing observation" this plant's own doctrine forbids.

**What this review does not land.** The deeper repairs belong to the roles
that own the organs, and this role does not edit them:

- the *reading* should publish its population's continuity (the population's
  start, and whether this view is a superset of the previously published
  one), so the discontinuity is visible in the reading itself and not only in
  a reader's reconstruction — `src/mishe_tauftauf/discovery.py`;
- the *outcome identity* should be the content address the plant already
  publishes per row (`record_sha256`) rather than a per-file counter that
  restarts, so a row stays resolvable across a tape generation — the
  classifier reads one tape path (`outcome_events.py:89`), and the audit's
  population should be derived from what the plant can still read, not from
  whichever tape happens to exist.

Both are named here and routed with this review; neither is applied by it.
