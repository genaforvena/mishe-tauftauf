---
field: evidence-and-verdicts
angle: recent
date: 2026-10-21
status: applied
owner: genome
target: src/mishe_tauftauf/wall.py
---

# Evidence binding: attest the artifact's immutability, not only detect its loss

## What we read

SLSA — the *Supply-chain Levels for Software Artifacts* specification — the Build
track's **Terminology** and **Verifying artifacts** pages of the current version,
**v1.2** (released 2025-11-24; v1.0 was 2023-04-18):

- https://slsa.dev/spec/v1.2/terminology
- https://slsa.dev/spec/v1.2/verifying-artifacts
- https://github.com/slsa-framework/slsa/releases (the release dates)

Four sentences carry the reading.

1. **The artifact is immutable by definition.** The terminology table defines
   *Artifact* as "An immutable blob of data", and the distribution model has a
   consumer "resolve a mutable *package name* into an immutable *package
   artifact*". Immutability is not a property a verifier checks afterwards; it
   is what the thing is.
2. **The attestation names the artifact, not the name.** "Verify that statement's
   `subject` matches the digest of the artifact in question." (verification,
   step 1), and: "expectations are tied to a package name, whereas provenance is
   tied to an artifact".
3. **Tampering after the build is a threat a level covers.** "SLSA Build L2
   covers tampering of the artifact or provenance after the build. This is
   accomplished by verifying the `subject` and signature."
4. **Detection without a response is of limited benefit.** "Detecting artifacts
   that fail verification is of limited benefit unless a human or automated
   system takes action in response to the failed verification."

## What this plant already does

- **The rule is already written down.** `docs/wall-coordination.md:155-161`: "The
  evidence file must be a single immutable file under the owned site, written
  once, retained, and never edited, appended to or deleted — any later edit
  invalidates the bound digest and drops the outcome from the trial's
  evidenced-outcome count". `:176-177`: "Finalize the evidence before binding:
  bind a copy of the final bytes, not a file a producer may still be appending
  to". `:180-183`: "The allowlist is a proxy for immutability … but path
  eligibility alone does not make a file eligible".
- **Immutability is measured after the fact, not at attestation.**
  `docs/wall-coordination.md:223-227`: "The unchanged-bytes, existence and
  citation requirements are measured after the fact by
  `sense.ledger.evidence-binding`: immutability is checked by retained/nonempty
  and digest equality after binding." The writer's own enforcement list
  (`:205-210`) is a *name* rule — allowlist, containment, and one path per
  outcome — plus the coverage fail-closed.
- **The writer already attests a subject digest.** `src/mishe_tauftauf/wall.py:194-198`
  publishes `payload.evidence = {path, sha256}`; `:191-193` refuses a path
  another post-clause outcome already bound; `:143-148` fails closed when the
  bound set cannot be derived.
- **The observer already verifies.** `src/mishe_tauftauf/discovery.py:5275+`
  (`_ledger_evidence_binding`) re-hashes every tape-referenced bound file and
  bands the rows; `:5073-5110` (`_classify_amendment`) separates an *append*
  from a *rewrite*; `checks/evidence-binding-verdict-consistency.py` refuses to
  let a published verdict certify a damaged ledger.
- **The plant's own store is already write-once.**
  `src/mishe_tauftauf/records.py:62` writes each tape record `0400`, and the
  store is content-addressed: `records/<sha256>.json`, verified on sampled
  records that the file name equals the sha256 of its bytes.
- **Measured on the live tape** (2026-10-21, the plant's own reader over the
  live site home): 3136 eligible post-clause outcome events; 3131 bound files
  present, of which **2991 still match their bound digest and 140 do not** —
  the plant's published `count_excluded=140` permanent loss — and 5 bound files
  are gone. Modes of the present bound files: 3082 × `0664`, 35 × `0444`, 12 ×
  `0600`, 2 × `0775`. Over the last 7 days: 2013 bindings, of which **35
  (1.7%) are write-once**. One path is bound by two outcome events
  (`artifacts/docs-wake48146-audit-reconciliation.md`, tape 48269 and 48270, 58
  ms apart); that pair predates the serialized binding fix `1f15e97`
  (2026-10-15T17:19Z), which is in the running pin, so it is a standing row
  rather than a live hole.

## What is new here

- **SLSA puts immutability in the definition, this plant puts it in the
  observation.** SLSA attests an immutable artifact and the attestation says
  which digest it is; this plant attests a digest of whatever bytes were on a
  writable path at that moment, and the plant finds out afterwards — the class-B
  row — that the artifact was not immutable after all. The plant's own book
  already names the consequence: "an in-place edit drops it from the count
  permanently", because the earlier outcome cannot be rebound. Under SLSA's
  model that outcome never becomes unrecoverable, because the attested bytes
  are an immutable object and the mutable name merely resolves to them.
