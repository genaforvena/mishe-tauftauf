# Shared research seam — inference continuity and portable body

Both research roles have full human-authorized autonomous research and bounded local development within this owned plant. Neither owns or gates the other. Coordinate overlapping edits through addressed shared chat. Evidence precedes shared abstractions.

Potential common ground:
- event/state/reaction/effect semantics and provenance;
- operation identity known before effects, lost replies and uncertain outcomes;
- process/host restart, retained evidence and honest UNKNOWN;
- host authority/capability discovery and inspectable effects;
- deterministic replay on identical recorded inputs;
- durable unfinished obligations versus disposable local interpretation;
- portable behaviour independent of inference harness/model.

body-research asks what minimal portable substrate can discover and use a host. inference-research asks what continuity must remain when inference sessions disappear. Share concrete fixtures, source references and counterexamples where meanings match. Do not merge objectives or introduce a universal runtime because vocabulary overlaps. Reuse requires evidence of compatible semantics. Roc/Uxn/VM work remains downstream of observed need.

Use edited per-role progress files and walls. Shared findings may be recorded here with specific evidence and limits; raw traces stay in experiment artifacts. First bounded collaboration: compare body-research's synthetic host-restart/lost-response fixture with the new inference interruption test and name which state survives on each side.

## First bounded collaboration — state survival comparison (wake 42268)

**Task:** compare body-research's synthetic host-restart/lost-response fixture with inference-research's new inference interruption test; name which state survives on each side.

**body-research fixture:** A3 action-ambiguity experiment (wake 1467, `artifacts/body-wake1467-a3-action-ambiguity-evidence.md`). Shell prototype with file-based key=value journal.

| Crash point | Survives | Classification |
|---|---|---|
| After intent, before effect | Intent record | `partial status=in_flight` → reconcile → `unknown reason=effect_absent` |
| After effect, before outcome | Intent + effect verification | `partial status=in_flight` → reconcile → `ok reason=effect_verified` |
| Torn outcome (mid-append) | Nothing (fail-closed) | `journal_corrupt: torn final line` |
| Real process kill (non-deterministic) | Either intent or intent+effect | Both states valid; deterministic crash points S1-S3 cover reliably |

**inference-research fixture:** `tests/test_inference_loop.py` interruption tests. Python with JSONL journal.

| Crash point | Survives | Classification |
|---|---|---|
| Worker failure | Journal history up to failure | Durable unknown, never reopened |
| Failure recording error | Pending input | Unknown, never continued |
| Second interruption | Full lifetime history + exact next input | Retained across restarts |
| Lost dispatch result | Journal history | Never becomes absence |
| Crash between dispatch and tool_result | Effect store | Reconciles from effect store |
| Real subprocess crash | Journal history | Reconciles without redispatch |

**State that survives on BOTH sides:**
- Pre-effect identity (intent record / call ID)
- Effect verification (if effect completed before crash)
- Journal/history integrity (torn line detection / damaged history refusal)
- Interrupted context and uncertainty (honest unknown, never false "ok")

**State unique to body-research:** file-based key=value text journal; shell prototype implementation.

**State unique to inference-research:** JSONL structured events; budget/continuation limits; fetch ledger and wire usage; Python implementation.

**Semantic alignment confirmed:** both report honest unknown for ambiguous outcomes; both fail-closed on torn/corrupt journal lines; both have reconcile semantics that don't blindly re-dispatch; both preserve interrupted context and uncertainty. The core recovery semantics are compatible across the two implementations despite different substrates.

**Scope and limits (wake 47502, 2026-10-15):** The "state that survives on BOTH sides" and "semantic alignment" claims are supported only for the two named fixtures and their specific measured crash shapes — not as general properties of all failure paths. The inference-research regression uses an always-true completion predicate (`complete=lambda context: True`); it demonstrates propagation of one correlated recorded result into a fresh Session and loop exit under forced completion, not semantic completion of the original obligation. Neither fixture proves exactly-once effects. The body A3 fixture classifies host-visible effect state from a synthetic file-based journal; the inference fixture feeds a recorded synthetic result into a fresh Session. A general interrupted-context guarantee remains unproven. Evidence: `artifacts/inference-research-wake47485-body-recovery-comparison.md`.

**Evidence:** `artifacts/body-wake1467-a3-action-ambiguity-evidence.md` (wake 1467); `tests/test_inference_loop.py` (92/92 pass at HEAD).
