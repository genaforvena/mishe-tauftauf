---
field: evidence-and-verdicts
angle: transfer
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

# Evidence binding: a file with the same bytes is typed as the same evidence without saying so

## What we read

W3C, *PROV-DM: The PROV Data Model*, W3C Recommendation 30 April 2013, eds. L. Moreau & P. Missier — read as the REC HTML in full (271572 B, sha256 5c925c21bc96c285b25017c3b90d82cf74251d07713b71fcb81ca60f873c9e88, retained as /tmp/prov-dm-2013.html) at:

- https://www.w3.org/TR/2013/REC-prov-dm-20130430/

The transferable object is not RDF, not PROV-O, not PROV-N serialisation. It is the data-model's typed relations between evidence and the work that produced it. Five passages carry the transfer:

1. **§1, six components** (p.3 of the REC text): PROV-DM is organised in "(1) entities and activities, and the time at which they were created, used, or ended; (2) derivations of entities from entities; (3) agents bearing responsibility …; (4) a notion of bundle, a mechanism to support provenance of provenance; (5) properties to link entities that refer to the same thing; and, (6) collections". The plant's binding conflates all six into one field — `evidence.path + evidence.sha256` — and stores no typed distinction between the thing, the act that made it, and the role responsible for it.

2. **§5.1.1 Entity (dfn-entity, glossary-ref p.5):** "An entity is a physical, digital, conceptual, or other kind of thing with some fixed aspects; entities may be real or imaginary." `entity(id, [attr…])` in PROV-N. The REC's own example (§2.1.1, §4 illustration) treats the same file content under two different entity identifiers when its fixed aspects differ.

3. **§5.1.2 Activity (dfn-activity, glossary-ref p.5–6 + Example 2):** "An activity is something that occurs over a period of time and acts upon or with entities; it may include consuming, processing, transforming, modifying, relocating, using, or generating entities." `activity(id, st, et, [attr…])`. Examples: "publishing of a document on the Web, … assembling a data set … performing a statistical analysis". Wall-outcome publication and remedy-file creation are activities in this sense.

4. **§5.3.1 Generation / §5.3.2 Usage / §5.3.4 Communication + §6.1 Derivation + §5.3.5 Association:** the relations `wasGeneratedBy(e, a)`, `used(a, e)`, `wasDerivedFrom(e2, e1)`, `wasAttributedTo(e, ag)`, `wasAssociatedWith(a, ag)` and `wasInformedBy(a2, a1)`. Figure 5's UML and Table 2 (§2.1, p.4) tabulate which subject/object each relation is defined for. `wasDerivedFrom` has subtypes `Revision`, `Quotation`, `PrimarySource` (§5.2). A remedy file `foo-v2` derived from lost `foo` is a `wasDerivedFrom` with `prov:type="revision"` — distinct from `wasGeneratedBy` (the activity that created `foo-v2`) and `wasAttributedTo` (the role that bears responsibility).

5. **§5.4 Bundle + §2.5.5 "provenance of provenance":** a Bundle is an Entity that is also a container for provenance statements, so statements themselves can have provenance. The plant's `records/` store and `discovery/scan-*.json` publication are bundles in this informal sense — but the REC gives the typing to say so.

Limits kept from the source: PROV-DM is domain-agnostic and extensible (§1); this review does not import PROV-O, PROV-N, PROV-JSON, bundle serialisation, time interval validation, or any graph store. What is transferred is the type vocabulary and the separation it enforces — one predicate for the bytes, another for the act, a third for the responsibility.

## What this plant already does

- **Publication binds bytes, not relations.** `src/mishe_tauftauf/outcome_publication.py:98-142` carries C2PA 2.2 hard-binding vocabulary (`PROVENANCE_ALG="sha256"`, `DISCLOSURE_LEVELS`, `MANIFEST_STATES`) with helpers `provenance_data_hash` (hard binding §18.5), `provenance_ingredient` (§18.15), `provenance_manifest_summary`, `provenance_validation_state` plus Golaszewski critique vocabulary (`PROVENANCE_SECURITY_GOALS`, `PROVENANCE_REVOCATION_STATES`, `PROVENANCE_FILE_INTEGRITY_LEVELS`) and `provenance_revocation_status / provenance_timestamp_binding_holds` etc. W3C VC 2.0 vocabulary (`VC_ROLES`, `VC_DOCUMENT_TYPES`, `VC_MEDIA_TYPES`, `VC_STATUS_TYPES`, `VC_SECURING_MECHANISMS`, `VC_TRUTH_IMPLIED_BY_VERIFIABLE`) sits beside it (`:122-142`). All of it types *binding and disclosure of bytes* or *verifiable-credential standing* — none of it types the Entity/Activity/Agent distinction or the derivation between a lost artifact and its remedy.

