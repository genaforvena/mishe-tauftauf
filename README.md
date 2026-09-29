# mishe-tauftauf

**A small, plantable culture for autonomous software development.** Clone the code onto a Linux host, give it an agent command, and plant a local tmux session. Each resident channel sees current evidence above its mind, works in the lower pane, and records its obligations and results in a shared text log. The plant can repair its own development loop and grow useful local capabilities one checked step at a time.

This repository supplies the loop, roles, and boundaries. A planted instance adds checks and senses for the system it actually owns. The planting script can tend this checkout or another Git worktree; its local charters and checks then grow around that target.

## Hello world: one observed wake

From this Git checkout, with `tmux`, a working `codex` command, and a user systemd manager available:

```bash
python3 scripts/plant_local.py --engine-command codex
.mishe-seed/bin/mishe-tauftauf --home .mishe-seed append --source operator \
  '[task] hello-world owner=discover source=/proc/loadavg acceptance=fresh-scan-and-live-pane retry=next-scan. Read one real load value, cite the scan artifact, verify the discover top pane, and mark this task done.'
tmux attach -t mishe-seed
```

Watch `discover`: its upper pane should show the fresh reading, and its lower pane should take the task. In another shell, `tail -f .mishe-seed/chat.log` shows `[taking]`, a checked `[work]` receipt with its handoff, and `[done]`. The site directory is ignored by Git. Replace `codex` with your installed agent command if needed; the [planting section](#plant-a-local-instance) covers another worktree and manual trials.

## How it lives

```text
local system and source → sampled observations → refreshing top panes
                                                ↓
                                      resident minds below
                                                ↓
                            bounded action → same live check
                                                ↓
                        artifact + handoff + append-only chat.log
                                                ↓
                              idle clear → restore → next step
```

The top pane is the current, shared view of a channel's goal, readings, and checks. Its moving lease proves the renderer is running; it does not make a failed check pass. A missing, stale, or conflicting reading is `UNKNOWN`. A reproducible fact belongs in a deterministic check with a real failure state. The lower pane is an agent's working shell. The agent chooses among plausible repairs or improvements, while the checks decide what happened.

`SITE/chat.log` is an append-only conversation and obligation tape. A stable `[task]` ID names work and its owner; `[taking]` records that work started; `[done]` requires a checked artifact; `[dropped]` records why an idea was declined. A handoff carries the current task and exact next step through a context clear. A charter carries the channel's lasting purpose. Neither substitutes for reading the live pane again after restoration.
Every new entry must explain the event, evidence or artifact, and next owner or action in plain text. Stable protocol tags stay on the first line for replay; the entry also includes a human explanation. Unchanged repeated samples stay in local artifacts instead of filling the shared tape.

The supervisor wakes a resident mind for a changed observation, an addressed event, a continuation, or a quiet self-pick. The mind takes one bounded step: inspect the source and obligation, reproduce a RED or UNKNOWN result, predict the effect of a change, make that change, and rerun the same check on the live pane. On a GREEN pane it can pursue one useful improvement grounded in its charter or an operator wish. It writes an artifact and handoff, then settles the exact wake with `seed yield --result changed|verified|blocked`. The supervisor archives the handoff, writes a `[work]` receipt, waits for an idle boundary, clears the mind's context, and restores its charter and handoff. A long task keeps its ID and uses `--continue` to receive another step after the clear. An unsettled wake is reconciled before any possible effect is repeated.

Every mind has standing authority to pursue useful work within its charter. When a source is unavailable, it leaves an honest `UNKNOWN` and an exact retry condition, then explores another candidate. A missing external capability goes through the scoped permissions ledger; granted capabilities are ready to use. Senses can sample and wire a new reading, then hand reusable source changes to genome for review and landing.

## The initial channels

| Window | What the upper pane shows | What the lower pane does |
| --- | --- | --- |
| `discover` | Recent bounded reads and capability candidates | Finds useful local readings, tests availability, and routes a candidate to its steward |
| `senses` | Sampled readings, freshness, and unknowns | Turns useful reads into truthful senses with a source, failure state, and consumer |
| `health` | Doctor, expected tmux windows, resident services, and CI state | Repairs the plant's local feed, panes, and services |
| `genome` | Repository goal, worktree, doctor, CI, and next development step | Changes reusable source, verifies it, obtains independent review, and lands owned changes |
| `witness` | Open tasks, conversation, CI events, receipts, and channel health | Routes work, follows it through completion, and checks claims against artifacts and live results |
| `permissions` | Pending and decided requests | Gives the operator a shell for scoped grant and revoke decisions |
| `operator` | A shared shell | Lets a person or agent inspect and speak to the plant |

The progression is **discover → senses → health → genome → witness**: identify what can be observed, make the observation reliable, keep the local substrate working, improve the reusable code, and independently check that work is finished. These are responsibilities, not a mandatory sequence for every task. A fault goes to its owner; a reusable source change goes to genome. The witness keeps unfinished IDs visible. The operator can attach to the same text session and read the same tape.

Discovery starts with bounded, read-only local samples: commands on `PATH`, `/proc` and `/sys` values, disk space, tmux windows, and input activity counters. A command's presence is only a candidate; a working sense needs an actual sample and an honest status. Input activity counts do not capture key content. The plant can ask for a capability it cannot use within its present scope: `permit request` records a stable ID, owner, task, capability, reason, and each path the decision would unblock. The operator's `permit grant` or `permit revoke` appears on the permissions pane and in `chat.log`. This ledger records a decision for this plant; it does not change Linux permissions or device access. A pending or revoked request is no authority to act.
Each discovery log entry explains the observed commands, verified readings, unknowns, artifact, and next action in plain text. Repeated scans still refresh the local sample, but they add a chat entry only when availability, status, or an unknown reason changes. Discover and senses wake on those meaningful changes rather than on every changing counter.

## Where this fits

| Project | Primary unit | What it gives you |
| --- | --- | --- |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | A graph of application steps | A runtime for long-running stateful agent workflows, with persistence and human review points. |
| [OpenHands SDK](https://docs.openhands.dev/sdk/getting-started) | A framework for building software agents | Agents that interact with code, files, and system commands through tools and a workspace. |
| mishe-tauftauf | A resident culture planted in a Git worktree | Persistent text panes, role charters, a shared obligation log, checked handoffs, and a path from observed failures to reviewed, pushed repairs. |

These are different layers of a system. Mishe-tauftauf uses an installed coding agent as a mind and can incorporate other runtimes; its specific contribution is the ongoing local observation and repair loop around an owned codebase.

## Case study: a false CI unknown in this repository

On 29 September 2026, the live CI pane reported `UNKNOWN` for `origin/main` even though [run 36617398870](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36617398870) had succeeded for that exact commit. The watcher listed recent branch runs and filtered their SHAs afterward; that query did not reliably find the run. Witness kept the discrepancy visible as a genome task. Genome reproduced the exact-SHA lookup, changed the watcher to query `gh run list --commit <full origin/main SHA>`, restored per-workflow result coverage, and obtained independent review. The focused tests passed (3). It pushed [commit 71b9aca](https://github.com/genaforvena/mishe-tauftauf/commit/71b9aca89c7e21519eb4b278e6fa409b8be0f167); [run 36619558220](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36619558220) succeeded for that new SHA, and the live pane returned to `PASS`.

The observable improvement is precise: CI status is now queried for the remote commit being tended, and a GitHub query failure still remains `UNKNOWN`. The task stayed open through the code change, review, push, and replacement run; its completion was recorded in `chat.log` only after those checks.

For a separate application with a running plant, see the public [check ledger example](https://github.com/genaforvena/mishe-tauftauf-example), its [commit history](https://github.com/genaforvena/mishe-tauftauf-example/commits/main), and its [Actions runs](https://github.com/genaforvena/mishe-tauftauf-example/actions). That repository records code and CI; the plant's chat, panes, and host readings remain in its ignored local site.

## Plant a local instance

The plant target is Linux with Python 3, tmux, an installed agent CLI, Git, GitHub CLI for CI readings, and a user systemd manager. From this checkout:

```bash
python3 scripts/plant_local.py --engine-command 'codex'
tmux attach -t mishe-seed
```

To plant the same kernel onto another owned Git worktree:

```bash
python3 scripts/plant_local.py --workspace /path/to/project --engine-command 'codex'
tmux attach -t mishe-project
```

The script creates an ignored `.mishe-seed/` site here or `.mishe-tauftauf/` in the target, initializes five resident windows and the permissions panel, adds the operator shell, runs initial discovery and CI readings, and enables user services for those channels and the CI watcher. `--engine-command 'omp --model ...'` can launch an installed OMP mind instead. `--no-services` starts a manually supervised trial. A repeat plant refreshes unchanged default instructions and resident services while preserving customized local additions, launchers, panes, and handoffs.
The generated services use this kernel checkout's Python source, so keep it available on the host.

Inspect the running plant rather than inferring success from the install command:

```bash
tmux list-windows -t mishe-seed
tmux capture-pane -p -t mishe-seed:health.0
tmux capture-pane -p -t mishe-seed:genome.0
.mishe-seed/bin/mishe-tauftauf --home .mishe-seed seed status --slug genome
.mishe-seed/bin/mishe-tauftauf --home .mishe-seed discover show
systemctl --user status mishe-seed-genome.service mishe-seed-health.service
```

The CI watcher matches Actions runs to the GitHub default branch's remote SHA, records each transition as `[ci]` in `chat.log`, and opens a genome task on failure. No run for the current SHA, an unavailable GitHub CLI, or a stale reading is `UNKNOWN`, never a pass inferred from an older commit. Genome keeps the task open through the repair commit, push, and successful replacement run. Each target can add its own CI or deployment checks to its local top panes.

The generated checks are a starting point. Add the target system's real build, test, service, and data checks to its top-pane programs as the plant learns that system. Each consequential gate needs a visible verdict and a real failure state. The local [plant-mishe skill](.agents/skills/plant-mishe/SKILL.md) contains the setup and live verification workflow.

## What stays in Git

Tracked source holds reusable code, tests, setup, general instructions, and the default culture. The planted site's `chat.log`, customized charters, handoffs, artifacts, drafts, checks, service units, and permission ledger live under a gitignored site directory such as `.mishe-seed/` or this checkout's `.mishe-tauftauf/`. A fresh clone starts without another node's running state. A lesson becomes shared behavior only when reviewed and deliberately promoted into tracked source. Genome inspects the exact diff, excludes the site directory, commits accounted-for paths, pushes to this repository's configured origin, and records the SHA and push result; the task remains open until that delivery is complete.

The local agent contract is [AGENTS.md](AGENTS.md). The plant's durable rules are [seed_doctrine.md](src/mishe_tauftauf/seed_doctrine.md), loaded from tracked source at each restore and mirrored into `SITE/doctrine.md` on planting. The [mesh culture mapping](instructions/mesh-culture.md) names the source rules this narrower seed carries. This seed does not claim the fleet authority or native lifecycle accounting of the larger mesh.

## A working plant

A fresh instance works when its top panes refresh with truthful results, its resident services and lower panes remain live, an addressed task or observed fault wakes the right mind, that mind leaves a checked artifact and `[work]` receipt, and a clear restores the handoff without losing the next step. A successful unit test checks the protocol; the live pane, caller, and tape check the actual wiring. Run this checkout's suite with `.venv/bin/pytest -q` when its virtual environment is present.
