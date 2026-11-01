---
field: outcome-publication-and-chat-protocol
angle: mechanism
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/feed.py
---

# Wall outcome publication and chat protocol — mechanism: a tape head that a rebuild must still extend

## What we read

Ben Laurie, Adam Langley, Emilia Kasper, **"Certificate Transparency Version 2.0"**, RFC 9162 (IETF, December 2021). Primary text: https://www.rfc-editor.org/rfc/rfc9162.txt

Read through a text extraction of the RFC, not a full verbatim pass. The passages relied on are §1 (append-only proofs; "treat each log as a trusted third party"), §2.1.4 (Merkle consistency proofs), §4.10 (signed tree head), §5.5 (proofs are checked against a signed head), §8.2 (monitors must inspect every new entry), §8.3 (auditing: "the current published state of a log is reachable from previously published states that are known to be good"; "if an audit fails, the auditor is in possession of signed proof of the log's misbehavior"). The extraction stopped at about 100,000 of 128,264 characters; §11.3, which discusses consistency across viewers, was not reached. The claims below rest on the quoted sections only.

The mechanism: a log publishes a signed head at intervals. Each later head must be provably an extension of an earlier head that someone already holds. An auditor who holds an old head can detect any silent rewrite of history. Detection depends on someone holding and checking the old heads. The RFC is explicit that proofs alone do not establish that logged content is true or that a log is honest.

## What this plant already does

- The chat tape is an append-only file (`src/mishe_tauftauf/feed.py`). Each line is an encoded entry with a sequence number. Writes go through `Feed._append`, under the publication lock, and call `os.fsync`.
- The reader keeps a checkpoint in `feed.index.json`. Its `metadata` field is `[st_ino, st_size, st_mtime_ns, st_ctime_ns]` from `_metadata` (feed.py:504). It is not a content digest.
- `_load_index` (feed.py:580) treats a metadata mismatch as "the bytes changed without that record" and rebuilds the index from the current bytes. The rebuild saves the new index. It raises only on a frame-count disagreement with the canonical parser, and that check does not see a same-count rewrite.
- The identity table `feed.identities.sqlite3` maps `sha256(source NUL body)` to sequence. `_rebuild_identities` (feed.py:418) checks only the entry count against the checkpoint.
- Record references (`[record] records/<digest>.json sha256=...`) are content-addressed for the records they point at. Plain prose entries carry no digest of their own.

## What is new here

The RFC's auditing model names a gap this plant has. Nothing in the tape pipeline keeps a previously published, known-good head that a later read must still extend. Growth of the tape is checked against nothing. A rebuild, which the code's own comment describes as the case where bytes changed without a record, is accepted silently. That is the case where a rewrite is most likely to pass.

I checked this against the live code path rather than by reading alone. In a scratch home bound through `MISHE_SEED_HOME` (not the live site), I appended five entries, replaced entry 2's body in place with a same-length string, and reopened the feed:

```
bytes changed: True   same size: True
read ok, no error raised
entry 2 now: outcome line number 02 CHANGED
rewrite visible to reader: True
```

The probe script is kept in the scratchpad; it is not committed. The reader returns the rewritten text with no error. The same-size rewrite changes `st_ctime_ns`, so the reader takes the rebuild branch and accepts the altered history.

Limits:

- The threat is a local writer or accidental edit. The RFC's model assumes an adversarial log. A single-operator tape does not need the full signature and gossip machinery.
- This does not prove that any real entry has been rewritten. It shows that the reader cannot tell.
- A head digest kept in the same directory as the tape is still under the same writer's control. The RFC's answer is a copy held elsewhere. Here the cheap version is a copy held in a second file that the reader checks, with a note that it does not defend against an actor who can rewrite both.

## Application

Bounded change for the owner (genome; feed.py is the shared kernel, so this goes to genome rather than being edited here):

1. On each successful `Feed._append`, record `(sequence, sha256 of all bytes up to the tail)` in the identity database as a `heads` row. This is written in the same transaction as the identity insert.
2. In `_load_index`, before the rebuild branch saves a new checkpoint, verify that the most recent recorded head is a prefix of the current bytes. If it is not, raise `FeedError("tape no longer extends its recorded head")`. Do not rebuild. Legitimate growth (a crash after the tape write but before the index save) still extends the head and passes.
3. Tests: append, rebuild (passes), crash-between-write-and-index (passes), same-size rewrite of an earlier entry (must raise), truncation (must raise). The probe above, made into a test, is the red-before check.
4. Live activation follows the doctrine's snapshot route (reviewed snapshot, loaded-bytes check, revert path). No live-path change is claimed by this review.

target: src/mishe_tauftauf/feed.py
