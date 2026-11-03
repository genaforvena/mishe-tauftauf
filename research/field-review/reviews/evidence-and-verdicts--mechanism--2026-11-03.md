---
field: evidence-and-verdicts
angle: mechanism
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

# Evidence binding needs RFC 3161's mechanism: MessageImprint + TSTInfo accuracy + nonce, not just a local clock

## What we read

IETF RFC 3161, *Internet X.509 Public Key Infrastructure Time-Stamp Protocol (TSP)*, C. Adams et al., Standards Track, August 2001 — read in full as the canonical text at:

- https://www.rfc-editor.org/rfc/rfc3161.txt

Fetched 2026-11-03 as `/tmp/rfc3161.txt` 54585 B sha256 `39fd17644ff2d654bc83814a78b1c5b5e7517f496741f34ead5064943eb98240` (also at `https://datatracker.ietf.org/doc/html/rfc3161`). The transferable object is the **mechanism by which a datum is bound to a time by a third party without trusting the requester's clock**, not a full PKI deployment.

Five passages carry the transfer (line numbers from the fetched text):

1. **§1 (p.1) proof-of-existence.** "A time-stamping service supports assertions of proof that a datum existed before a particular time. A TSA may be operated as a Trusted Third Party (TTP) service … Non-repudiation services require the ability to establish the existence of data before specified times." And: "The TSA's role is to time-stamp a datum to establish evidence indicating that a datum existed before a particular time. This can then be used, for example, to verify that a digital signature was applied … before the corresponding certificate was revoked." The bound is *existed-before*, not *true-at*.

2. **§2.4.1 MessageImprint (l.245–247).** `MessageImprint ::= SEQUENCE { hashAlgorithm AlgorithmIdentifier, hashedMessage OCTET STRING }` — "The hash algorithm indicated in the hashAlgorithm field SHOULD be a known hash algorithm." The request binds **alg + digest together**; a digest alone is not a MessageImprint, and an alg without a digest is not either. This is the exact predecessor to the plant's `path + sha256` conflation — RFC 3161 refuses to let them be separate optional fields.

3. **§2.4.2 TSTInfo (l.416–430, plus ASN.1 at l.1300ff).** `TSTInfo ::= SEQUENCE { version, policy, messageImprint, serialNumber, genTime GeneralizedTime, accuracy Accuracy OPTIONAL, ordering BOOLEAN DEFAULT FALSE, nonce INTEGER OPTIONAL, tsa GeneralName OPTIONAL, extensions }` where `Accuracy ::= SEQUENCE { seconds INTEGER OPTIONAL, millis [0] INTEGER OPTIONAL, micros [1] INTEGER OPTIONAL }` (l.530). `genTime` is the TSA's time, not the requester's; `accuracy` states how far that time may be from the true time; `ordering` says whether the TSA orders tokens monotonically; `nonce` binds the response to one request. The review that put `published_at` into a record (evidence-and-verdicts--mechanism--2026-10-22 via HLC) already noted that a coordinate without a stated ε is not a coordinate — accuracy is that ε, typed as seconds/millis/micros, not as a prose comment.

4. **§2.4.1 nonce + §3 timeliness (l.267–270, l.814–853).** "The nonce, if included, allows the client to verify the timeliness of the response when no local clock is available. The nonce is a large random number … a 64 bit integer." And §3: "A client application using only a nonce and no local clock SHOULD … any TimeStampResp that takes more than an acceptable period …" The nonce is not a sequence number — it is a freshness binding between one request and one response so a replayed token cannot be reused.

5. **§2.4.2 genTime + §4 TSA requirements (GeneralizedTime, accuracy MUST be supported only optionally, nonce MUST be mirrored).** "Among the optional fields, only the nonce field MUST be supported. … The nonce field MUST be present if it was present in the TimeStampReq. In such a case it MUST equal the value provided in the TimeStampReq structure." (l.440, l.571). The model is: the TSA asserts `genTime` + `accuracy`; the client checks that the returned `messageImprint` equals what it sent and that the `nonce` echoes. No local clock is required on the requester for the binding to hold.

Limits kept from the source (so the transfer stays bounded):

- The guarantee is relative to a TSA the verifier trusts. RFC 3161 §4 requires the TSA to have a trustworthy time source, to include the TSA name, and to sign the token (SignedData with `id-ct-TSTInfo` at l.405/1297). Without a TSA key and a trust anchor, a local `genTime` is just a number. This review does not import X.509, CMS SignedData, ESSCertID, or any network fetch.
- `accuracy` is a *claim* by the TSA, not a measurement by the client. A token that says `seconds=1` does not prove it was 1 s accurate.
- `ordering=true` requires the TSA to order tokens; `ordering=false` makes two tokens with close `genTime` unordered. The paper's own ordering flag is the mechanism's admission that time alone is not order.
- The `policy` OID identifies which TSA policy produced the token; different policies have different accuracy/ordering guarantees. A bare timestamp without a policy identifier is underspecified.

