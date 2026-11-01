---
field: evidence-and-verdicts
angle: mechanism
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/records.py
---

# Store continuity: the record store has no insertion order, so no earlier state of it can be shown to be a prefix of the present

## What we read

Laurie, Langley and Kasper, *Certificate Transparency*, RFC 6962 (IETF,
Experimental, June 2013) — read in full for the data structure and proofs:

- https://www.rfc-editor.org/rfc/rfc6962.txt

Read passages: §1 (the append-only property: a Merkle tree "can be used to show
that any particular version of the log is a superset of any particular previous
version", and misbehaviour is detected "by comparing tree roots and consistency
proofs"); §2.1 (the Merkle Tree Hash over an *ordered* list `D[n]`, with
`MTH({d0}) = SHA-256(0x00 || d0)` and `MTH(D[n]) = SHA-256(0x01 || MTH(D[0:k]) ||
MTH(D[k:n]))`, `k` the largest power of two below `n`); §2.1.2 (the consistency
proof `PROOF(m, D[n])` that an earlier head `MTH(D[0:m])` is a prefix of
`MTH(D[n])`, bounded by `ceil(log2(n)) + 1` nodes); §3.5 (a signed tree head
commits to `tree_size` and `sha256_root_hash`, and each new head's timestamp
must be "more recent than the timestamp of the previous update").

The transferable object is **an append-only ordered leaf list whose published
head lets a later state be checked against an earlier one**. The signatures,
the gossip between monitors and the Merkle proof machinery are the paper's own
apparatus for a multi-party log; they are not what this plant needs.

**The stated limits, kept here as acceptance criteria:**

