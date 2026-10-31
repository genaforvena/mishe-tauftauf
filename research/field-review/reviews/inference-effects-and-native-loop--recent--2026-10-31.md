---
field: inference-effects-and-native-loop
angle: recent
date: 2026-10-31
status: discarded
owner: inference-research
---

## What we read

- Model Context Protocol, specification revision 2025-06-18, "Tools" (server feature): <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>. Read the fetched page, which states that tool annotations are "optional properties describing tool behavior" and that "clients **MUST** consider tool annotations to be untrusted unless they come from trusted servers"; its Security Considerations ask clients to "Prompt for user confirmation on sensitive operations" and to "Log tool usage for audit purposes"; and its User Interaction Model says a human "**SHOULD** always be a human in the loop with the ability to deny tool invocations." The annotation fields themselves (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) were not present in the fetched text, so this reading does not quote their definitions or defaults.
- The plant's own boundary text, read from the working tree: `src/mishe_tauftauf/inference_effects.py` module docstring (lines 1-22: a crash "leaves an operation `unknown` for a fresh process to reconcile; it is never silently retried"; the module "does not claim power-loss durability, exactly-once semantics, or general concurrent safety") and the `EffectBoundary` docstring (`unknown` is "never permission for a second execution").
- Attempted and not usable: arXiv 2606.10106, "What makes a harness a harness", reached through search. The PDF fetch returned compressed binary streams and no readable body text, so nothing from it is quoted or relied on here.

## What this plant already does

The plant has a deliberate, stricter answer than the one the spec's hints point toward. `EffectLedger` writes an intent, then a dispatch start, then an outcome, in three append-only journals under one lock. A crash between intent and outcome reads back as `unknown`, and `reconcile` reports that state instead of retrying. `ChangedContentReuse` and `AlreadyExecuted` guard a reused operation id before any effect runs. The boundary takes no position on whether the capability is idempotent: it never re-executes a pending call on its own, whatever the call is.

## What is new here

The spec's own warning is the new item for this plant. It asks clients to treat annotations such as an idempotence hint as untrusted unless the server is trusted. That bears directly on one idea the cell invites: letting an `unknown` call marked idempotent be re-dispatched after a crash, instead of left for hand reconciliation. The plant has no trusted-annotation channel and no reason to add one. The spec's guidance, to confirm sensitive operations with a human and to log tool use, is already what `unknown` plus `reconcile` provides. Any retry rule would rest on a hint the spec says not to trust, and the boundary's current behaviour is the safer reading of the same text.

## Discard

The MCP spec's "untrusted annotations" rule means an idempotence hint cannot authorize a second execution, so the boundary's never-retry-`unknown` behaviour is already the correct reading and no change to `inference_effects.py` follows from this source.
