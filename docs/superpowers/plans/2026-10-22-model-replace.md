# Model replacement implementation plan

Goal: one installed mishe model replace command, with preview, status, revert and explicit fresh-wake abandonment.
Architecture: independent typed model-change service; literal launcher/catalog/process/receipt adapters in model_boundary.py. Reuse seed ownership/locking/launch environment, Feed and existing immutable snapshot deployment. Preserve unfinished peer mind_loop work; no other branches or worktrees.

User clarification: loss of transient context is authorized. Fresh start preserves evidence, records abandonment and prevents old wake replay. It does not certify prior effects succeeded or undo them.

1. Add behavior tests for exact matching, preserving argv/env, malformed/custom refusal, model/effort preflight, dry-run, write conflict, receipt/revert and fresh abandonment. Run them failing before implementation.
2. Implement boundary decoders and typed configuration; strict support for simple export-plus-exec OMP and explicit native model launchers. Unsupported launchers stay UNKNOWN. Keep credentials as references only. Validate catalog and a bounded no-tools provider call in the effective environment before mutation.
3. Implement durable operations, immutable before/after files, idempotent re-entry, fresh process identity, site ownership and serialized activation. Add canonical abandon lifecycle decoding. Preserve historical evidence and route unknown effects in the fresh-wake brief.
4. Add CLI model replace/status/revert/watch commands. A bounded foreground wait and independent watch support queued idle changes and failure routing. Show selected/queued/activated/verified/unknown separately. Add scoped installed CLI snapshot and monitor with tested revert/reapply.
5. Add concise book/reference docs and explicit boundary policy. Run focused tests, parsing check and independent code review. Fix issues. Exercise isolated end-to-end process activation, failure detection, stale/missing status and backout/forward. Commit only owned paths on main; record live limits honestly.

Acceptance: command is installed and usable, a change has a stable id, no hidden global restart/auth change, unknown/custom/pending states are explicit, fresh-start cannot replay an abandoned wake, configured bytes are distinguished from observed processes/provider results, and revert refuses subsequent edits.