Not re-read line-by-line: full ASN.1 module beyond the two SEQUENCEs above, CMS SignedData wrapping details, ESSCertID / `signingCertificate` attribute, and the annex example about signature-before-revocation. No claim relied there.

## What this plant already does

- **Provenance vocabulary is alg + disclosure + revocation + timestamp, but timestamp is a predicate over codes, not a time.** `src/mishe_tauftauf/outcome_publication.py:98-121` carries C2PA 2.2 `DISCLOSURE_LEVELS / MANIFEST_STATES / PROVENANCE_ALG="sha256" / TRUSTED_SIGNERS` plus Golaszewski vocabulary `PROVENANCE_SECURITY_GOALS / PROVENANCE_REVOCATION_STATES / PROVENANCE_FILE_INTEGRITY_LEVELS` and helpers `provenance_revocation_status / provenance_timestamp_binding_holds / provenance_validators_agree`. `provenance_timestamp_binding_holds` (l.602-618) checks whether a status code `timeStamp.validated` is present — it checks *whether a binding was claimed*, not *when* or *how accurately*. The comment at l.112 even notes `UNACTIVATED: no production caller` and that the predicates test C2PA status codes.

- **Verbatim "no RFC 3161" limits are already documented beside that vocabulary.** `publication-and-attribution--critique--2026-10-29` and `publication-and-attribution--recent--2026-10-29` explicitly list "No X.509 signer, no COSE claimSignature, **no RFC 3161 timestamp**, no JUMBF …" as keeps. `outcome_publication.py:112-113` keeps the same boundary: no signer, no COSE, no timestamp, no trust-list fetch. Those reviews correctly refused to claim a timestamp mechanism without reading one. This REVISIT now reads it and extracts only its **data-model**, not its PKI.

- **The plant does have time, but it is local and untracked as an accuracy.** `feed._encode_entry` (`src/mishe_tauftauf/feed.py:100-107`) writes `v1 <sequence> <instant> <source> ::` with `feed.utc_now()` (feed.py:96-97); `outcome_publication.py:400-407` decodes that `GeneralizedTime`-shaped stamp (`decode_timestamp` via `fromisoformat(... replace('Z', '+00:00'))`) but never records *how far* that instant may be from true time. The two prior mechanism reviews measured the gap from other sides: `evidence-and-verdicts--mechanism--2026-10-22` (HLC) gave the store no coordinate at all and proposed `published_at = feed.utc_now()` in the record (not on `main` as of 2026-11-01 per the successor review); `evidence-and-verdicts--mechanism--2026-11-01` (RFC 6962 CT) gave the store no order and proposed a running hash head `h_i = sha256(h_{i-1} || digest_i)` published in the reading (also not on `main`). Neither typed **alg + digest as one imprint**, nor **genTime + accuracy as one claim**, nor **nonce as freshness** — the three things RFC 3161 contributes orthogonally.

- **Discovery re-hashes, but verifier obligation is named via SLSA, not via TSP.** `discovery.py:_ledger_evidence_binding` re-hashes the file at `evidence.path` and compares to `evidence.sha256` (the SLSA split landed 2026-11-03 as a comment/helpers beside the decoder: `externalParameters` = path untrusted, `resolvedDependencies` = digest). That split says *what* to verify; RFC 3161's `MessageImprint` says *how to name* what to verify (alg + digest as one unit, not two coincidental keys), and `TSTInfo` says *when* that binding was asserted and *how tightly*.

- **The evidence record itself carries no time vocabulary.** `records.prepare` (`src/mishe_tauftauf/records.py:108-153`) writes `{"version","kind","payload"}` with sorted keys, file named by sha256, mode `0400`; `VerifiedStore` (records.py:202-296) caches verification time, not an imprint or a TSA time. A wall-outcome record's payload (`wall.py:196-201`) is `{role, kind, text, evidence{path, sha256}}` plus `supersedes` — no `messageImprint`, no `accuracy`, no `nonce`, no `policy`.

## What is new here

RFC 3161 contributes one idea the plant does not embody: **a bound evidence claim needs three typed pieces that the plant currently stores as one or as prose — the imprint (alg + digest as one value), the asserted time plus its stated accuracy (genTime + seconds/millis/micros), and the freshness that ties one response to one request (nonce) — and conflating any of them makes a replayed or backdated binding look the same as a fresh one.**

