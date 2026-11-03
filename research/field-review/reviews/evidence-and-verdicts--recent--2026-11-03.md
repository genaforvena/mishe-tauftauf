---
field: evidence-and-verdicts
angle: recent
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

# Evidence binding needs SLSA's split: untrusted external parameter vs resolved digest (builder = transitive closure)

## What we read

SLSA Specification, *Provenance — predicate `https://slsa.dev/provenance/v1`* (SLSA v1.1, Retired status on site, 2023-04 baseline; current site draft v1.1):

- https://slsa.dev/spec/v1.1/provenance — fetched 2026-11-03 as `/tmp/slsa-v1.1.html` 62138 B sha256 `d6f9509b9921880ef455475daeb0fe32903405cb702ebd471eb4d3460ee4eb44` (HTML via Jekyll; canonical markdown at `https://github.com/slsa-framework/slsa/blob/releases/v1.1/spec/provenance.md` is same predicate).

Read cue: summary + Model + Parsing rules + Schema (BuildDefinition / RunDetails / Builder / BuildMetadata) + Verification + Extension fields, with full text extraction around `builder.id`, `externalParameters`, `resolvedDependencies`, `predicateType`.

1. Predicate identity — §Purpose: "This document defines the following predicate type within the in-toto attestation framework: `predicateType: https://slsa.dev/provenance/v1` — Always use the above string; the URI resolves to the latest **minor** version; see parsing rules."
2. Model — "Provenance is an attestation that a particular build platform produced a set of software artifacts through execution of the `buildDefinition`." "The `builder.id` identifies this platform, representing the **transitive closure of all entities that are trusted to faithfully run the build and record the provenance**." Same model applies to platform-less builds; implementer SHOULD define security model for boundaries/actors/interfaces.
3. Parameters — defined by `buildType`-parameterized template: "`externalParameters`: the external interface to the build. In SLSA, these values are **untrusted; they MUST be included in the provenance and MUST be verified downstream**." "`internalParameters`: set internally by the platform. **trusted** because the platform is trusted; OPTIONAL, need not be verified downstream; MAY be included for reproducibility/debugging."
4. Dependencies — "All artifacts fetched during initialization or execution … are considered dependencies, including those referenced directly by parameters. The `resolvedDependencies` captures these dependencies, if known. For example, a build that takes a git repository URI as a parameter might record the specific git commit that the URI resolved to as a dependency." Guidance: digests of artifacts referenced by parameters SHOULD go in `resolvedDependencies` instead of inlining URI alone; `externalParameters` then holds the untrusted URI, `resolvedDependencies` holds the digest that was actually used.
5. Subjects — outputs are `subject[]` of `ResourceDescriptor` with `digest` (`sha256` string). External inputs whose digest is carried separately must still have digest in `resolvedDependencies`, verified against the external value.
6. Parsing rules (in-toto): "Consumers MUST ignore unrecognized fields unless otherwise noted. The predicateType URI includes the major version number and will always change whenever there is a backwards incompatible change. Minor version changes are always backwards compatible and **monotonic** — such changes do not update the predicateType. Unset, null, and empty field values MUST be interpreted equivalently." Extension fields SHOULD follow monotonic principle and MUST NOT affect verification.
7. Verification — `externalParameters` MUST be verified downstream against resolved values; `resolvedDependencies` digests, `subject` digests, and `builder.id` are the verified facts. Accuracy/completeness of provenance is bound to the builder the verifier trusts.

Not re-read line-by-line: full `.proto` field table, `buildType`-specific examples, `index of build types`, migration shims from 0.2 (`buildConfig` → `externalParameters`/`byproducts`). No claim relied on beyond the seven points above, each checked against the live text extracts (see sha256 above).

## What this plant already does

