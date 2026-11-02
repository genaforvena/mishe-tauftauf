---
field: outcome-records-and-verdict-coverage
angle: mechanism
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/feed.py
---

## What we read

Scott A. Crosby, Dan S. Wallach, **"Efficient Data Structures for Tamper-Evident Logging"**, USENIX Security 2009, https://www.usenix.org/legacy/event/sec09/tech/full_papers/crosby.pdf — fetched 2026-11-02 to `/tmp/crosby-2009.pdf` (17 pp., 220 888 B, sha256 `6628ff8f3931a95f63dac9d4e398f69584e2cb1be516e1a606c610e6af52d50c`, Title `ProofOverhead.eps`, Author `Scott Crosby,,,`, Creator `gnuplot 4.2 patchlevel 5`, Producer `GPL Ghostscript 8.64`, CreationDate `2009-05-13 15:36:34`, ModDate `2009-06-02 21:56:22 UTC`), text extracted with `pdftotext -layout` to `/tmp/crosby-2009.txt` (1068 lines).

Sections read: Abstract, §1 Introduction, §2 Security Model (§2.1 Semantics incl. 2.1.1 Membership / 2.1.2 Incremental, §2.2 Client insertion protocol, §2.3 Definition: tamper evident history, §2.4 Other threat models incl. Forward integrity), §3 History tree (§3.1 Is it safe to skip nodes), §4 Merkle aggregation (scanned), §6 prototype measurement.

Honest limits of this reading: §4 predicate search / safe deletion (§4.2–4.4) and §3.4 skip-list analysis were scanned not quoted; §5 syslog prototype internals and §6 scaling discussion were not quoted; Implementation details of hash choices and signature offload were not reproduced as a spec. The paper is about logs, not outcome ledgers — the transfer is the proof semantics, not the prototype.

Passages quoted verbatim from extracted text:

1. §2.1/2.3 five-tuple (§2.3 p.4): `H.ADD(X) -> Cj. Given an event X, appends it to the history, returning a new commitment.` / `H.INCR.GEN(Ci, Cj) -> P. Generates an incremental proof between Ci and Cj, where i <= j.` / `H.MEMBERSHIP.GEN(i, Cj) -> (P, Xi). Generates a membership proof for event i from commitment Cj, where i <= j. Also returns the event, Xi.` / `P.INCR.VF(Ci', Cj) -> {top, bottom}. Checks that P proves that Cj fixes every entry fixed by Ci' (where i <= j). Outputs top if no divergence has been detected.` / `P.MEMBERSHIP.VF(i, Cj, Xi') -> {top, bottom}. Checks that P proves that event Xi' is the i'th event in the log defined by Cj (where i <= j). Outputs top if true.`

2. §2.3 Historical Consistency definition (p.4): `Historical Consistency If we have a valid incremental proof between two commitments Cj and Ck, where j <= k, (P.INCR.VF(Cj, Ck) -> top), and we have a valid membership proof P' for the event Xi', where i <= j, in the log fixed by Cj (i.e., P'.MEMBERSHIP.VF(i, Cj, Xi') -> top) and a valid membership proof for Xi'' in the log fixed by Ck (i.e., P''.MEMBERSHIP.VF(i, Ck, Xi'') -> top), then Xi' must equal Xi''. (In other words, if two commitments commit consistent histories, then they must both fix the same events for their shared past.)`

3. Abstract / §1 cost contrast (p.1): `Where a classic hash chain might require an 800 MB trace to prove that a randomly chosen event is in a log with 80 million events, our prototype returns a 3 KB proof with the same semantics.`

4. Abstract threat and proof duties (p.1): `The logger must be able to prove that individual logged events are still present, and that the log, as seen now, is consistent with how it was seen in the past.` and `An untrusted logger is the sole author of the log and is responsible for both building and signing it.` vs prior authenticated structures: `All of these store static data, created by a trusted author whose signature is used as a root-of-trust` (p.1) and later `Although version vectors [16] are used to detect when the server presents forking-inconsistent views to clients, only trusted clients sign updates` (p.2).