Concretely:

- `sha256 = e3b0…` is not a MessageImprint. `MessageImprint = (hashAlgorithm=sha256, hashedMessage=e3b0…)` is. Storing `evidence.sha256` without naming its alg as part of the same imprint lets a future alg-agility change break verification silently — the verifier must know *which* alg the digest is for, as part of the same value, which is why `PROVENANCE_ALG` is already a constant but is not paired with the digest as one imprint type.
- `2026-11-03T00:33:00Z` is not a timestamp. `TSTInfo.genTime + Accuracy(seconds=1, millis=500)` is — the time plus the ε the asserter claims. The plant's `feed.utc_now()` instant is a local `genTime` without a stated `accuracy`, and its `decode_timestamp` parses GeneralizedTime without ever naming an accuracy. HLC's proposed ε (seconds-scale) and CT's ordering head are not the same as a per-token accuracy.
- A second publication that re-binds the same `evidence.path + sha256` with the same instant but a different purpose is indistinguishable from a replay without a `nonce` that ties the token to one request. The plant's `feed` sequence is per-file and restarts at 1, so it is not a cross-generation nonce.

The transfer maps onto the plant's own vocabulary idiom: like the C2PA `PROVENANCE_ALG / MANIFEST_STATES` block, the VC `VC_ROLES / VC_DOCUMENT_TYPES` block, the PROV-DM `PROV_TYPES / PROV_RELATIONS` block, and the SLSA `SLSA_WALLOUTCOME_PREDICATE_TYPE` block, TSP vocabulary is pure constants plus pure validators, additive beside them, changing no emitted bytes until an owner decides to populate them.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Add a TSP vocabulary block beside the C2PA (`DISCLOSURE_LEVELS / MANIFEST_STATES / PROVENANCE_ALG`), Golaszewski (`PROVENANCE_SECURITY_GOALS`), VC (`VC_ROLES`), SLSA (`SLSA_WALLOUTCOME_PREDICATE_TYPE`), and PROV-DM (`PROV_TYPES`) blocks, in `src/mishe_tauftauf/outcome_publication.py`. Additive only, no emitted-value or path change, no new file write, no network, no X.509, no CMS. Concretely (names illustrative, `Final`, owner chooses exact identifiers):

1. **Constants (§2.4.1 / §2.4.2, with section citations in docstrings):**
   - `TSP_HASH_ALGS: Final[tuple[str, ...]] = ("sha256", "sha384", "sha512")` — the `hashAlgorithm` values RFC 3161 allows via `MessageImprint.hashAlgorithm`; the plant's `PROVENANCE_ALG="sha256"` is the one member currently used, but the imprint type must name the alg explicitly (cf. C2PA Sec.13.1 p.58 alg set).
   - `TSP_TSTINFO_FIELDS: Final[tuple[str, ...]] = ("version", "policy", "messageImprint", "serialNumber", "genTime", "accuracy", "ordering", "nonce", "tsa")` — the `TSTInfo` SEQUENCE members (l.416–430), of which the evidence binding uses at least `messageImprint / genTime / accuracy / nonce / policy / serialNumber`.
   - `TSP_ACCURACY_UNITS: Final[tuple[str, ...]] = ("seconds", "millis", "micros")` — the `Accuracy` SEQUENCE members (l.530), typed as the plant's `ε` vocabulary so a timestamp claim carries its stated looseness.
   - `TSP_MESSAGE_IMPRINT_VERSION: Final[int] = 1` — `TSTInfo.version` per l.416 (`version INTEGER { v1(1) }`).
   - Keep the C2PA/VC/PROV/SLSA blocks unchanged; the new block cites `IETF RFC 3161 §2.4.1 MessageImprint / §2.4.2 TSTInfo / Accuracy / §3 nonce` in its module comment, like the existing `Ch.16.3 p.111` and `W3C REC-prov-dm-20130430 §5.1.1` citations. Note the `UNACTIVATED` boundary beside it: no TSA, no SignedData, no trust anchor — the vocabulary is the contribution.

