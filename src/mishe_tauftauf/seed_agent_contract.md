<!-- mishe-tauftauf plant contract -->

## Resident development culture

This worktree has a resident mishe-tauftauf plant in the gitignored `.mishe-tauftauf/` directory. Read its `doctrine.md`, your `charters/ROLE.md`, current `handoffs/ROLE.md`, live tmux top pane, and relevant `chat.log` entries before acting. The top pane's lease proves that it refreshed; a missing or stale check is UNKNOWN.

Keep reusable source, tests, and instructions in this Git worktree. Keep chat, plans, site checks, local charters, artifacts, requests, handoffs, and service units inside `.mishe-tauftauf/`. Never stage the site directory. Preserve unrelated work and name the paths you own.

For each wake, inspect the exact obligation and live source, reproduce a RED or UNKNOWN result, predict a checkable outcome, make one bounded change, rerun the same check, and verify the live pane. A GREEN pane allows one useful charter improvement. Record a source-bound artifact and handoff, then settle the exact wake with `seed yield --result changed|verified|blocked`. Keep a stable task ID and use `--continue` for unfinished long work. Reconcile uncertain effects before retrying after a crash.

The supervisor clears context after a settled idle turn and delivers the charter and handoff with the next real wake. A clear alone creates no model task. On the next wake, verify the restored handoff against the live pane before acting.

Every mind may act proactively within its charter. A checked UNKNOWN with an exact retry condition stays visible while the mind pursues another useful step. Ask through the permissions flow only for a genuinely missing external capability; already granted capabilities are ready to use.

Separate work admission from final acceptance. A blocked result or publication gate does not suspend an independently authorized measurement, repair, analysis, or limitations draft. Produce locally owned deliverables; do not repeat unchanged gate audits. Preserve frozen registrations, budgets and external authority. Progress names a changed outcome or new evidence, not a fresh artifact hash.

Keep the overall goal open through bounded child tasks: `task add ID --owner ROLE --parent GOAL --next-step TEXT --reason TEXT --evidence FILE`, then `task finish ID --owner ROLE --result TEXT --evidence FILE` for checked child completion. Managed goals cannot close through prose `[done]`; unfinished children prevent parent completion. Use evidence-backed `task reopen` to correct a terminal task. Waiting tasks name `--producer ROLE` and an exact event/deadline or `--retry-task ID`. Review waits on completed producer evidence instead of polling partial output with `--continue`. An independent ready child named with `--alternative ID` may advance while final acceptance waits. Completion cycles are rejected; one independent-work decision is allowed per stable checked waiting backlog.

Structured task control tags belong to the task CLI; ordinary notices use `[update]` or prose. Never manually append task-state, task-event, task-claim, task-add, task-close, or task-reopen payloads.

On every wake, consider whether Mishe itself needs to change. Every mind is encouraged and authorized within owned scope to repair any part of Mishe when evidence requires it: source, checks, prompts, doctrine, charters, roles, task routing, supervisor lifecycle, planting, or coordination. Roles name responsibilities, not restrictions to one layer. Reproduce the cause, compare approaches against a concrete acceptance check, name and coordinate path ownership, implement a bounded repair, and verify its live effect. Route shared source through independent review and genome's landing process. Preserve unrelated work and external authority boundaries; do not defer a repair solely because it crosses your usual layer.

Use `[task]`, `[taking]`, `[done]`, and `[dropped]` in `chat.log` so witness can see progress and unresolved work. Request scoped missing capabilities with `permit request`; only an operator decision in the permissions window can grant plant authority. Genome obtains independent review, stages only owned paths, commits, pushes to the configured origin, and records the SHA and outcome. CI failure remains open until a replacement run for the pushed SHA succeeds.

Write ordinary chat entries with `mishe-tauftauf --home SITE append --source ROLE 'readable text'`. Never write an ordinary entry directly or invent a framed sequence header. The feed CLI owns numbering and framing; one malformed entry blocks every live pane. For a malformed feed, preserve the bytes, repair only the corrupt frame under the feed lock, then verify parser and panes.

Record each task transition as a separate chat entry whose first line starts with its lifecycle tag. A `[done]` buried in a handoff or later in a combined entry does not close the live task board.

Use the canonical CLI to record durable task state before yielding: `task step ID --owner ROLE --next-step TEXT --progress TEXT --evidence FILE`, or `task wait ID --owner ROLE --next-step TEXT --reason TEXT --evidence FILE --retry-event TOKEN` (or timezone-bearing `--retry-at`). Publish `task event TOKEN --source ROLE --reason TEXT --evidence FILE` only when the condition changes. Each selected step or fired retry permits one attempt; `--continue` needs an actionable next step. `task show` exposes current owner, next step, last attempt and retry condition.

An owner can offer related work to suitable helper charters with `task offer ID --owner ROLE --helper OTHER_ROLE --evidence FILE`; repeat the helper flag or omit it to withdraw the offer. The supervisor prefers a mind's own ready work, then offered ready work, and records ownership transfer before delivery. Active wakes reserve their tasks and unchanged waiting prerequisites prevent helper selection. Preserve task identity and unrelated work.

When the shared plant kernel changes, rerun the planting command for every active site using it and verify refreshed panes and resident services. The tracked kernel instructions are authoritative; local site instructions add target-specific detail and stay outside Git.

Every new chat entry must explain what happened, the evidence or referenced artifact, and the next owner or action in plain text. Keep unchanged repeat samples in site artifacts; do not flood the shared log. Stable tags and IDs can lead an entry but are not an explanation by themselves.
