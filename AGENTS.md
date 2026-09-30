# Mishe-tauftauf agent contract

This repository is a plantable, self-tending seed of mesh. Authority covers this repository and owned local plant state; it does not extend to LTE, other nodes, accounts, or devices. Source mapping: `instructions/mesh-culture.md`.

Read this contract, the full live top pane, your charter, latest handoff, relevant `chat.log` entries, and current source before acting. Shared behavioral rules are in `src/mishe_tauftauf/seed_doctrine.md` (planted as `SITE/doctrine.md`). Charters add role-specific goals; handoffs record unfinished work. Neither replaces live observation.

## Work and evidence

- Act proactively within granted scope. On every wake, advance ready owned work or offered work before choosing a new improvement. Idle is not an acceptable choice while an authorized useful step is available. A healthy pane does not complete open tasks.
- Look for defects, missed obligations, and useful opportunities throughout the work. Claim and repair a concrete finding, or route it with evidence, an owner, and an acceptance check. Every mind may improve any Mishe layer within owned scope; a role is a responsibility, not a file restriction. Coordinate existing owners and preserve unrelated work and windows.
- Reproduce RED or UNKNOWN, predict a checkable result, compare two or three plausible approaches, choose a bounded repair, rerun the same check, and verify the live result. If no ready task remains, choose a useful charter improvement or operator wish. Missing, stale, conflicting, or untested evidence stays UNKNOWN; a refresh lease proves renderer liveness only.
- Put recurring facts and consequential gates in deterministic checks with visible failure states. Verify the actual caller and tmux presentation as well as focused tests. A self-test alone does not prove deployment.
- Diagnose local dependencies and recoverable failures. Inspect the checkout's virtual environment and project commands before claiming a test runner is missing; record the actual command and result. Use the permissions flow only for a genuinely missing external capability; granted authority requires no further approval.
- A blocked task needs the exact failed check, artifact, prerequisite producer, and retry condition. Produce missing local deliverables or advance independently admissible work while final acceptance waits. Preserve budgets, frozen registrations, and external boundaries. Repeating an unchanged audit or changing an artifact hash is not progress.

## Tasks and shared tape

Use the canonical CLI and home from the restore prompt. Check `task show` for ownership, next steps, consumed attempts, and retries before acting. Active wakes reserve their tasks. Advance your own ready work, then offered ready work; helper selection records ownership transfer. Never retake an unchanged waiting task until its retry fires.

- Start visibly with a separate `[task] <stable-id> owner=<role> source=<path-or-sequence> acceptance=<check> retry=<edge>` entry, then `[taking] <stable-id>`. `[done]` requires checked completion and an artifact; `[dropped]` requires a reason. Dispatch is not proof of start or completion.
- Create bounded deliverables with `task add ID --owner ROLE --parent GOAL --next-step TEXT --reason TEXT --evidence FILE`. Complete managed tasks only with `task finish ID --owner ROLE --result TEXT --evidence FILE`; keep parents open until their acceptance and children are complete. Correct a terminal task with evidence-backed `task reopen`; duplicate announcements never reopen it.
- Record progress with `task step ID --owner ROLE --next-step TEXT --progress TEXT --evidence FILE`. Record a wait with `task wait ID --owner ROLE --next-step TEXT --reason TEXT --evidence FILE --producer ROLE` and `--retry-event TOKEN`, timezone-bearing `--retry-at`, or `--retry-task ID`. Use producer completion for review readiness. Create an independently ready child and name `--alternative ID` when final acceptance waits. Completion dependencies cannot form a cycle.
- Publish `task event TOKEN --source ROLE --reason TEXT --evidence FILE` only when its condition changes. Each selected step or fired retry permits one attempt. Offer related work with `task offer ID --owner ROLE --helper OTHER_ROLE --evidence FILE`; omit helpers to withdraw it.

`SITE/chat.log` is the append-only conversation and obligation tape. Append ordinary entries only through `mishe-tauftauf --home SITE append --source ROLE 'readable text'`; the feed owns numbering and framing. Each entry explains the event, evidence, and next owner or action. Put lifecycle tags at the start of separate entries; tags buried in handoffs do not update the board. Structured task control tags belong only to the task CLI. Keep unchanged samples in artifacts. If framing is corrupt, preserve bytes, repair only the corrupt frame under the feed lock, and verify parser and live panes.

## Wake completion and delivery

Leave a source-bound artifact with before/after evidence, verification, unresolved work, and a rollback or retry edge. Write a durable handoff, then settle only the exact wake with `seed yield --result changed|verified|blocked`. For an actionable next step, keep the task ID, record its next step, and use `--continue`. For a checked wait, record the retry instead. Repeated no-change receipts against one observation require investigation.

Yield ends a bounded wake, not an unfinished task. The supervisor clears at the settled idle turn boundary and delivers instructions with the next real wake. A clear or restore alone creates no model work. Before retrying an unsettled wake after a crash, reconcile its prior effects.

Source authors own delivery in an isolated worktree based on current `origin/main`: stage only accounted-for paths, commit, obtain independent exact-base/head review, publish the branch, and `delivery submit`. Check the review artifact; renew review after revision changes. The watcher creates genome integration work only after exact branch CI passes. Genome serializes ready main updates; the author retains exact final main CI, clean release rollout, and live consumer verification through `delivery finish`. Await delivery transitions instead of polling. Source debt does not hold unrelated production. Commands: `docs/operating.md`.

When shared kernel instructions or code change, refresh every active site using that source and verify its next restore, live panes, and services. Python services must restart to load new code. Durable rules belong in contract, doctrine, or charters; case history belongs in artifacts.

Track reusable source, tests, skills, and general instructions. Keep chat, local charters, handoffs, plans, checks, drafts, artifacts, and service units under gitignored `.mishe-tauftauf/`. Promote reusable lessons deliberately. Before every commit, verify `git ls-files .mishe-tauftauf` is empty and no staged path contains local state.
