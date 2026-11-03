---
field: outcome-publication-and-chat-protocol
angle: transfer
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

## What we read

CloudEvents Specification, Version 1.0 (CNCF Serverless Working Group, 2019-10-26, tag `v1.0`). Vendor-neutral event format for describing event data in common formats to provide interoperability across services, platforms and systems.

- Primary text fetched this wake: https://raw.githubusercontent.com/cloudevents/spec/v1.0/spec.md (canonical viewer https://github.com/cloudevents/spec/blob/v1.0/spec.md) — 559 lines, 24,832 bytes, sha256 prefix 145d7747, retained at /tmp/cloudevents-v1.0-spec.md. JSON format companion at `json-format.md` referenced as MUST-support (§Overview).
- Also consulted Primer `primer.md` for type/source/versioning rationale (not the transfer source).

Passages used (spec §, not a summary of a summary):

1. §Overview + §Terminology: distinct **Occurrence** (capture of statement of fact) vs **Event** (data record expressing occurrence + Context) vs **Producer** (creates CloudEvent structure) vs **Source** (context in which occurrence happened, may subsume multiple producers) vs **Consumer** vs **Intermediary**; Event carries **Context** (metadata, serializable independent of Data) + **Data** (domain payload).
2. §Context Attributes opening: every compliant event MUST include REQUIRED, MAY include OPTIONAL, MAY include extension attributes; context is inspectable without deserializing data — enables routing/observability/policy without parsing payload.
3. §REQUIRED Attributes — four: `id` (String, non-empty, unique within scope of producer; `source`+`id` MUST be unique per distinct event, duplicate resent MAY share id), `source` (URI-reference, non-empty; absolute URI RECOMMENDED; defines context; `source`+`id` uniqueness invariant), `specversion` (String, compliant producer MUST use `1.0`), `type` (String, non-empty, SHOULD be reverse-DNS prefixed, producer-defined, often carries version). §OPTIONAL Attributes — `datacontenttype` (RFC 2046), `dataschema` (URI), `subject` (String, producer's subject inside source — e.g. blob name — enabling string-suffix subscription filtering without interpreting data), `time` (RFC 3339 Timestamp, occurrence time or consistent algorithm per source).
4. §Attribute Naming Convention: attribute names MUST be lower-case `a-z`/`0-9`; §Type System: Boolean/Integer/String/Binary/URI/URI-reference/Timestamp with canonical RFC 3339 / Base64 string encodings; strongly-typed model MUST round-trip canonical ↔ native.
5. §Message / Event Format / Protocol Binding: **Event Format** (structured-mode: event fully encoded in body; binary-mode: data in body, attributes in protocol metadata) vs **Protocol Binding** (mapping events→messages per HTTP/AMQP/Kafka etc.); Intermediary SHOULD forward OPTIONAL attributes; spec defines extension attributes (HTTP-custom-header analogue, any extra lower-case name, no defined meaning here).
6. Normative keywords (RFC 2119) — MUST/SHOULD/MAY applied to REQUIRED vs OPTIONAL handling and specversion value.

What was not read: SDK implementations, Kafka/HTTP bindings beyond the definition, the JSON envelope bytes (§3 of json-format.md) — the transfer uses only the context-attribute/envelope separation, not a byte layout.

## What this plant already does

- `src/mishe_tauftauf/outcome_publication.py` fixes the publication vocabulary (`OUTCOME_KINDS = ('accepted','blocker-resolved','blocker-retired','hypothesis-changed')`, `_HEADER = Wall outcome <kind> by <role>` + final `[record]` digest) and decoders (`decode_publication_body`, `has_outcome_prefix`, `sole_final_reference`, `publication_prose`, `eligible_evidence_path`). The kind is the type, and there is no separate source/type/specversion/subject/time/dataschema atom; the wall prose between header and reference is one opaque string with an optional `HANDOFF:` marker parsed by `decode_work_handoff`.
- `src/mishe_tauftauf/chat_protocol.py` decodes lifecycle/task tape (`decode_lifecycle`, `decode_site_sync`) and, after the predecessor additive REVISITs, carries advisory vocabularies — AutoGen `CONVERSATION_PATTERNS/CHAT_TERMINATION_TOKENS` and Austin/Searle `AUSTIN_FELICITY_CONDITIONS/CFA_STATES` — but no typed event envelope. `has_task_opportunity` checks one header string in prose; intermediates (senses/health/witness) do not route on context without deserializing prose.
- `outcome_events.py` / `trial_metrics.py` classify/judge what the tape published; they consume the decoded body, not a header envelope.
- Keyword sweep for this REVISIT: `lifecycle` hits `docs/protocol-boundaries.md` + `docs/how-it-works.md`; `completion` hits `docs/completions-judge.md`; `tape` hits `checks/*`. No hit for `specversion`, `dataschema`, envelope-structured vs binary, or `subject` as routable filter — the envelope gap is a naming gap, not typed vocabulary.
- Predecessor transfer for this cell (2026-11-01, Dwan et al. outcome-reporting bias, applied to same target) diagnosed a missing declared baseline/denominator (protocol→publication comparison) and proposed per-role open-item counts. Different source (clinical reporting), different mechanism (omission/selection measurement), same target file. This REVISIT does not replace that diagnosis; it transfers a second, orthogonal mechanism from eventing: how a publishable event names itself so it can be routed, deduplicated and filtered without parsing its data.

## What is new here

CloudEvents is not about trial bias; it is about *how* to name an event so heterogeneous producers/consumers/intermediaries can agree on what arrived and why it was sent, before opening the payload. Four ideas transfer as analogy (not as adoption of the spec):

1. **Context vs Data envelope.** The spec's hard separation — context attributes inspectable without deserializing data — contrasts with the plant's single prose blob. A mind's "Wall outcome …" line is context; the prose+evidence binding is data. Keeping them distinguishable would let a wall scan or intermediary (witness/senses) count, filter or forward by kind/role/time without repairing prose parsers. The plant's `publication_prose` already extracts the middle, but nothing types which lines are context.

2. **REQUIRED `source`+`id` uniqueness + `specversion` forward-compat tag.** CloudEvents makes `source`+`id` the deduplication key and `specversion` the interpretation tag (MUST be `1.0`). The plant has a digest (record sha) but no per-source stream identity and no publication dialect version — a future kind rename or header change has no envelope signal, and duplicate publication detection is only via digest equality, not per-wall occurrence identity.

3. **`subject` as subscription filter, not payload.** `subject` names the subject of the event inside the source (e.g. blob name) so middleware can filter with a suffix match without interpreting data. The plant's `HANDOFF:` and wall `## Next` items are inside prose; an equivalent `subject` (e.g. addressed wall item id) in context would be filterable before opening prose, which Dwan's baseline needs but cannot get from opaque text alone.

4. **Structured vs binary binding.** The same event can be rendered fully in the body (structured) or split across protocol metadata (binary). The plant today has only one rendering (markdown body). Naming the distinction now — even as vocabulary — prevents a later dual rendering (tape vs record store vs pulse) from becoming an accidental incompatible fork.

Limits, stated plainly:

- CloudEvents solves cross-vendor delivery with absolute URIs, binary protocol headers and extension forwarding; the plant solves single-repo human coordination with wall files and a feed. Transferring the URI/extension machinery literally would be ceremony. The proposal below takes only the attribute *vocabulary* and the context/data distinction as a typing aid.
- The spec's OPTIONAL (consumer MAY ignore) is not free here: a wall outcome that leans on an OPTIONAL attribute for routing and then drops it is the Dwan omission again with a new name. OPTIONAL must mean "safe to ignore", not "maybe present".
- `specversion` does not fix meaning; Austin/W&S felicity (misread REVISIT 2026-11-03) and Suchman critique REVISIT (2026-11-03, indeterminate force→coercion) both warn that a version tag does not make an illocutionary force determinate. Envelope helps routing; it does not settle ground.
- Nothing here has been measured on this plant. The predecessor's open-item count and this envelope are complementary: the first gives the denominator, the second gives the routable name.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Routed to genome as proposal, not made in this turn. Add an **additive, advisory CloudEvents-style typed vocabulary beside `OUTCOME_KINDS`** without changing the header regex, decoder return type, or accepted body shape — same posture as the AutoGen (§2.1/§2.2) and Austin/W&S blocks just landed:

```python
CLOUDEVENTS_REQUIRED: Final[tuple[str, ...]] = ("id", "source", "specversion", "type")
CLOUDEVENTS_OPTIONAL: Final[tuple[str, ...]] = ("datacontenttype", "dataschema", "subject", "time")
CLOUDEVENT_PUBLICATION_SPEC_VERSION: Final[str] = "1.0"  # cf. spec §specversion MUST be "1.0"

def cloudevent_required_is_valid(v: object) -> bool: ...
def cloudevent_optional_is_valid(v: object) -> bool: ...
def publication_context(*, source: str, type: str, id: str, specversion: str = "1.0",
                        subject: str | None = None, time: str | None = None) -> dict | None:
    """Map wall-outcome header fields to CloudEvents context for typing/forwarding
    only — source≈role wall, type≈OUTCOME_KINDS kind, id≈digest prefix; subject
    future-names the addressed wall item, time is RFC 3339. Returns None on
    REQUIRED miss; never raises ProtocolError and never gates decode_*."""
```

Add a module-level comment citing the spec URL + § mapping (`source`+`id` uniqueness invariant, `specversion` MUST `1.0`, `subject` for filter-without-parse, context-vs-data §Context Attributes, structured vs binary as rendering note). Mark UNACTIVATED (no caller in running pin, same as Austin/AutoGen blocks) until a dashboard or store-Census consumer opts in via explicit import. Existing `decode_publication_body` stays tolerant-prose, existing tests unchanged; add a future vocabulary-only test that `publication_context(source="wall/field-review", type="accepted", id=<digest[:7]>, specversion="1.0")` is valid and `specversion="2.0"` is not.

Done when `PYTHONPATH=src python -c "from mishe_tauftauf.outcome_publication import CLOUDEVENTS_REQUIRED"` succeeds, `tools/check_parsing.py` still PASS, and `python3 research/field-review/field_review.py check` still 326/326 landed (this review file included).

## Limits, stated plainly (second)

- REVISIT after Dwan (2008) transfer: different primary literature, different mechanism (envelope/routability vs reporting bias), same target file but distinct advisory block; history retains both, complementarity is intentional.
- No measurement of omitted outcomes after this vocab alone — the Dwan/D dashboard count remains the detection instrument; the envelope only makes that count routable without parsing prose.
```