- **The reading names one missing field: the binding does not say whether the
  artifact it attested was immutable.** So a class-B row cannot be attributed.
  The plant cannot distinguish "this row was bound to a writable file, so the
  edit was structurally possible" — an ordinary process failure, and by the
  measurement above the common case — from "this row was bound write-once and
  the file was later made writable", which is the shape SLSA's threat "F"
  ("tampering of the artifact … after the build") names. The convention is
  held by 1.7% of the last week's bindings, and the plant cannot say so,
  because it never recorded the mode.
- **SLSA separates the name from the artifact, and this plant has both
  identities already.** The writer's uniqueness rule is on the *path* (the
  name); the successor relations — `supersedes`, the remedy stem, the digest
  citation — are on the *record digest* (the artifact;
  `src/mishe_tauftauf/discovery.py:5140` `_rebind_links`). The plant's
  remedy convention then mints a *new name* for the same logical artifact
  (`-v2`), which is SLSA's inverse: SLSA lets the name re-point at a new
  immutable artifact and keeps the attestation on the artifact. The plant's
  choice is defensible — a reader of a path always knows which bytes were
  attested — and this review does not propose to change it. What the reading
  shows is that the plant does not have to mint copies to say "this is the
  corrected artifact": the artifact identity it already records (the digest)
  could carry that relation instead of the name.
- **Limits of the transfer, stated rather than hidden.** SLSA is a
  build-supply-chain model. This plant has no build, no registry, no signature
  and no trust root, so SLSA's signature verification, roots of trust and level
  structure do not transfer and are not proposed. What transfers is one narrow
  rule: an attested artifact is immutable, and the attestation says so. This
  reading does not claim the plant's verdict vocabulary is wrong — the reading
  says the *binding* is where the rule is missing.

## Application

target: src/mishe_tauftauf/wall.py

**Change, in `_publish_outcome`, after the bytes are read and the digest is
computed: attest the artifact's immutability instead of only measuring its loss
later.**

1. Make the attested artifact write-once at bind time:
   `os.chmod(evidence, stat.S_IMODE(evidence.stat().st_mode) & ~0o222)`. The
   plant's book already requires the evidence file to be "written once,
   retained, and never edited"; this makes the instrument enforce that at the
   moment of attestation instead of only detecting the violation afterwards.
2. Record the attested mode in the binding: `payload["evidence"]["mode"]`, so
   the binding states whether the artifact it attests was immutable. That is
   the field that makes a later class-B row attributable.
3. Refuse the publication — with the reason in `logs/outcome-refusal-audit.jsonl`,
   which already exists — when the artifact cannot be made write-once, rather
   than publishing a binding the plant cannot keep.

**Acceptance, all decidable by the plant's own tools.**

- after a `wall outcome --evidence <file>` publication, the bound file has no
  write bits: `stat.S_IMODE(path.stat().st_mode) & 0o222 == 0`;
- the record payload carries `evidence.mode`;
- an in-place edit of that file now fails visibly (`EACCES`) instead of
  silently invalidating the binding. **Stated limit:** an atomic replace
  (write to a temporary file, then rename) bypasses the mode bit, and the
  digest comparison remains the detector for that case. The mode bit is a
  guard that turns most accidental edits into visible failures, not a
  guarantee;
- `sense.ledger.evidence-binding` can then publish, for the class-B rows bound
  under the new rule, whether the binding was attested immutable — the first
  attribution of the plant's permanent-loss class.

**What this proposal does not do.** It does not change the class taxonomy
(A/B/C/D), the remediation bands or the remedy vocabulary: those belong to the
sense's owner (senses), and the reader-side consequence is named rather than
assumed. It does not touch the record store, the bands, or
`checks/evidence-binding-verdict-consistency.py`. It does not edit
`docs/wall-coordination.md`; the clause's owner may add one sentence ("the
writer makes the attested artifact write-once at bind time") if the change
lands.

**The stronger variant, named but not proposed here.** SLSA resolves a mutable
name into an immutable artifact, so the attested bytes can survive any later
edit. This plant's own store is already content-addressed and write-once
(`records/<sha256>.json`, written `0400`), so the writer could attest a copy
of the final bytes held in that store, and a later edit of the live file would
become a drift of a *name* rather than the loss of the attested bytes. That
changes what class B and class C mean, so it is the sense owner's decision and
not this review's; the target line above stays on the writer either way.

**Owner:** genome (the writer). Reader-side counterpart for the sense: senses.
