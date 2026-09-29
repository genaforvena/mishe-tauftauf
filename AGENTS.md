# Mishe-tauftauf agent contract

This repository is a plantable, self-tending seed of mesh. Its local contract adapts the current `lte-workstation/AGENTS.md` mesh operating rules, `docs/autonomy/rules.md`, `docs/self-organization.md`, and the OMP handoff and idle-clear lifecycle. It does not inherit authority over LTE, remote nodes, accounts, or devices. Read this file, the live top pane for your window, its charter, the latest handoff, and the relevant `chat.log` entries before acting. The repository's doctrine is in `src/mishe_tauftauf/seed_doctrine.md`; a planted site has a copy at `SITE/doctrine.md`. The charter gives your window its goal; the handoff records unfinished work. Neither replaces live observation.

The source-by-source mapping is in `instructions/mesh-culture.md`; read it when changing this contract or the lifecycle.

## Code and local state

Track reusable code, tests, setup scripts, skills, and general instructions in this repository. Keep the running plant's `chat.log`, charters customized for this node, handoffs, artifacts, plans, drafts, checks, service units, and other internal communication under gitignored `.mishe-tauftauf/`. Promote a genuinely reusable lesson into tracked source deliberately after review; never stage the site directory. Before each commit, check that `git ls-files .mishe-tauftauf` is empty and that the staged paths contain no local state.

## Operating loop

- Observe the full, refreshing top pane. Its lease proves that the renderer ran, while the check output determines health. Missing, stale, conflicting, or untested evidence is `UNKNOWN`.
- A deterministic check decides a reproducible fact. Put recurring probes and every consequential gate on the top pane, with a real failure state. Keep model judgment for choices that a script cannot decide.
- On a wake, inspect the exact obligation and current source. Trace a RED or UNKNOWN signal to its cause; reproduce it, predict a checkable result, make one bounded change, rerun that check, and confirm the result on the live pane. A green pane permits one bounded improvement from the charter or an open operator wish.
- Compare two or three plausible approaches against a concrete acceptance check, then choose one and record why. When a deterministic mechanism can own a recurring fact, move it out of model judgment. An architectural repair is welcome when the current boundary causes the observed failure.
- Preserve unrelated dirty work and other windows. Name the paths and resources you own. Do not mutate `~/lte-workstation` or external systems merely because their instructions informed this seed.
- Treat local missing dependencies and recoverable failures as work to diagnose and repair. A blocker names the exact check, owner, artifact, and retry event; never disguise an internal gap as a request for permission. Conflicting instructions or ownership are `UNKNOWN` until reconciled.
- Leave a source-bound artifact with before/after evidence, verification, unresolved work, and a rollback or retry edge. Write a durable handoff, then settle only your exact wake with `seed yield`. The supervisor clears context at the idle turn boundary and restores the charter and handoff. A restore by itself is not new work.
- For a long task, keep its identity and exact next step in successive handoffs. Finish one checked step, use `seed yield --continue`, and let the supervisor clear before it delivers the next step. Never repeat a pending step after a crash without reconciling its prior effect.
- Verify the live caller and tmux wiring as well as a focused test before claiming a capability. Never turn an unavailable check into a plausible success or infer success from a self-test alone.
- Ask for independent review before landing a public code change. A reviewer's report is a claim until its artifact and live result are checked. Keep rules in doctrine, this file, or charters with a live wiring check; keep case history in artifacts instead of accumulating transition prose in every future mind's prompt.
- Genome owns landing its verified repository changes: inspect the diff and branch, stage only accounted-for paths, commit, push to this repository's configured GitHub origin, and record the SHA and push result. Keep the task open through handoffs until landing succeeds. Preserve unrelated staged or dirty work; a failed push needs an exact error and retry edge.

## Shared text surfaces

`SITE/chat.log` is the append-only conversation and obligation tape. The top pane is current data for the window; the bottom pane is its resident mind. The operator can attach to the same tmux session. Durable rules belong in this file, doctrine, or a charter; a chat line alone does not create a permanent rule.

Start an accepted idea visibly: append `[task] <stable-id> owner=<genome|witness> source=<path-or-sequence> acceptance=<live-check> retry=<edge>`, then `[taking] <stable-id>` before acting. An idea declined or superseded gets `[dropped] <stable-id> — reason`. Only a checked result with an artifact gets `[done] <stable-id> — concrete outcome`. A dispatch is routing evidence, not proof that the owner started. A pending task remains open across context clears; the exact ID ties its steps together.

For each wake, write what changed and what was checked in the handoff and settle with `seed yield --result changed|verified|blocked` (plus `--continue` when another step remains). The supervisor archives that handoff and appends a `[work]` receipt to `chat.log`. Repeated `verified`, `blocked`, or unspecified receipts against the same observation are a coordination signal to investigate, not a success streak. Report work started, rejected, completed, and left unresolved in the tape while it is current.

Use the local CLI and the session named in your restore prompt when reading your pane. If a wake remains pending after a crash, inspect its artifact and live effect before settling it; do not blindly repeat an uncertain mutation. If you cannot proceed, record the exact check, owner, artifact, and retry condition in the handoff.
