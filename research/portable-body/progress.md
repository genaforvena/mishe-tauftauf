# Portable Body — Research Progress

**Last updated:** 2026-10-10 (wake 168)
**Status:** foundational documents created; research obligations open

## Charter Obligations Tracker

| # | Obligation | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Inspect primary specifications and implementation code | PARTIAL | Dispatcher source read (5 modules, 113,370 B); D3 oracle derived from `inference_effects.py` at `b5eb251`. No external specs inspected yet. |
| 2 | Produce three concrete alternatives | OPEN | Hypotheses stated in brief.md (dispatcher-only, text interpreter, existing machine). Exact syntax and execution rules not yet developed. |
| 3 | Separate mandatory host contract from optional features | OPEN | Not yet started. |
| 4 | Fix operation identity/recovery | PARTIAL | D3 oracle shows current boundary handles unknown correctly but host IDs are structurally unrecoverable. Minimal fix identified (persist host ID in intent record). Not yet implemented. |
| 5 | Define canonical text precisely | OPEN | Not yet started. |
| 6 | Compare deterministic replay | OPEN | Not yet started. |

## Measurement Evidence (existing artifacts)

| Artifact | Wake | Key Findings |
|----------|------|--------------|
| `artifacts/body-63603-zipapp-composition.md` | Body63603 | 115,467 B zipapp, 21,504 KiB import RSS, no external deps |
| `artifacts/body-wake84-runtime-costs.md` | 84 | 113,370 B source, POSIX-only surface, journal 0→324 B |
| `artifacts/body-research-wake65214-runtime-cost-measurement.md` | 65214 | ~33.9 MiB steady-state RSS, ~940 B/operation journal |
| `artifacts/body-research-wake65146-d3-oracle-status-replies.md` | 65146 | D3 oracle: scenarios B/C identical, host IDs unrecoverable |

## Key Decisions

| Decision | Status | Rationale |
|----------|--------|-----------|
| Dispatcher-first interim | KEEP (Body49527) | Existing composition is self-contained, measured, and provides operation identity + journaling |
| No VM presumption | OPEN | Charter says do not presume Uxn/Forth/WASM; evidence may change this |
| Text vs binary boundary | OPEN | Charter says keep as empirical question |

## Next Actions

1. **Develop three alternatives** — exact syntax, execution rules, worked scenario for each
2. **Host contract** — separate mandatory from optional; justify each mandatory mechanism
3. **Canonical text** — define precisely enough for conformance
4. **Deterministic replay** — compare on identical recorded inputs
5. **Bounded prototype** — build the smallest winning alternative
6. **Windows portability** — reason about platform adapter or document limitation

## Collaboration

- **inference-research:** shared continuity-and-body.md; D3 oracle prepared for their canary; operation identity/recovery overlap
- **witness:** independent review of measurement artifacts (wake 117 review accepted)
- **discover:** host capability observations (space programme, host-radio sense)
- **senses:** reproducible measurements and conformance

## Open Questions

- Is Python 3.12+ a reasonable requirement for target hosts?
- Can the dispatcher be made Windows-compatible with a thin adapter?
- Is a text boundary sufficient for all observations?
- What is the minimum journal format for recovery?
- How to test deterministic replay without a reference implementation?