2. **Pure validators, same shape as `provenance_disclosure_is_valid / prov_type_is_valid / slsa_provenance_is_valid`:**
   - `tsp_hash_alg_is_valid(value) -> bool` — `value in TSP_HASH_ALGS`.
   - `tsp_accuracy_is_valid(value) -> bool` — `value is dict with optional int keys "seconds"/"millis"/"micros"` each `0 <= v < 1000` where present (per l.530: millis/micros are sub-second), and at least one key present when called as an accuracy.
   - `tsp_message_imprint_is_valid(value) -> bool` — `value is dict with "hashAlgorithm" in TSP_HASH_ALGS and "hashedMessage" is hex64/96/128 matching that alg's digest length` (sha256→64, sha384→96, sha512→128) — mirrors `provenance_ingredient`'s alg+digest pairing but as one imprint predicate.
   - `tsp_tstinfo_holds(*, message_imprint: dict, gen_time: str, accuracy: dict | None, nonce: int | None) -> bool` — returns `tsp_message_imprint_is_valid(message_imprint) and decode_timestamp(gen_time) is not error and (accuracy is None or tsp_accuracy_is_valid(accuracy))` — same predicate shape as `provenance_timestamp_binding_holds`: a check over codes/identifiers, not a network fetch. Whether two digests were actually timestamped by a TSA remains an owner decision; the vocabulary only gives the words.

3. **Acceptance, checkable on the committed bytes (no fixture, no network):**
   - `python -c "import mishe_tauftauf.outcome_publication as p; assert p.TSP_HASH_ALGS==('sha256','sha384','sha512'); assert 'genTime' in p.TSP_TSTINFO_FIELDS; assert 'seconds' in p.TSP_ACCURACY_UNITS; assert p.tsp_hash_alg_is_valid('sha256') is True; assert p.tsp_hash_alg_is_valid('md5') is False; assert p.tsp_message_imprint_is_valid({'hashAlgorithm':'sha256','hashedMessage':'e3b0'+'0'*60}) is True; assert p.tsp_accuracy_is_valid({'seconds':1,'millis':500}) is True"`
   - `rg -n "TSP_HASH_ALGS|TSP_TSTINFO_FIELDS|TSP_ACCURACY_UNITS|tsp_hash_alg_is_valid|tsp_message_imprint_is_valid" src/mishe_tauftauf/outcome_publication.py` shows only the new block.
   - `rg -rn "RFC.?3161|TSP|MessageImprint|TSTInfo" src/` shows only `outcome_publication.py` boundary comments until a publication review lands — same invariant the C2PA/VC/PROV blocks preserved.
   - Compatibility: `OutcomePublication / decode_publication_body / reference_* / provenance_* / prov_* / slsa_*` contracts unchanged, `pytest tests/test_outcome_publication.py -q` and existing `tests/test_publication_*.py` still pass; `python3 research/field-review/field_review.py check` stays PASS.

4. **Predecessor-and-limits line (so the review is not read as a defect claim):**
   - Predecessors: `evidence-and-verdicts--mechanism--2026-10-22` (HLC → `published_at` coordinate in record, store's own time) and `evidence-and-verdicts--mechanism--2026-11-01` (RFC 6962 CT → ordered leaf list with running head `h_i`, store's order) remain true; this transfer covers **binding a datum to a time with a stated accuracy and freshness** (proof-of-existence before T, not causality and not append-only prefix). Three orthogonal properties: when (this REVISIT) vs. in-what-order (CT) vs. at-what-instant (HLC).
   - Limits: no TSA, no X.509 signer, no CMS SignedData, no `id-ct-TSTInfo` OID, no `signingCertificate` ESSCertID, no policy OID fetch, no OCSP, no JUMBF embedding, no soft binding, no change to `Feed.append / seed yield / VerifiedStore / discovery.py` verdict words — exactly the source's §1 extensibility boundary: the *data model* (`MessageImprint / TSTInfo / Accuracy / nonce`) is the transfer, not the PKI.

**Prediction / falsifier:** if the block lands, a future `wall-outcome` or `records.prepare` reviewer can label `evidence.sha256` as `MessageImprint(hashAlgorithm=sha256, hashedMessage=digest)` and a `genTime + Accuracy` pair as the asserted time with its ε, and a `nonce` as the freshness that distinguishes a re-publication from a replay, without adding a parser site; if it does not land, that labeling stays reconstruction from `PROVENANCE_ALG` + bare digest + unqualified instant.

**Owner:** genome (outcome publication dialect + records; TSP vocabulary co-located with C2PA/VC/PROV/SLSA in same file keeps vocabularies together, mirroring `ci_watch.py` SLSA vocab for CI).

## Limits

The source is a PKI protocol, not a local timestamp format; adopted constants do not claim conformance, TSA operation, signing, or time synchronization. The distinction between a local instant (`feed.utc_now()`) and a TSA-asserted `genTime + Accuracy` with a `nonce`-bound `MessageImprint` is the only theorem transferred — §1's *existed-before* with a stated looseness, not a guarantee of accuracy or trust.