- **Wall-outcome binding is path + sha256 together.** `src/mishe_tauftauf/outcome_publication.py:392-407` decodes `payload.evidence.{path,sha256}` and `eligible_evidence_path()` checks the allowlist (`artifacts/` or `dms/`). The sense `src/mishe_tauftauf/discovery.py:_ledger_evidence_binding` (≈6800–7600) re-hashes the file at that path, compares `current != digest` → class B, splits `append` vs `rewrite`, builds bands/gate, and publishes `verified`/`drift`/`settled`/`unknown`. The tape stores `path` and `digest` as two keys of one object — no named split between the untrusted parameter and the resolved digest.
- **The same vocabulary already lives for CI, not for wall-outcomes.** `src/mishe_tauftauf/ci_watch.py` (≈SLSA vocabulary block cited in sweep) carries `SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"`, `SLSA_BUILD_LEVELS/PROVENANCE_STATES/ISOLATION_LEVELS`, `SLSA_BUILDER_ID_NOTE = "transitive closure …"`, validators `slsa_builder_id_is_valid / slsa_subject_is_valid / slsa_provenance_is_valid`, and `slsa_level_for` / `slsa_provenance_summary` for GitHub Artifact Attestations (`actions/attest@v4`, `binary-path` vs `container-oci`). That is CI artifact attestation; wall-outcome publication has no `predicateType`, no `builder.id`, no `externalParameters`/`resolvedDependencies` split, and no monotonic parsing note — the sweep for this field hits `checks/` and `outcome_publication.py` (C2PA/VC), not a wall-outcome SLSA shape.
- **Outcome publication recently gained media-provenance vocab, not build-provenance vocab.** `outcome_publication.py:98-142` carries C2PA 2.2 `DISCLOSURE_LEVELS / MANIFEST_STATES / TRUSTED_SIGNERS / PROVENANCE_ALG=sha256` and `98ff` provenance helpers (`provenance_data_hash`, `provenance_ingredient`, `provenance_validation_state`, etc.) plus Golaszewski critique vocabulary (`PROVENANCE_SECURITY_GOALS` …) and W3C VC 2.0 vocabulary (`VC_ROLES/DOCUMENT_TYPES/MEDIA_TYPES …`, `VC_TRUTH_IMPLIED_BY_VERIFIABLE = False` at :138). No SLSA `externalParameters`/`resolvedDependencies` mapping, no builder identity, no statement that the evidence path is untrusted until verified against the digest.
- **Discovery already re-hashes, but verifier obligation is not named.** The sense correctly treats `digest` as ground truth (re-hash vs stored digest), but neither the decoder's docstring nor the sense comment states the SLSA rule: the path is the untrusted external parameter and MUST be verified downstream against the resolved digest. A reader who sees `evidence.path` can take it as the artifact rather than as the parameter that the digest resolves.
- **Predecessor (2026-11-01, same cell).** That REVISIT read IETF `draft-ietf-scitt-architecture-22` (SCITT §1/§3/§4/§9.2) to separate receipt/inclusion from truth of statement, and landed a comment beside `EVIDENCE_BOUND_STATE_VERIFIED` in `verdict_coverage.py:84-87` ("verified here means only that a bound was computed … ledger's clean-audit verified is a separate word"). That transfer is intact on `main` (`provenance wording landed 2026-11-01`). It clarifies the **verdict word**; this REVISIT clarifies the **provenance fields** that the verdict rests on. Both invoke the same limit — verifiable ≠ true — but SCITT via receipt vs statement, SLSA via parameter vs resolved digest, VC via §1.1 literal already in `outcome_publication.py:138`.

## What is new here

- **The split is one field vs two fields, not a new crypto primitive.** SLSA does not ask for signatures, Rekor, or builder attestation in this transfer. The operative move is to name the two values the plant already stores as different kinds: `path` is `externalParameters` (untrusted; MUST be included and MUST be verified downstream), `sha256` is the `resolvedDependencies`/`subject` digest (the actually resolved content). The digest is not "the same value again for convenience"; it is the resolved dependency that the verifier checks. Storing them together without naming the split lets a reader treat the path as verified by publication.
- **builder.id is the missing who.** SLSA's `builder.id` is the transitive closure of the trusted platform that produced the artifact and its provenance. For a wall-outcome the builder is not GitHub Actions; it is the owned site's publication path — the CLI/build that wrote the tape entry (the mishe-tauftauf client, its version pin, and the site root). Recording/referencing `builder.id` distinguishes "who produced this leaf" from "what bytes that leaf binds"; today that `who` is only the tape's `role` string, not a builder closure.
- **Parsing rule is monotonic, and that is observable without a network.** SLSA's parsing rule — ignore unrecognized fields, monotonic minor-version extensions, `predicateType` carries major version only — is directly actionable for the plant's JSON readers (`discovery.py` sense, `evidence-binding-verdict-consistency.py`). The plant's decoders already tolerate unknown fields in practice, but the rule is not stated beside them, so a later extension that adds a field to `evidence` or `payload` has no contract for old readers.
- **Limits of the transfer (stated so the change stays bounded).**
  - SLSA is a supply-chain model with multi-tenant build platform, trusted control plane, Hermetic/reproducible tiers — the plant is single-tenant, file-system-local, with no control plane. L1/L2/L3 Isolation strength and hosted-build claims do not map.
  - SLSA Provenance v1.1 is Retired (banner: v1.2 is current). The predicate `https://slsa.dev/provenance/v1` is stable and the split it codifies survives into v1.2; the retirement does not invalidate the external-vs-resolved pattern. If the plant ever needs versioned verification, pin to `v1` predicate and handle `v1.2` via monotonic rule rather than switching predicate on minor change.
  - No signing (Sigstore/Fulcio/Rekor), no `in-toto Statement` envelope, no DSSE, no "attest" step is proposed. The plant already hashes with `provenance_data_hash` (`sha256`) per C2PA §13.1/18.5 — that alg choice is compatible, but signing is out of scope for this review.

## Application

target: src/mishe_tauftauf/outcome_publication.py

**Change — additive, pure, comment + helpers; no decoder/writer behavior change here:**