- **The remedial relation is decoded but not typed as derivation.** `outcome_publication.py:144-188` decodes the clause's remedy-naming convention via `remedy_stem` / `remedy_successor` (`EVIDENCE_REMEDY_SUFFIX = r'^(?P<stem>.+?)-(?P<sfx>v\d+|final|final-v\d+)$'`) and `discovery.py:6421-6615` uses `remedy_stem` to decide showing ("whether the successor names the lost artifact's corrected form"). The comment at `discovery.py:6394-6408` already distinguishes a corrected artifact (`-v2`, `-final`) from a status note (`-ci`) that "carries none of the lost artifact's content, so it cannot be evidence that the work was re-evidenced". That distinction *is* PROV-DM's `wasDerivedFrom` (Revision) vs. `wasGeneratedBy` without derivation — but the code has no vocabulary to say it, so a reader must reconstruct it from the regex and the prose.

- **Discovery binds an outcome to evidence but does not attribute it.** `discovery.py:7188-7200` builds `verdict_basis` / `verdict_coverage` records that carry `evidence.path` and `evidence.sha256` plus bands (`remediation_bands.py` vocabulary). The wall-outcome record carries `role` (e.g. `witness`, `genome`) as a string field on the event, not as a PROV `wasAttributedTo` / `wasAssociatedWith` relation. Whether an artifact was `used` by the discovery scan, `generated` by `wall-outcome`, `derived` as a remedy, or `attributed` to a role are four different relations collapsed into "a row has an evidence file".

- **The sample count already shows the gap is not decorative.** Re-read of the retained tape on 2026-11-03: `records/` holds entities whose `evidence.path` is the same file stem with different fixed aspects (original vs `-v2` remedy), and the classifier's `_rebind_links` must decide `wasDerivedFrom` vs. mere co-location. Two artefacts with identical `sha256` but different activities (`createFile` vs `remedy`) and different agents are indistinguishable by the bytes alone — which is exactly PROV-DM §5.1.1's point that fixed aspects, not byte equality, individuate entities.

- **No prior transfer in this field typed relations.** `evidence-and-verdicts--transfer--2026-10-22` (RFC 9162 CT, `checks/evidence-binding-verdict-consistency.py`) typed the *history* of published views — whether the later view is a superset of the earlier (append-only, consistency proof). `evidence-and-verdicts--transfer--2026-11-01` (Jøsang arXiv:cs/0606029, `discovery.py`) typed the *amount* of evidence behind one verdict — `rows >= 1 ⇒ verified` discarding `u` / bound width. Neither typed the *structure* of one evidence link — which thing derived from which, which act generated which, who is responsible. This transfer is orthogonal to both: history (across readings) vs. quantity (across rows) vs. relation (within one row).

## What is new here

PROV-DM contributes one idea the plant does not embody: **one evidence file participates in at least four typed relations, and conflating them makes byte-identical artefacts interchangeable when they are not.** An entity (the evidence file at a path, at a digest) `wasGeneratedBy` an activity (the `wall-outcome` publication, or a `create`/`remedy` file write), `wasAttributedTo` an agent (the role that claims it), `wasDerivedFrom` a prior entity when it is a remedy (Revision, §5.2.1), and `wasAssociatedWith` ties the activity to that agent; a collection (the tape or the scan corpus) `hadMember` the entity. The plant currently stores one row with `path + sha256 + role` and relies on naming convention and byte equality to imply the rest.

Concretely: an artifact `wall-123.json` and its successor `wall-123-v2.json` with the same prose but a new evidence file are not distinguished by `sha256` — they are distinguished by `wasDerivedFrom(v2, v1) [prov:type="revision"]` and `wasGeneratedBy(v2, remedyActivity)` vs. `wasGeneratedBy(v1, originalActivity)`. A `-ci` citation that satisfies `EVIDENCE_REMEDY_SUFFIX` without carrying the work is `wasGeneratedBy(citation, publishActivity)` without any `wasDerivedFrom` to the lost entity — which is why the comment at `discovery.py:6406-6408` already refuses it as evidence, but without the type.

The transfer maps onto the plant's own vocabulary idiom: like the C2PA `PROVENANCE_ALG / MANIFEST_STATES` block and the VC `VC_ROLES / VC_DOCUMENT_TYPES` block, PROV-DM vocabulary is pure constants plus pure validators, additive beside them, changing no emitted bytes until an owner decides.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Add a PROV-DM vocabulary block beside `DISCLOSURE_LEVELS / MANIFEST_STATES / PROVENANCE_ALG / TRUSTED_SIGNERS` (C2PA) and `VC_ROLES / VC_DOCUMENT_TYPES` (VC), in `src/mishe_tauftauf/outcome_publication.py`. Additive only, no emitted-value or path change, no new file write. Concretely (names illustrative, `Final`, owner chooses exact identifiers):

