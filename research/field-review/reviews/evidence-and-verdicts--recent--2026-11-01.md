---
field: evidence-and-verdicts
angle: recent
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/verdict_coverage.py
---

# Evidence verdicts: a receipt proves registration, not the truth of what was registered

## What we read

IETF SCITT Working Group, *An Architecture for Trustworthy and Transparent Digital
Supply Chains*, `draft-ietf-scitt-architecture-22` (October 2025, the newest
revision found; still an Internet-Draft, not an RFC):

- https://datatracker.ietf.org/doc/html/draft-ietf-scitt-architecture-22

Read through a fetch-and-summarise pass over the Datatracker HTML, not a line-by-line
reading of the full text. The sentences below are the ones that pass returned, with
their section numbers. Re-check each against the draft before a change lands on its
strength:

1. §1: "The SCITT ledger represents a linear and irrevocable history of statements made."
2. §3: a *Receipt* is "a cryptographic proof that a Signed Statement is included in the
   Verifiable Data Structure"; §4 adds that it "is universally verifiable without online
   access to the TS."
3. §3 and §6.3: the *Statement* "is considered opaque to Transparency Service, and MAY
   be encrypted"; the service "MAY validate the Signed Statement payload in order to
   enforce domain specific registration policies".
4. §4: "Transparency does not prevent dishonest or compromised Issuers, but it holds
   them accountable."
5. §9.2: "registering a Statement only proves it was produced by an Issuer", and
   "Issuers can make false Statements either intentionally or unintentionally".

## What this plant already does

- **The ledger sense's `verified` is a clean-audit word, and the code says what it is
  not.** In `src/mishe_tauftauf/discovery.py` (the state derivation near `:7140-7150`),
  `verified` is set when "the standing audit holds no violating row at all". The comment
  beside it says `settled` "is not `verified`: the ledger's history is not clean, and the
  word says so". So the sense already separates a clean history from an accounted one.
- **The audit checks bytes and accounting, not outcome content.** The re-hash in
  `_ledger_evidence_binding` (`discovery.py:6618`) and the classes it bands are about
  whether bound files changed and whether violations are accounted for. Nothing in that
  path reads what an outcome claims happened.
- **The same word means a different thing one file away.**
  `src/mishe_tauftauf/verdict_coverage.py:83-84` defines `EVIDENCE_BOUND_STATE_VERIFIED =
  "verified"` with the comment "the state a stratum's bound carries when it is quoted".
  That is a statistical bound (Clopper-Pearson, `discovery.py:7107-7118`), not a clean
  audit. Two readers of the published word, the pane and the coverage block, can take
  it for the same claim.
- **The wall book states the immutability rule, not the verdict's limit.**
  `docs/wall-coordination.md` (gitignored, site-local, so not a repository target) sets
  the write-once rule and says the sense measures it after the fact. A grep of that file
  for "truth", "correct" and "verified" finds no sentence that says what a clean
  verdict does not establish.

## What is new here

- **SCITT separates registration from truth, and this plant's `verified` needs the same
  separation stated.** A receipt proves a statement was registered and has not since
  changed. It does not prove the statement is true (§9.2). The plant's clean-audit
  `verified` proves that bound bytes are unchanged and every violation is accounted for.
  It does not prove the claimed outcome happened. The draft is explicit that transparency
  "holds [issuers] accountable" rather than preventing false statements (§4). Here the
  accountability is the tape and the digests, and a reader who sees `verified` should
  not hear it as a truth claim about content.
- **Two meanings of one word in one plant is the concrete hazard.** SCITT keeps the
  receipt (inclusion) and the statement (content) as different objects with different
  verification steps. The plant publishes `verified` for a clean audit and, one file
  away, for a quoted bound. The same word for two claims is where a reader would
  mistake one for the other, and the code gives no hint of it at the second site.
- **Limits of the transfer.** SCITT is a supply-chain model with an issuer, a transparency
  service and a verifiable data structure. This plant has no issuer signatures and no
  independent ledger service; the tape is its own. Receipts, inclusion proofs and COSE
  are not proposed. The mapping is one sentence wide: a clean-audit verdict proves
  integrity and accounting, not truth of content.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

**Change: state at the definition, next to `EVIDENCE_BOUND_STATE_VERIFIED`, which
`verified` means here and what it does not establish.** Proposed comment text, for the
owner to place and word (comment only, no code path changes):

> `verified` here means a stratum's bound is quoted. It is not the ledger sense's
> `verified`, which means the standing audit holds no violating row. Neither word says
> the outcome a bound or a clean audit describes is true.

**Acceptance, checkable on the committed bytes.**

- `grep -n "is not the ledger sense" src/mishe_tauftauf/verdict_coverage.py` finds the
  sentence beside `:84`.
- No value, state name, threshold or emitted word changes. The `verified` strings
  remain byte-identical, so the consistency checks see no difference.
- A rename of one meaning is raised as a follow-up for the sense's owner (senses) and
  is not proposed here.

**What this proposal does not do.** It does not change `discovery.py`, the state
derivation, the bands or the audit. It does not add content validation. It does not
rename either `verified`. Those choices belong to the owners of the sense and the
coverage block.

**Owner:** genome (the coverage block, with senses for the ledger sense's wording).
