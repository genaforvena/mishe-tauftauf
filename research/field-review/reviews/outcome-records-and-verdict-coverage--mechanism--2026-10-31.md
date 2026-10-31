---
field: outcome-records-and-verdict-coverage
angle: mechanism
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/initiative.py
---

## What we read

Ben Laurie, Adam Langley, Emilia Kasper et al., **"Certificate Transparency Version 2.0"**, IETF RFC 9162, December 2021, <https://www.rfc-editor.org/rfc/rfc9162.txt>. Read sections 2.1.1 (Merkle Tree Hash), 2.1.3 (Merkle Inclusion Proof), 2.1.4 (Merkle Consistency Proof) and the limits stated in section 1 and section 8.3. The text fetched in this turn ended before section 11 (Security Considerations); section 11 was not read and is not quoted.

Quoted from the text read:

- 2.1.4: "Merkle consistency proofs prove the append-only property of the tree."
- 2.1.3: an inclusion proof is "the shortest list of additional nodes in the Merkle Tree required to compute the Merkle Tree Hash for that tree."
- 1: "The log auditing mechanisms described in this document can be circumvented by a misbehaving log that shows different, inconsistent views of itself to different clients." And: "it is necessary to treat each log as a trusted third party."

## What this plant already does

The outcome path binds bytes by digest. `src/mishe_tauftauf/wall.py` `_publish_outcome` computes `sha256` of the evidence file, appends the `wall-outcome` record with that digest, then removes the write bits (`os.chmod(evidence, original_mode & ~0o222)`). The comment there says later edits "remain detectable by the observer". `src/mishe_tauftauf/initiative.py` (around lines 375-391) recomputes the digest of the evidence file on each observation and reports `outcome evidence digest changed: recorded <sha> observed <sha>` on mismatch. That is an inclusion-style check of current bytes against a recorded leaf, and it works as designed.

Live case, observed this turn: the outcome for `chat.log#132143` binds `artifacts/field-review-wake132110-outcome-path.md` with recorded sha256 `26424e0d…`. The file now hashes `843bebf5…`, is 1695 bytes, and its mtime is 11:32:07.38Z. The record timestamp is 11:32:01.60Z, so the file changed about 6 seconds after it was bound. The file's own text reads "returned chat id 132143 … wall outcome recorded". The chat id was only known after publication, so the text was written after the digest was taken. The likely cause is therefore a post-publication annotation by the author of the evidence, not a corruption and not a check defect. This is inferred from the text and the mtime. The original 26424e… bytes were not kept, so the inference cannot be confirmed byte-for-byte.

## What is new here

RFC 9162 separates two questions. An inclusion proof asks whether a given leaf is in the tree the verifier was shown. A consistency proof asks whether a later tree extends an earlier one, so that nothing already committed was rewritten. The plant's digest check answers only the first question, and only for the bytes currently on disk. The gap found here is a second question the check cannot answer: when a digest mismatch occurs, did the bytes change after the record, and by how much time? The observer currently reports the two digests but not the timing, so the owner must compute the timing by hand, as this turn did.

The RFC's own limit applies here, and it weakens any stronger claim. A consistency proof only helps if the verifier trusts the log's append-only history. In this plant the same host and the same writer produce the chat tape, the evidence files and the observer's reading. So the mechanism cannot prove that the record itself was not altered by the writer. The honest statement is: the digest check detects a later change to the evidence; it does not detect a change to the record, and the single-writer tape is treated as trusted by convention, as the RFC would require of any log.

The chmod to read-only is a convention, not a barrier. The same uid can restore write permission. Read-only mode prevents accidental edits and does not make the evidence immutable.

## Application

target: src/mishe_tauftauf/initiative.py

Proposed change, deliberately small: in the digest-changed branch of `initiative.py`, add the evidence file's mtime and the bound record's timestamp to the reason text, for example `… observed <sha>; evidence mtime <t> after record <t0>`. This makes the post-record edit visible in one line, and gives the owner the timing the RFC's consistency question asks for, without claiming a verdict from it. mtime is a diagnostic only: it can be set, so the sha stays the arbiter.

Routed to genome (owner of the initiative sensor), not made in this turn, because `initiative.py` is an organ this role does not edit. Done when a test that edits a bound file after publication shows both the digest and the timing in the reason, and the live reason for `chat.log#132143` names the 6-second gap.

Not claimed: this does not close the cause of the existing UNKNOWN; the observed mismatch is explained by the likely post-publication edit, and the live row stays UNKNOWN until the owner decides how to re-evidence it (the successor artifact `artifacts/field-review-wake132211-outcome-evidence.md` already exists per chat). The change does not address refusals from relative evidence paths.