1. Beside the existing C2PA/VC blocks (currently `--- Provenance vocabulary (C2PA …) ---` at :98 and `--- Verifiable Credentials vocabulary (W3C …) ---` at :122), add a short `--- SLSA Provenance v1.1 (§Model / §externalParameters / §resolvedDependencies / Parsing rules) ---` block citing `https://slsa.dev/spec/v1.1/provenance` (predicate `https://slsa.dev/provenance/v1`, Retired-banner noted, monotonic rule) and stating the wall-outcome mapping:
   - `evidence.path` is the external parameter (untrusted; MUST be included and MUST be verified downstream — SLSA §BuildDefinition).
   - `evidence.sha256` is the resolved-dependency / subject digest (what the parameter resolved to at publication time — SLSA §resolvedDependencies / §subject).
   - The verifier's job is to compare the two (re-hash path, compare to digest); publication alone does not make the path verified.
   - `builder.id` for a wall-outcome is the publication platform's transitive closure (owned site root + `mishe-tauftauf` client pin), distinct from `role`; no new record field is added here, only the definition.
   - Parsing is monotonic: decoders MUST ignore unrecognized fields; `predicateType` carries only the major version.

2. Add pure vocabulary constants (mirroring how `ci_watch.py` exposes SLSA vocab but scoped to wall-outcomes, so the sweep for this field finds it beside the evidence decoder):
   ```python
   SLSA_WALLOUTCOME_PREDICATE_TYPE: Final[str] = "https://slsa.dev/provenance/v1"
   SLSA_WALLOUTCOME_BUILDER_ID_NOTE: Final[str] = "transitive closure of trusted build platform"
   ```

3. Add two pure helpers (no I/O, no crypto, same shape as `provenance_ingredient`/`slsa_subject_is_valid`):
   - `outcome_evidence_as_slsa_subject(evidence: dict) -> dict | None` — returns `{"uri": path, "digest": {"sha256": digest}}` when `eligible_evidence_path` + hex64 digest hold, else `None`. This is the `subject`/`resolvedDependencies` projection of the already-decoded evidence, not a new serialization.
   - `outcome_external_parameter_needs_verification(path: str, digest: str) -> bool` — returns `True` iff `eligible_evidence_path` holds and digest is hex64: the path is untrusted until re-hash verifies it against the digest (the SLSA MUST-verify obligation in pure form). Name captures the rule without re-implementing hashing.

   Both are one-line projections over `decode_evidence`/`eligible_evidence_path`; they do not re-derive bands, do not sign, and do not change the tape format.

**Acceptance, checkable on the committed bytes (no behavior change):**

- `grep -n "SLSA_WALLOUTCOME_PREDICATE_TYPE" src/mishe_tauftauf/outcome_publication.py` finds the constant with value `https://slsa.dev/provenance/v1`.
- `grep -n "outcome_evidence_as_slsa_subject" src/mishe_tauftauf/outcome_publication.py` finds the helper; `python3 -c "from mishe_tauftauf.outcome_publication import outcome_evidence_as_slsa_subject; print(outcome_evidence_as_slsa_subject({'path':'artifacts/demo.md','sha256':'e3b0'+'0'*60}))"` returns `{'uri': ..., 'digest': {'sha256': ...}}` and returns `None` for an ineligible path.
- `grep -n "externalParameters.*MUST be verified" src/mishe_tauftauf/outcome_publication.py` finds the SLSA §mapping comment beside the C2PA/VC blocks, with URL `https://slsa.dev/spec/v1.1/provenance` on a nearby line.
- No tape format, digest alg, allowlist, or `verified` word changes; `python3 -m py_compile src/mishe_tauftauf/outcome_publication.py` and `python3 tools/check_parsing.py` (if present) still pass.

**What this proposal does not do.** No envelope, no signing, no Rekor, no new storage field, no change to `discovery.py` sense or `verdict_coverage.py` wording (that was the SCITT predecessor), no SLSA L2/L3 isolation claim, no versioned predicate switch on minor bump (monotonic rule instead). The builder closure is defined in comment only; populating it from runtime bytes belongs to a follow-up owned by `genome`.

**Owner:** genome (outcome publication + checks/ binding; VC/C2PA vocab in same file — additive placement keeps vocabularies co-located, mirroring `ci_watch.py` SLSA vocab for CI).

**Prediction:** Naming `path` as untrusted external parameter and `sha256` as its resolved digest beside the decoder makes the verifier obligation (`re-hash and compare`) auditable at the definition site, without changing any published bytes.

**Falsifier:** If a second reader can correctly state "path is untrusted until digest-verified" and "digest is the resolved dependency" from the current `outcome_publication.py` without this comment/helpers — i.e., the code already separates them by type or docstring — then the gap is already closed and this proposal is a duplicate.

**Predecessor:** `evidence-and-verdicts--recent--2026-11-01` (SCITT `draft-ietf-scitt-architecture-22` → `verdict_coverage.py:84-87` comment). This REVISIT keeps that word-limit intact and adds the SLSA field-limit beside the evidence decoder.