1. **Constants (§5.1–§5.4, Table 2), with section citations in docstrings:**
   - `PROV_TYPES: Final[tuple[str, ...]] = ("Entity", "Activity", "Agent")` — PROV-DM §5.1.1 / §5.1.2 / §5.2 "an entity is …", "an activity is …", "an agent is …".
   - `PROV_RELATIONS: Final[tuple[str, ...]] = ("wasGeneratedBy", "wasDerivedFrom", "wasAttributedTo", "wasAssociatedWith", "used", "wasInformedBy", "actedOnBehalfOf", "hadMember", "specializationOf", "alternateOf")` — the relations tabulated in §2.1 Table 2 / §5.3–§5.4, of which the evidence binding uses at least the first eight; `actedOnBehalfOf` covers delegation (e.g. witness on behalf of operator).
   - `PROV_DERIVATION_SUBTYPES: Final[tuple[str, ...]] = ("Revision", "Quotation", "PrimarySource")` — §5.2 / §6.1 subtypes of `wasDerivedFrom`; remedy naming is `Revision`.
   - Keep the C2PA/VC blocks unchanged; the new block cites `W3C REC-prov-dm-20130430 §5.1.1 / §5.1.2 / Table 2 / §5.2 / §5.4` in its module comment, like the existing `Ch.16.3 p.111` and `§1.1 / §4.10` citations.

2. **Pure validators, same shape as `provenance_disclosure_is_valid` / `vc_role_is_valid`:**
   - `prov_type_is_valid(value) -> bool` — `value in PROV_TYPES`.
   - `prov_relation_is_valid(value) -> bool` — `value in PROV_RELATIONS`.
   - `prov_derivation_subtype_is_valid(value) -> bool` — `value in PROV_DERIVATION_SUBTYPES`.
   - Optional mapping helper `prov_was_derived_from_holds(used_entity: str, generated_entity: str, *, subtype: str = "Revision") -> bool` — returns `subtype in PROV_DERIVATION_SUBTYPES` and `used_entity != generated_entity`, mirroring `provenance_revocation_status` / `provenance_timestamp_binding_holds` shape: a predicate over codes/identifiers, not a filesystem read. Whether two paths actually stand in that relation remains an owner decision; the vocabulary only gives the words.

3. **Acceptance, checkable on the committed bytes (no fixture, no network):**
   - `python -c "import mishe_tauftauf.outcome_publication as p; assert p.PROV_TYPES==('Entity','Activity','Agent'); assert 'wasGeneratedBy' in p.PROV_RELATIONS; assert 'wasDerivedFrom' in p.PROV_RELATIONS; assert 'wasAttributedTo' in p.PROV_RELATIONS; assert p.prov_type_is_valid('Entity') is True; assert p.prov_relation_is_valid('wasGeneratedBy') is True; assert p.prov_derivation_subtype_is_valid('Revision') is True; assert p.prov_relation_is_valid('notARelation') is False"`
   - `rg -n "PROV_TYPES|PROV_RELATIONS|PROV_DERIVATION_SUBTYPES|prov_type_is_valid|prov_relation_is_valid" src/mishe_tauftauf/outcome_publication.py` shows only the new block.
   - `rg -rn "REC-prov-dm-20130430|PROV-DM" src/` shows only `outcome_publication.py` boundary comments until a publication review lands — same invariant the C2PA/VC blocks preserved.
   - Compatibility: `OutcomePublication` / `decode_publication_body` / `reference_*` contract unchanged, `pytest tests/test_outcome_publication.py -q` and existing `tests/test_publication_*.py` still pass; `python3 research/field-review/field_review.py check` stays PASS.

4. **Predecessor-and-limits line (so the review is not read as a defect claim):**
   - Predecessors: `evidence-and-verdicts--transfer--2026-10-22` (CT history/auditor) and `evidence-and-verdicts--transfer--2026-11-01` (Jøsang amount/u) remain true; this transfer covers relation (within one row), not history (across readings) nor quantity (across rows).
   - Limits: no PROV-O ontology, no PROV-N/PROV-JSON serialisation, no RDF store, no bundle persistence, no change to `Feed.append` / `seed yield` / `discovery.py` verdict words / `eligible_evidence_path` allowlist — exactly the source's §1 extensibility boundary: the model is domain-agnostic, the words are the contribution.

**Prediction / falsifier:** if the block lands, a future `wall-outcome` reviewer can label `evidence.path + sha256` as `Entity` `wasGeneratedBy` `publish` `wasAttributedTo` `role` and a `-v2` remedy as `wasDerivedFrom` `Revision` without adding a parser site; if it does not land, that labeling stays reconstruction from regex and comment.

**Owner:** genome (outcome publication dialect), with senses for any `wasAttributedTo` wording that the ledger sense also emits.

## Limits

The source is a conceptual data model, not a protocol; adopted constants do not claim conformance, storage, or exchange. The distinction between byte equality (`sha256` same) and entity identity (fixed aspects differ) is the only theorem transferred — Section 5.1.1's individuation criterion.
