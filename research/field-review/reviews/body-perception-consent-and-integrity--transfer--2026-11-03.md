---
field: body-perception-consent-and-integrity
angle: transfer
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — transfer (2026-11-03)

## What we read

- W3C, *Verifiable Credentials Data Model v2.0*, W3C Recommendation 2025-05-15 (REC). Canonical URL <https://www.w3.org/TR/vc-data-model-2.0/> (based on 2022-03-03 REC, editors Sporny/Thibodeau et al.). Fetched 2026-11-03 as `/tmp/vc-data-model.html` 1,023,165 B sha256 `a9196a3d0b6601356c4e127bad24f8c7f2c17f6ed22e41b35755a910104156f8` via `curl -sL https://www.w3.org/TR/vc-data-model-2.0/`. Read Abstract + §1 Introduction/§1.1 What is a VC/§1.2 Ecosystem Overview, §3 Core Data Model (Claims/Credentials/Presentations), §4.7 Issuer, §4.8 Credential Subject, §4.9 Validity Period, §4.10 Status, §4.11 Data Schemas, §4.12 Securing Mechanisms, §4.13 Verifiable Presentations, §5.1 Trust Model, §7.1 Verification algorithm and §8 Privacy Considerations in full (ReSpec HTML with examples).

The source's own claims for this transfer, in its words:

- Abstract/§1.1–1.2: "A verifiable credential is a specific way to express a set of claims made by an issuer … secured from tampering … three-party ecosystem … issuers, holders, and verifiers" plus a verifiable data registry "mediating the creation and verification of identifiers, verification material, and other relevant data, such as … revocation registries" (§2 Terminology / §1.2).
- §4.7 Issuer: "A verifiable credential MUST have an issuer property. The value of the issuer property MUST be either a URL or an object containing an id property whose value is a URL; … It is RECOMMENDED that the URL be one which, if dereferenced, results in a controlled identifier document, as defined in Controlled Identifiers v1.0, about the issuer."
- §4.8 Credential Subject: "each property-value pair in the credentialSubject object expresses a particular property of the credential subject" — the subject's claims are the credential's payload.
- §4.10 Status + §4.13 Presentations + §4.12 Securing Mechanisms: credentialStatus (e.g. `BitstringStatusListEntry`) for suspension/revocation, validity period (`validFrom`/`validUntil`), and a securing `proof` (ecdsa, bbs, sd-jwt etc.) that makes tampering detectable; verification (§7.1) checks proof, issuer identifier resolution, status, and schema before a verifier accepts.
- §8.4/§8.11/§8.13–8.14: correlation, aggregation and patterns-of-use risks when the same identifier is reused — directly relevant to linking `light_class` windows.

What this transfer does NOT borrow: JSON-LD contexts (§6.1), specific proof suites (§9.1), or the full Trust Model (§5.1) — only the data-model shape (issuer/credentialSubject/status/proof) as a cross-domain vocabulary for consent receipts.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:273–314` keeps `PERSONAL_CONSENT_RECEIPTS` as a static table: each `BODY_INFORMATION_TYPES` member maps to `{principle, origin, date, recipients, scope_note}`. Comment at lines 243–260 frames these as Kaye et al. EJHG 2015 "dynamic consent" sticky policies ("machine-readable disclosure policies … that travel with … data," Box 2–3 "one record of all transactions," "preferences travel with samples") — revisable per type without replacing the 2026-10-15 broad mandate. Per-type `date: "2026-10-15"` and `origin: ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md"` are typed, but `personal_consent_allows` (lines 519–565) ignores dates and no revocation field exists (prior misread review, commit 25695b65 docstring boundary note).
- Consent-model taxonomy (lines 261–272, Gilbert npj Digital Medicine 2025 Table 1): typed as dynamic, not broad/meta/SHC — acknowledges fatigue/tech-requirements challenge (Refs 16–19) without implementing withdrawal or fatigue handling.
- The 2026-11-01 transfer discard for this same cell (GDPR Art. 7 demonstration burden + withdrawal parity, plus Kantara 410) already framed receipts as needing demonstrability and revocation, and was discarded because wiring `date`/`revocation` into the predicate is a senses/operator consent-model decision, not a docstring duplicate. That discard proposed no VC shape; it shared the gap but not the mechanism.
- Belmont vocabulary `BELMONT_PER_TYPE_ASSESSMENT` (lines 344–417) and CI tables `_APPROPRIATENESS`/`_CI_FLOWS` live beside the receipts but are not credentials — they are module constants with no issuer identifier, no proof, no status check, and no verifier role. `rg -n "credential|VC|issuer.*URL|proof|BitstringStatus" src/mishe_tauftauf/body_perception.py` hits only research/ before this review.

## What is new here

Verifiable Credentials give a standard shape for exactly the gap the prior transfer and the Kaye sticky-policy note both name: a demonstrable, tamper-evident, revocable receipt that travels with the data.

Transfer mapping onto `PERSONAL_CONSENT_RECEIPTS`:

- **Issuer** (§4.7) → the consent authority whose URL identifies it unambiguously and dereferences to a controlled identifier document. The plant's `origin` is a file path, not a URL, and does not identify the issuer (operator? site? human?) — Art. 7(1) demonstrability and the Kaye "package travels" both assume an issuer a verifier can check. VC's MUST/RFC 2119 makes explicit what is currently a comment.
- **credentialSubject** (§4.8) → the per-type claim: `id` = subject (the deployment/bearer), `informationType` = one of `BODY_INFORMATION_TYPES`, plus `transmissionPrinciple`, `recipients` scope, and `scope_note`. Today these are bare dict values; in VC they are explicit claims about a subject.
- **Validity + Status** (§4.9/§4.10) → `validFrom`/`validUntil` (replaces bare `date`) and `credentialStatus` (e.g. `BitstringStatusListEntry`) so withdrawal as easy as giving (Art. 7(3)) becomes a status-list update, not a source edit. Kaye's "alter consent preferences in real time" (p.143) and Box 3 "modify over time" need a status mechanism; the current table has none.
- **Securing mechanism / proof** (§4.12) → tamper evidence over the four fields above. Today receipts are constants in source; a VC's proof binds issuer + subject + claims + validity so a verifier (§7.1) can reject a copied or edited receipt without trusting the carrier.
- **Holder / Verifier / Registry** (§1.2/§5.1) → holder = the reading that carries the sticky policy; verifier = any recipient in `_CI_FLOWS` or a `body_perception` caller that checks `personal_consent_allows`; registry = where revocation/status is published. The three-party separation clarifies that checking the proof and status belongs to the verifier, not to the producer that wrote the row.

Why this is distinct from the 2026-11-01 transfer: GDPR named the legal obligation; Kaye named per-type revisability. Neither named a concrete credential shape with URL-identified issuer, status-list revocation, and proof. Pufferfish (2026-11-03 critique REVISIT, Kifer-Machanavajjhala TODS 2014 §1/§3/§6) pins that `k=2` does not bind correlated rows — VC does not solve that either; the correlation/aggregation privacy risk (§8.4/§8.13) is complementary, not a substitute for declaring Pufferfish S/Spairs/D or a DP `ε/δ`.

Limits the source states and kept here: VC is not a consent protocol; it does not decide who the issuer must be, which proof suite to use, or where the registry lives — those are deployment decisions per §5.1 Trust Model. Spec says extensibility (§5.2) but also warns (§8.11) that reusing identifiers correlates presentations; a VC-shaped receipt must not introduce a linkable long-lived identifier without a correlation analysis. No JSON-LD, no key management, no network fetch is implied.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` adds a docstring-only boundary paragraph beside `PERSONAL_CONSENT_RECEIPTS` / `CONSENT_RECEIPT_VERSION` (around lines 243–314), complementary to the existing Kaye EJHG 2015 sticky-policy note, the 2026-11-01 misread static-grant boundary, and the 2026-11-03 Pufferfish correlated-record note at `DENSE_TAIL_BYTES`. Vocabulary only, no behaviour, no I/O, revertible by `git revert`:

- Cite W3C Verifiable Credentials Data Model v2.0 REC 2025-05-15 §1.1/§1.2/§4.7/§4.8/§4.10/§4.12 via <https://www.w3.org/TR/vc-data-model-2.0/>: a verifiable credential expresses issuer claims about a subject secured by a proof, in an issuer–holder–verifier ecosystem with a verifiable data registry for identifiers and revocation.
- Map the plant's receipt: issuer (§4.7 MUST URL, RECOMMENDED dereferenceable controlled identifier) vs current `origin` file path; credentialSubject (§4.8 property-value claim about the subject per `BODY_INFORMATION_TYPES` type) vs current bare dict; validity + credentialStatus (§4.9/§4.10, e.g. `BitstringStatusListEntry`) vs no expiry/revocation; proof/securing mechanism (§4.12, §7.1 verification) vs tamper-invisible constant; holder = reading carrying the sticky policy, verifier = recipient caller, registry = status publisher (§1.2/§5.1).
- State that `PERSONAL_CONSENT_RECEIPTS` is not a VC: no issuer URL/identifier document, no proof, no status list, no verifier check; demonstration therefore rests on the static source note, not on a verifiable presentation — re-stating the 2026-11-01 GDPR/Kaye gap in VC vocabulary without wiring withdrawal.
- Note the §8.11/§8.4 identifier-correlation caution and that making receipts verifiable must not add a linkable long-lived identifier without analysis; correlation across `DENSE_TAIL_BYTES` windows (Pufferfish) and aggregation (§8.13) remain separate risks not solved by VC shape alone. Keep Kaye revisability and Pufferfish correlation notes as complements.

**Acceptance (deterministic):**

- `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0.
- `python3 research/field-review/field_review.py check` passes for this review.
- `rg -n "Verifiable Credential|credentialSubject|credentialStatus|W3C.*vc-data-model" src/mishe_tauftauf/body_perception.py` hits the new note and nowhere else in `src/` outside research/.
- `rg -n "personal_consent_allows|PERSONAL_CONSENT_RECEIPTS" src tests` still shows no production caller invoking the predicate (docstring-only; wiring issuer verification/status is a separate, senses-owned decision).

**Ownership.** `senses` owns `body_perception.py`. Whether to type an issuer URL, add a proof type, publish a status list, or implement §7.1 verification is senses/operator decision with its own review; this review does not ask for it.

**One thing this review does not ask for.** It does not add JSON-LD, keys, signatures, network fetches, or a registry. It does not make withdrawal machine-enforceable — the prior transfer discard and the misread docstring already state why that needs a consent-model decision. VC is the vocabulary for that decision, not the implementation.