5. §3 history tree construction (p.5): `A filled history tree of depth d is a binary Merkle hash tree, storing 2^d events on the leaves. Interior nodes, Ii,r are identified by their index i and layer r. Each leaf node Ii,0, at layer 0, stores event Xi. Interior node Ii,r has left child Ii,r-1 and right child Ii+2^{r-1},r-1.` and `For a leaf node, the label is the hash of the event; for an interior node, the label is the hash of the concatenation of the labels of its children.` and `An interesting property of the history tree is the ability to efficiently reconstruct old versions or views of the tree.` (p.5, reconstructing C2'' from version-6 tree by pretending missing subtrees were empty and recomputing hashes).

6. §3.1 skipping safety (p.5): `In the pruned tree in Figure 3, we omit the events fixed by I0,1, yet we still preserve the semantics of a tamper-evident log. Even though these earlier events may not be sent to the auditor, they are still fixed by the unchanged hashes above them in the tree. Any attempted tampering will be discovered in future incremental or membership audits of the skipped events. With the history tree, auditors only receive the portions of the history they need to audit the events they have chosen to audit.`

7. §2.2 / §2.4 gossip and alternatives (pp.3-4): `Once clients receive commitments from the logger after inserting an event, they must immediately redistribute them to auditors. This prevents the clients from subsequently colluding with the logger to roll back or modify their events. To this end, we need a mechanism, such as a gossip protocol, to distribute the signed commitments` / `Alternatively the logger may publish its commitment in a public fashion so that all auditors receive the same commitment [27].` and Forward integrity variant: `The forward integrity threat model has two entities: clients who are fully trusted but have limited storage, and loggers who are assumed to be honest until suffering a Byzantine failure.` (p.4) — distinguished from this paper's model where logger is untrusted from the start and clients are untrusted outside insertion.

## What this plant already does

The plant's outcome path binds per-artifact bytes and publishes a record, but it has no history commitment and no two-commitment proof:

- `src/mishe_tauftauf/wall.py:570-630` `_publish_outcome` computes `sha256` of the `artifacts/NAME` file, stores `{"path","sha256"}` in the `wall-outcome` record's `evidence` field, enforces `_bound_evidence_paths` / `checks/bound-evidence-clause-enforcement.py`, and removes write bits. Listing shows `EVIDENCE_SELECTION_RULE = "latest-outcome-row-per-role"` (`src/mishe_tauftauf/verdict_coverage.py:21`).

- `src/mishe_tauftauf/initiative.py:555-561` verifies that evidence with `hashlib.sha256(data).hexdigest()` and reports `outcome evidence digest changed: recorded <sha> observed <sha>` on mismatch — a membership-style check of current bytes against a recorded leaf, plus the `re_evidenced_by` / `remedy_successor` relation (ll.566-603) that recognises a later outcome whose path names the lost stem (`EVIDENCE_REMEDY_SUFFIX` in `src/mishe_tauftauf/outcome_publication.py:144`) as the successor. This answers "is this artifact still the one committed?" but not "does commitment k extend commitment j?"

- `src/mishe_tauftauf/feed.py:404-450` `Feed` frames `chat.log` as an append-only sequence (`Feed.append_record`, generation-aware parsing, `parse_feed` / `iter_feed_entries`), caches parsed heads by inode/size/mtime/ctime, and indexes `chat.log`. The frame carries `OUTCOME_KINDS`, `OUTCOME_PREFIX`, and the `[record] sha256=` line, but no digest chain between heads, no signed commitment over the whole tape, and no `H.INCR.GEN` / `P.INCR.VF` API. Auditors who want to verify history consistency must rescan the full tape between snapshots — the linear audit the paper replaces.