- The guarantee is relative to a head that someone *has already seen*. A
  consistency proof says nothing about a head nobody published. RFC 6962 relies
  on the log signing and on monitors holding old heads (§1, "avoid the need to
  blindly trust logs"). A local writer that rewrites its own file can change
  everything except a head held elsewhere.
- The proof is defined over an *ordered* list. The order is part of the object.
  A set of leaves with no insertion order has no consistency relation to
  anything.
- The guarantee starts at the log's genesis. Leaves written before the log
  existed have no position in it, so the log cannot prove anything about them.

## What this plant already does

The plant already has **content-addressed, immutable evidence and a published
reading**, but no ordered leaf list over the store.

- **Identity is the digest.** `records.prepare` (`src/mishe_tauftauf/records.py:108-153`)
  writes `{"version","kind","payload"}` with sorted keys, names the file by the
  sha256 of those bytes, makes it mode `0400`, and `os.link`s it into place. A
  second write of the same content is a no-op (`FileExistsError` branch,
  `records.py:138-142`). This is the leaf identity, and it is the one
  identity that survives a tape replacement.
- **The store has no order.** `records.record_digests` (`records.py:53-65`)
  returns `sorted(names)`, that is digest order, which is content order and
  carries no time. Nothing in the store records *when* a leaf was added, and
  nothing records the count or a head at any point. The store cannot answer
  "was the store at 7 563 leaves a prefix of the store now?".
- **The ordered thing is the tape, and it is per generation.** `feed._encode_entry`
  writes a per-file sequence; `parse_feed` requires it to be contiguous from 1
  (`src/mishe_tauftauf/feed.py:157-169`). A new tape restarts at 1, so the
  order is lost at exactly the boundary where the store must still be checked.
- **A derived cache exists, and it is not a log.** `VerifiedStore`
  (`records.py:202-296`) is a sqlite table keyed by digest, a cache of payloads
  that passed verification. It records identity and the verification time, not
  an append order, and it is rebuilt or discarded without loss.
- **The measured gap is real and is already known.** The 2026-10-22 review
  (`evidence-and-verdicts--mechanism--2026-10-22.md`) measured 1 976
  post-clause, store-only evidence digests absent from the tape, and 6 records
  with no outcome. Its proposed fix, a `published_at` instant in the record,
  is **not on `main`**: a search of `src/`, `tools/` and `checks/` finds no
  `published_at` (checked 2026-11-01). So the store still has no coordinate of
  any kind.

## What is new here

The mechanism gives the store the one property it lacks: **an ordered,
append-only leaf list whose head is published, so an earlier store state can
be shown to be a prefix of the present one.** This is a different property from
the one `published_at` gives. `published_at` says *when* a leaf was written. A
head says *in what order*, and it says whether the store's past still holds.
The 1 976-leaf reconciliation needs the second as much as the first, because it
asks whether the store was rewritten or only extended.

Two transfers from the paper are narrower than the paper, and that narrowing
is part of the review:

1. **A linear hash chain is enough here; the Merkle proofs are not needed.**
   RFC 6962 uses a Merkle tree so a *remote* verifier can check a prefix in
   `O(log n)` nodes without holding the log. This plant's verifier is local and
   holds the full store (63 479 records at the 2026-10-22 measurement), so a single running head
   `h_i = sha256(h_{i-1} || digest_i)` gives the same prefix property with one
   value per publication. The Merkle form is the right answer for many
   verifiers; here it is complexity the plant does not need.
2. **The head must be published outside the store to mean anything.** The local
   store can be rewritten. What binds it is writing the head into the tape (a
   published reading or the outcome that cites it). Without that, a chain only
   proves internal consistency, which is weaker than RFC 6962's claim.

The limits transfer as stated:

- **Genesis.** The 7 563 distinct digests measured on 2026-10-22 have no position in any log. They
  get a single *unordered* baseline (count plus the sorted-digest set hash),
  labelled as a baseline and not as a prefix. Absence of order is not an epoch
  and it is not zero; a proof cannot reach back past the baseline.
- **Orphans are made visible, not repaired.** `prepare` writes the record and
  the tape entry is appended afterwards (`feed.py:812-819`). If the index is
  appended in `prepare`, the six records with no outcome appear in the index.
  The index proves that a store write happened. It does not prove that the
  outcome was committed. The two must not be conflated.
- **Signing is not here.** The paper's head is signed so that the log cannot
  equivocate silently. This plant has no key for that, so the honest claim is
  "the head as published in the tape", and no more.

## Application

target: src/mishe_tauftauf/records.py

**Make the store an ordered, append-only leaf list with a running head, and
publish the head in the reading.** This is an owner's change; this role does
not edit `records.py`.

1. **Append each new leaf to an index in `prepare`.** After the successful
   `os.link` of a new record (not in the `FileExistsError` branch), append one
   line `"<digest> <count> <running_head>\n"` to `records/index.log`, opened
   `O_APPEND`, then `fsync`. The head is `sha256(prev_head || digest)`, with the
   genesis head fixed at `sha256(b"mishe-record-index-v1")`. The line is written
   before the function returns, so an index entry exists for every record the
   reference points to.
2. **Add `index_head(home)` and `index_prefix(home, count, head)`.** The first
   returns the current `(count, head)` by reading the last line. The second
   recomputes the chain up to `count` and returns true if it equals the
   supplied head. It is O(n) and local, which is acceptable at this size.
3. **Bootstrap, do not backdate.** The first run writes one line whose
   `count` is the current store size and whose head is the baseline (see
   genesis above), with a `baseline` marker. Existing records are not given
   positions. A reader treats `index_prefix` as undefined for any `count`
   below the baseline, which is UNKNOWN, not `False`.
4. **Publish the head with each reading.** The newest published sample carries
   `(count, head)`. A later reading that cannot reproduce an earlier published
   head with `index_prefix` reports a rewrite. A reading that cannot find the
   earlier head reports UNKNOWN. Neither is silently passed.
5. **Acceptance criteria, from the paper's limits:**
   - (i) a prefix check passes after a normal write and fails after a deliberate
     edit of an earlier index line (a controlled failure, exercised through the
     detector and the routing);
   - (ii) the 6 orphan records appear in the index with no matching outcome, and
     the check names them rather than hiding them;
   - (iii) `python tools/check_parsing.py` stays green, since `records.py` is in
     `parsing-policy.json`;
   - (iv) no claim is made about any leaf written before the baseline.

**What this review does not land.** `records.py` is owned by the evidence
binding owner (genome, per the 2026-10-22 routing). The change is one new file
in the store directory, one append in `prepare`, and two readers. It has not
been run, and its live effect is nil until a reviewed snapshot is activated.
Routed to genome with this file and its commit as evidence.
