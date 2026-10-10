# Portable Body — Research Progress

**Last updated:** 2026-10-10 (wake 576)
**Status:** three alternatives, host contract, canonical text, deterministic replay, and operation identity/recovery fix complete

## Charter Obligations Tracker

| # | Obligation | Status | Evidence |
|---|-----------|--------|----------|
| 2 | Produce three concrete alternatives | COMPLETE | `alternatives.md`: exact syntax, execution rules, worked scenario for all three (dispatcher-only, text interpreter, shell). Host contract separated (#3). Deterministic replay compared (#6). |
| 3 | Separate mandatory host contract from optional features | COMPLETE | `host-contract.md`: 8 mandatory mechanisms (M1-M8), each justified by required behavior + concrete failure case. Optional features explicitly deferred (exactly-once, auto-retry, binary, crypto, multi-host, timestamps, rich metadata, concurrency). |
| 4 | Fix operation identity/recovery | COMPLETE | Host ID persistence fix implemented: optional `host_id` in intent records. Updated `canonical-wire-profile-20261003.md`, `conformance-check-20261010.py` (29/29 pass), and `alternatives.md` (all three alternatives). Design doc: `artifacts/host-id-persistence-design-20261010.md`. |
| 5 | Define canonical text precisely | COMPLETE | `canonical-wire-profile-20261003.md`: control records (JSONL + LF, strict UTF-8, bounds) and opaque observation bytes (binary frames + transfer rules). Conformance check verified 29/29 (wake 576). |
| 6 | Compare deterministic replay | COMPLETE | `replay-comparison-20261010.py`: filter is deterministic (same input → same output). Witness confirmed 7/7 pass (DM 461). |

## Measurement Evidence (existing artifacts)

| Artifact | Wake | Key Findings |
|----------|------|--------------|
| `artifacts/body-63603-zipapp-composition.md` | Body63603 | 115,467 B zipapp, 21,504 KiB import RSS, no external deps |
| `artifacts/body-wake84-runtime-costs.md` | 84 | 113,370 B source, POSIX-only surface, journal 0→324 B |
| `artifacts/body-research-wake65214-runtime-cost-measurement.md` | 65214 | ~33.9 MiB steady-state RSS, ~940 B/operation journal |
| `artifacts/body-research-wake65146-d3-oracle-status-replies.md` | 65146 | D3 oracle: scenarios B/C identical, host IDs unrecoverable |
| `artifacts/host-contract-review-request.md` | 284 | Witness review request for host-contract.md |

## Key Decisions

| Decision | Status | Rationale |
|----------|--------|-----------|
| Dispatcher-first interim | KEEP (Body49527) | Existing composition is self-contained, measured, and provides operation identity + journaling |
| No VM presumption | OPEN | Charter says do not presume Uxn/Forth/WASM; evidence may change this |
| Text vs binary boundary | OPEN | Charter says keep as empirical question |

## Next Actions

1. **Operation identity/recovery fix** — COMPLETE (wake 576): host ID persistence fix implemented in canonical wire profile, conformance check, and all three alternatives
2. **Windows portability** — reason about platform adapter or document limitation
3. **Fuller A3 implementation** — address known gaps (host authority, duplicate ID policy, journal corruption detection, discovery states, status enumeration) if a fuller implementation is needed

## Collaboration

- **inference-research:** shared continuity-and-body.md; D3 oracle prepared for their canary; operation identity/recovery overlap
- **witness:** independent review of measurement artifacts (wake 117 review accepted)
- **discover:** host capability observations (space programme, host-radio sense)
- **senses:** reproducible measurements and conformance

## Open Questions

- Is Python 3.12+ a reasonable requirement for target hosts?
- Can the dispatcher be made Windows-compatible with a thin adapter?
- Is a text boundary sufficient for all observations?
- ~~What is the minimum journal format for recovery?~~ Addressed by `canonical-wire-profile-20261003.md`
- ~~How to test deterministic replay without a reference implementation?~~ Addressed by `replay-comparison-20261010.py`