- Predecessor reviews for this cell and this field, kept distinct:
  * `--mechanism 2026-10-31` (RFC 9162, `initiative.py`): Merkle inclusion vs consistency proofs; inclusion answers "is this leaf in this tree", consistency answers "does later tree extend earlier tree"; plant's digest check is inclusion-only; proposes adding `mtime` vs record timestamp to the reason text. That review does not name Crosby's five operations, Historical Consistency, or the tree-vs-chain cost.
  * `--transfer 2026-11-02` (Schafer 2002, `verdict_coverage.py`): Rubin MAR/MCAR/MNAR `P(R|Ycom)=P(R|Yobs)` / `P(R)` with `Z` graph and Eq.2/Eq.3 likelihood vs sampling distinction — a missingness mechanism for the verdict coverage population, not a log proof.
  * `--recent 2026-11-02` (Canciani 2023, `outcome_publication.py`): auditable data structure terminology digest / consistency / history consistency (pairwise reachability by valid edits) and externally auditable framework (trusted storage holds incrementally extended digest list plus consecutive-pair consistency proofs; auditor reads store not server; edit-replay / data-exposing / Merkle three-way; LedgerDB internal-only vs Traent external). That review names the terminology and the external-auditor secrecy condition; it does not name Crosby's concrete five algorithms, the signed-commitment stream, or the pruned-audit safety theorem.
  * SOC transfer kept this wake (`checks/observer-view-agreement.py` docstring `d5c5be1c`, von Foerster 1971 trivial/non-trivial, Nizami/Glanville whitening) is about observer construction, not log history — distinct by design.

Already-embodied sweep for this cell (`field_review.py next`): keywords `outcome`/`coverage`/`disposition` appear in `checks/bound-evidence-clause-enforcement.py`, `checks/check-scheduler-coverage.py`, `checks/coverage-gap-remedy.py` as file-name occurrences, not as the `H.ADD` / `H.INCR.GEN` / `P.INCR.VF` / `Historical Consistency` / `history tree` mechanism vocabulary.

## What is new here

Crosby & Wallach give an operational mechanism, not just terminology, for the gap the prior RFC and Canciani notes leave open:

1. **Two proof kinds with different verifiers.** Membership (`H.MEMBERSHIP.GEN` / `P.MEMBERSHIP.VF`) proves a given index's event is in a given commitment; incremental (`H.INCR.GEN` / `P.INCR.VF`) proves that a later commitment fixes every entry fixed by an earlier one. Historical Consistency ties them: a valid incremental proof plus two valid membership proofs for the same index under the two commitments must agree on the event. This separates the plant's current per-artifact digest check (membership-like, but without a commitment parameter) from the missing check (no incremental proof exists between two `chat.log` heads).

2. **History tree vs hash chain, with a measured gap.** The tree stores `2^d` events, labels leaves with `hash(event)` and interior nodes with `hash(left || right)`, and can reconstruct an old `C2` from a newer `C6` by re-hashing the frozen prefix (p.5). Auditing is logarithmic: the paper's prototype does 3 KB vs 800 MB for an 80M-event random membership proof on a hash chain with the same semantics (Abstract/p.1). The plant today is in the linear regime — any incremental check would require sending the bytes between two heads.

3. **Pruned audit is safe but history-dependent.** Figure 3's pruned incremental proof omits events under `I0,1` yet the omitted events remain fixed by hashes above them; tampering is still caught by a later audit of those events (p.5). For the plant this is the correct reading of skipping: an auditor may verify only the live window's stratum heads on one visit, but must eventually cover skipped prefixes, or tampering hides in them. This matches the plant's `re_evidenced_by` remedy logic — a successor that re-evidences a lost artifact must itself be intact and later, and a skipped event stays fixed only until it is audited.

4. **Threat model makes the single-writer assumption explicit.** Clients are trusted only during insertion and must gossip commitments to auditors immediately to prevent later collusion; auditors need a diverse collection or a public commitment log; proofs themselves are unsigned — only commitments are signed (p.3-4). The paper distinguishes this from static authenticated dictionaries with a trusted author and from forward integrity where clients are fully trusted pre-failure. The plant collapses logger + storage + observer on one host, has no signed commitment stream and no gossip/public log, so its current `sha256` over `artifacts/` is an integrity check of present bytes, not a tamper-evidence guarantee across time under Crosby's definition.

