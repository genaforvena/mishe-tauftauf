# mishe-tauftauf

**A small culture of coding agents that lives in a terminal and tends a Git worktree.**

Give it a Linux host and an installed coding agent. Mishe adds refreshing checks,
resident tmux windows, a shared work log, and handoffs that survive an agent's
context being cleared. The agents can inspect faults, make bounded repairs, and
carry reusable changes through review, commit, push, and CI.

Think sourdough starter for a codebase. Except this one is supposed to tell you
when it hasn't risen.

## What you actually get

Attach to the tmux session and you see the same evidence the agents see:

- **Checks above, agent below.** Each resident window has a refreshing status pane
  and a coding agent's working pane. A moving timestamp means the display is alive,
  not that the system is healthy.
- **One shared text log.** `chat.log` records who took a task, what they checked,
  what remains, and where the evidence lives.
- **Work that survives a fresh context.** A supervisor archives a handoff, replaces
  a settled agent process when it is idle, and restores its purpose and unfinished
  work on the next wake.
- **A loop that can repair itself.** Agents may improve Mishe's own code, checks,
  or instructions within their owned scope. Shared source still needs verification
  and independent review before landing.

Mishe supplies the coordination and observation loop, **not the language model**.
You bring the agent CLI and its authentication. It is a local development practice,
not a hosted service, a sandbox, or a promise that every repair will be correct.

## Start here

| You want to… | Read |
| --- | --- |
| Plant it and watch one real task | [Getting started](docs/getting-started.md) |
| Understand the roles, checks, and handoffs | [How it works](docs/how-it-works.md) |
| Inspect, recover, or update a running plant | [Operating guide](docs/operating.md) |
| Read the rules an agent must follow | [Agent contract](AGENTS.md) and [seed doctrine](src/mishe_tauftauf/seed_doctrine.md) |

A **plant** is one running instance. Its **site** is the local directory holding
its log, checks, charters, and evidence. A **mind** is the coding agent in a lower
pane. No botany qualification required.

## Plant your first instance

You need **Linux, Python 3.10+, Git, tmux, an installed and authenticated coding
agent, and a working user systemd manager** for the persistent setup. GitHub CLI
(`gh`) with access to the repository is needed for GitHub Actions readings; missing
CI access is reported as `UNKNOWN`, not success.

From a fresh clone:

```bash
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
python3 -m coordination.launcher --engine-command codex
tmux attach -t mishe-seed
```

**This starts real agents and enables background user services.** Agent work may
consume provider credits and change the owned worktree. The site is ignored by
Git; the code is not. Read the [setup guide](docs/getting-started.md) first if you
want a manually supervised trial or to plant another project. Planting another
worktree also adds or refreshes a marked agent-contract block in its `AGENTS.md`.

The names above are fresh-clone defaults. If the checkout already has a plant,
the launcher reuses it: use the site and session printed by the command rather
than assuming `mishe-seed`.

A successful launch is only the beginning of the check. The setup guide walks
through a real task, its live pane, its artifact, and its completion record.

## How the loop works

```text
observe → choose one bounded step → act → check the live result
   ↑                                           ↓
next wake ← fresh context ← handoff + artifact + shared log
```

Scripts decide reproducible facts; agents choose what to do about them. Checks
report healthy (`GREEN`), failed (`RED`), or missing/stale/conflicting evidence
(`UNKNOWN`). **Unknown is not a reassuring shade of green.**

Five resident roles share the work:

| Role | Responsibility |
| --- | --- |
| `discover` | Find useful local observations and check whether they are available. |
| `senses` | Turn those observations into reliable readings with honest failure states. |
| `health` | Keep the local feed, panes, and services working. |
| `genome` | Improve reusable source and land verified, independently reviewed changes. |
| `witness` | Follow open work and compare completion claims with evidence. |

There is also a permissions panel and an operator shell. Roles identify
responsibilities, not walls around files: any mind can pursue an owned repair,
while genome handles shared-source landing. The [concept guide](docs/how-it-works.md)
explains the task protocol and what happens between wakes.

## Scope, not a force field

Plant only in systems and worktrees you own or are authorized to maintain.
Charters define intended authority; they do not enforce an operating-system
sandbox. The permission ledger records scoped operator decisions—it does not
grant Linux permissions or unlock a device.

This seed borrows culture from [lte-workstation](https://github.com/genaforvena/lte-workstation),
but **does not inherit authority over that mesh, remote nodes, accounts, or devices**.
It starts with bounded local readings, not someone else's sensors. It needs your
project's real build, test, service, and data checks to know what healthy means
for that project.

## Evidence, not just atmosphere

The public [example repository](https://github.com/genaforvena/mishe-tauftauf-example)
shows a plant tending a small application; its
[commits](https://github.com/genaforvena/mishe-tauftauf-example/commits/main) and
[Actions runs](https://github.com/genaforvena/mishe-tauftauf-example/actions) are
public. Host readings, conversation, and handoffs stay in the ignored local site.

For a concrete repair in this repository, see the
[false-CI-unknown case study](docs/how-it-works.md#case-study-a-false-ci-unknown).
It follows a bad reading through reproduction, review, push, and replacement CI.
An agent saying “fixed” is a claim. The check is the receipt.

## Find your way around the checkout

| Path | What belongs there |
| --- | --- |
| `src/mishe_tauftauf/` | Installable runtime and CLI; reusable production code. |
| `coordination/` | Checkout-only planting and linked-site release commands. |
| `examples/` | Optional mind/judge adapters and a runnable demo. |
| `tests/` | Automated behavioral checks. |
| `docs/` | Reader guides: setup, mental model, and operations. |
| `instructions/`, `skills/`, `.agents/skills/` | Mind instructions and agent/operator workflows. |
| `.mishe-seed/` or `.mishe-tauftauf/` | Ignored local state: logs, customized charters, handoffs, checks, and artifacts. |

A fresh clone gets the reusable code and default rules, not another host's living
state. A local lesson becomes shared behavior only when deliberately promoted
into reviewed source. The [culture mapping](instructions/mesh-culture.md) records
which mesh rules this smaller seed actually carries.

For checkout development, with the local test environment available:

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

Tests check behavior in isolation. The running caller, live pane, and shared log
check whether it is actually wired. Both matter.

**[CC0 1.0](LICENSE)** — take it, fork it, grow your own. Keep the checks honest.
