# mishe-tauftauf

A small, plantable seed of mesh that tends its own development. Its shared body is a tmux session and an append-only `chat.log`: current data appears above each resident mind, work is recorded as readable text, and a checked handoff survives every context clear. The code and instructions live together so another checkout can grow the same loop.

## Goal

Keep a living codebase able to observe its own condition, repair its own faults, pursue useful improvements, and reproduce that practice on another owned machine. The direction comes from mesh's self-organization: self-maintain, self-develop one checked step, generate ideas from real gaps, and turn wishes into owned work. A green check is a current observation, never a reason to stop learning.

This repository is the single-node kernel of that practice. It owns its local files, tmux session, `chat.log`, and user services. It carries no authority over `~/lte-workstation`, remote nodes, accounts, devices, or private data. The lightweight mishe culture demo is a separate skill; this repository plants a persistent development loop.

## The living layout

| Window | Upper pane: current text | Lower pane: acting channel |
| --- | --- | --- |
| `genome` | Repo health, worktree, goal, next improvement, and a fresh check report | Resident development mind |
| `witness` | `chat.log` view, open tasks, recent work receipts, channel health, and coordination goal | Resident coordination mind |
| `operator` on a fresh plant | One-pane shared shell | A human or agent joins here |

Each upper pane refreshes and carries a visible lease. The lease proves the renderer ran; `SYSTEM ZERO` and the source check determine whether the claim is true. RED, UNKNOWN, stale, and absent are distinct. A consequential gate belongs on a pane, so the minds and anyone attached can see the same result. The lower pane owns its shell and its work; another channel does not clear it or type into it.

`chat.log` is the conversation, task, and work tape. `[task] <id> owner=<role>` opens work, `[taking]` records start, and `[done]` or `[dropped]` records a checked result or reason. The witness keeps open IDs visible and routes code tasks to genome. Each settled wake writes a `[work]` receipt with its result type, observation, handoff hash, archived handoff path, and a bounded account of the action and check. Three no-change receipts on the same observation render a loop warning on the witness pane.

The doctrine is [seed_doctrine.md](src/mishe_tauftauf/seed_doctrine.md). [AGENTS.md](AGENTS.md) is the local agent contract. [Mesh culture mapping](instructions/mesh-culture.md) identifies the current mesh rules carried into this narrower plant. A charter gives each window its lasting responsibility; a handoff gives its current task state. A chat line alone does not become a durable rule.

## One turn and a long task

The supervisor observes the full live upper pane and new addressed tasks, appends an observation and exact wake to `chat.log`, and delivers one wake to an idle resident mind. The mind reads the current pane and source, reports the task start, makes one bounded change or check, verifies the same live surface, and writes a source-bound artifact and handoff. It settles that wake with `seed yield --result changed|verified|blocked`. The supervisor archives the handoff, logs the work, waits for the mind to be idle, clears its context, and restores doctrine, charter, and handoff. A clear alone is not new work.

Long work keeps one stable task ID through successive handoffs. `seed yield --continue` requests the next bounded step after the clear, even when the pane has not changed. A pending wake after a crash is held until its prior effect is reconciled; if the mind is visibly idle, the supervisor redelivers the exact unsettled wake with a reconciliation instruction. When no task is pending, a quiet pane gets a periodic self-pick so genome and witness can pursue their goals proactively.

Genome owns delivery as well as development. After checking and reviewing its change, it inspects the exact diff, commits only accounted-for paths, pushes to this repository's configured GitHub origin, and records the commit and push result in the shared tape. The task stays open across handoffs until that landing succeeds. Existing work from another channel is preserved.

## Plant this repository

On Linux, have Python 3, tmux, an agent CLI, and a user systemd manager available. From this checkout:

```bash
python3 scripts/plant_local.py --engine-command 'codex'
tmux attach -t mishe-seed
```

The script creates `.mishe-seed`, initializes `genome` and `witness`, starts their two-pane windows, adds an `operator` shell, and enables separate user services for the two resident channels. It preserves existing charters, launchers, panes, and handoffs on a repeat run. Use an installed OMP command with `--engine-command 'omp --model ...'` when that is the resident mind. `--no-services` makes a temporary, manually supervised trial. The repo-local [plant-mishe skill](.agents/skills/plant-mishe/SKILL.md) guides setup and verification.

The generated genome pane checks the plant's own `doctor`. Add the repository's real build, test, or service checks to `SITE/top-pains/genome` as the plant grows. A new check needs a real failure state and a live pane observation; a self-test alone is insufficient. The witness pane follows the site's `chat.log` and open tasks. Its source view omits its own supervisor bookkeeping so recording a wake cannot wake itself forever; the full tape remains in `SITE/chat.log`.

Inspect a fresh plant with:

```bash
tmux list-windows -t mishe-seed
tmux capture-pane -p -t mishe-seed:genome.0
tmux capture-pane -p -t mishe-seed:witness.0
.mishe-seed/bin/mishe-tauftauf --home .mishe-seed seed status --slug genome
systemctl --user status mishe-seed-genome.service mishe-seed-witness.service
```

The current development checkout uses the owned session `mishe-self-development-current` with windows `codex`, `genome`, and `witness`; its enabled services are `mishe-self-development-current.service` and `mishe-witness-current.service`. Its plans, checks, drafts, chat, handoffs, artifacts, and service files stay in the gitignored `.mishe-tauftauf/` site. Reusable behavior goes into tracked `src/`, `scripts/`, and instructions. A fresh clone starts with no local plant state.

## What proves it works

A plant is working when both services stay active, the panes advance with truthful check reports, an addressed task or observed fault wakes the right mind, the mind leaves an actual artifact and `[work]` receipt, and an idle clear restores its handoff without losing the next step. The test suite exercises the protocol with a fake resident terminal; the current checkout also runs OMP in the live lower panes. `python3 -m pytest -q` checks the repository code. The live panes and `chat.log` prove the running wiring.