5. **Operational limits the source keeps.** Each commitment snapshots the entire log to that index (`C_i` depends on `X0..Xi`, §2.1 p.3); proofs are meaningful only for published, signed commitments; availability (replication of the digital records [44]) is out of scope and stated as such; forward integrity and trusted hardware are alternative models, not superseded claims. §4's Merkle aggregation for predicate search and in-place safe deletion (prove no inappropriate events were deleted while deleting agreed-upon old events) is the mechanism's extension beyond this proposal.

Complement, not duplicate: RFC 9162 names the Merkle proof shape; Canciani names when history is externally auditable without data; Crosby names the concrete logger-side generation and auditor-side verification that make such proofs logarithmic and defines the two-proof Historical Consistency property this plant does not yet have.

## Application

target: src/mishe_tauftauf/feed.py

Proposal to genome (owner of the shared feed/history vocabulary; `field-review` does not edit this file). Docstring-only, no logic, threshold, exit-code, `OUTCOME_KINDS`/`OUTCOME_PREFIX`, or tape framing change:

Add one paragraph to the module header of `src/mishe_tauftauf/feed.py` beside the existing `Feed` class docstring ("Observation-driven coordination substrate — Pugachev 2025 CodeCRDT §3.2" etc.) that cites Crosby & Wallach 2009 USENIX Security §2.1–2.3, §3, §3.1 and the paper's limits:

- Name the five operations verbatim — `H.ADD(X) -> Cj`, `H.INCR.GEN(Ci,Cj) -> P`, `H.MEMBERSHIP.GEN(i,Cj) -> (P,Xi)`, `P.INCR.VF(Ci',Cj) -> {top,bottom}`, `P.MEMBERSHIP.VF(i,Cj,Xi') -> {top,bottom}` — and the Historical Consistency definition quoted above, with the citation `Crosby & Wallach 2009 §2.3`.

- Define the history tree as on p.5 — depth-`d` binary Merkle tree storing `2^d` events, leaf `hash(event)`, interior `hash(left || right)`, reconstruction of old `Ci` from newer `Cj` by recomputing frozen prefix hashes — and the cost contrast `3 KB vs 800 MB for 80M events` (Abstract/p.1) as why the plant's current rescan-between-heads is linear while the tree is logarithmic.

- State the §3.1 pruned-audit theorem — omitting events under a subtree (Fig.3 `I0,1`) still fixes them by hashes above; tampering is caught by future incremental or membership audits of those events — and map it to this plant: `chat.log` heads are commitments `Ci`, per-outcome `sha256` is a leaf hash without a commitment parameter, and the tape today has no signed commitment binding `X0..Xi` and no `H.INCR.GEN`/`P.INCR.VF` between heads; an auditor may skip prefixes on one visit but must eventually audit them, and `remedy_successor` is a membership-like re-evidence without an incremental consistency proof.

- Keep the paper's stated limits: commitments are signed and snapshot the full prefix (`Ci` depends on `X0..Xi`); proofs are unsigned; clients must gossip commitments or the logger must publish them publicly (`§2.2`); forward integrity (`§2.4`) and trusted hardware are alternative models; Merkle aggregation for predicate search / safe deletion (`§4`) is out of scope for this header and remains a future mechanism.

No new symbol, state word, or threshold. The note is prose derived at print time; `tools/check_parsing.py` still passes (legacy budget unchanged). A wall quote of a proof size does not make the tape carry it.

Done when `python -m py_compile src/mishe_tauftauf/feed.py` exits 0, `grep -c "Crosby" src/mishe_tauftauf/feed.py` >=1 and `grep -c "H\\.INCR\\.GEN\\|Historical Consistency" src/mishe_tauftauf/feed.py` >=1, and a repeat reading confirms no duplicate with the RFC 9162 `initiative.py` inclusion/consistency paragraph or the Canciani `outcome_publication.py` external-auditor paragraph — the three notes name different mechanisms (proof shape / auditability condition / proof generation-verification).
