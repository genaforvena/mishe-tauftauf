# mishe-tauftauf

**A small, plantable culture for development through self-observation.**

Mishe gives agents a way to observe the work they are doing, the evidence behind
their conclusions, and the machinery they depend on—then use those observations
to choose and check the next step. That work can be software development,
an experimental study, or improving Mishe itself.

Give it a Linux host and an installed coding agent. Refreshing checks, shared
text panes, a work log, and durable handoffs keep goals, results, and unknowns
visible across fresh agent contexts. The current implementation lives alongside
an owned Git worktree; Git stores reusable code and delivers reviewed changes.
**The worktree is the substrate, not the purpose.**

Think sourdough starter for a development process. Except this one is supposed
to tell you when it hasn't risen.

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
- **Progress beyond commits.** A project's checks can expose experiment results,
  data provenance, resource limits, and unfinished review—not just builds and CI.
  A successful command need not mean the underlying goal has been achieved.
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

Self-observation means more than watching the host. It includes asking whether
the work's evidence supports its claims, whether a task actually advanced, and
whether Mishe's own checks and coordination are telling the truth. An observed
gap can change the next experiment, the plan, or the machinery—not only the code.

### In practice: tiny-fleet's research loop

The running plant in [tiny-fleet](https://github.com/genaforvena/tiny-fleet) is
working toward reproducible research, not merely keeping a repository tidy.
Its project-specific panes expose experiment progress, provenance gaps, and
pending independent review. Agents inspect evaluation artifacts, reconcile
claims with the recorded experiments, and carry the remaining work through
handoffs.

In the live instance inspected on 30 September 2026, operational health checks
passed while research acceptance remained `UNKNOWN`: experimental outputs
existed, but provenance and review obligations were still open. The agents also
identified a stale sample in their own evidence display. This is the point of
the loop: **observe the work, question the observation, develop from what it
reveals.** It is not a claim that the study is complete.

The [concept guide](docs/how-it-works.md#development-is-not-just-repository-maintenance)
explains how those project-specific checks fit the generic runtime.

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

Optional adapters are shipped as package modules: `python -m mishe_tauftauf.jev_judge`
for the hosted Jev judge and `python -m mishe_tauftauf.omp_mind` for one-shot OMP
invocations. The latter requires `MISHE_TAUFTAUF_WORKSPACE`; hosted Jev requires
its TypeSafe credentials. The repository does not include a demo directory.

The optional [Chat Completions judge trial](docs/completions-judge.md) uses an
explicit provider and model; its read-only shadow runs do not activate a live
judge or dispatch witness work.

Task replay tolerates historical payload-free task-state notes, future state
fields and stale steps after closure, without admitting those records through
the current writer or hiding corrupt JSON. Health reports a missing or invalid
local service manifest as UNKNOWN rather than treating it as no services.

**[CC0 1.0](LICENSE)** — take it, fork it, grow your own. Keep the checks honest.

## Task continuity and productive waits

For long goals, create evidence-bound child work with `task add STEP --owner ROLE --parent GOAL --next-step TEXT --reason TEXT --evidence FILE`. Finish only the checked deliverable with `task finish STEP --owner ROLE --result TEXT --evidence FILE`; the goal remains open. `task reopen` explicitly recovers an incorrectly closed goal. Scheduling and the displayed task board share the same lifecycle.

A blocked final acceptance gate can coexist with productive work. Add an admissible child and use `task wait GOAL ... --producer ROLE --retry-event TOKEN --alternative STEP`. Review readiness can use `--retry-task STEP` to wake once the producer finishes, without repeated model polling. Keep registrations and resource limits intact; changed timestamps or evidence hashes alone do not establish progress. Structured task control must go through the task CLI.
