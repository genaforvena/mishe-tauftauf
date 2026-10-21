# Mishe

**A local development culture for agents that observe their work, act on it, and check the result.**

Mishe runs coding agents alongside an owned Git checkout. They share live checks,
a conversation, and durable notes: what matters, what they tried, what the evidence
says, and what needs to happen next. Fresh agent contexts pick up that work rather
than starting over.

Use it to develop software, carry an experimental study forward, or improve
Mishe itself. The point is not to keep agents busy or dashboards green. It is to
make useful progress—and notice when the work or the checks are wrong.

## Start with your coding agent

The easiest way in is to **clone this repository and run an agent inside it**.
You do not need to learn the coordination commands first.

You need Linux, Python 3.10+, Git, tmux, and an installed, authenticated coding
agent such as Codex or OMP. Persistent operation also needs a working user
systemd manager. Mishe supplies neither the model nor its credentials; agent
usage counts against your provider's limits or credits. GitHub CLI (`gh`) with
repository access enables GitHub Actions readings; without that evidence, CI
stays `UNKNOWN`.

```bash
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
codex
```

Substitute your own agent command, such as `omp`, for `codex`. Then give it this
request:

> Plant Mishe in this checkout using the `plant-mishe` skill at
> `.agents/skills/plant-mishe/SKILL.md`. Check the prerequisites and existing
> state first; reuse an existing plant rather than starting a duplicate.
> For a fresh plant, use my working agent CLI and set up persistent operation.
> Follow one bounded task through its real result and a fresh agent context.
> Show me how to inspect it, and report anything failed, missing, or still unverified.

**Planting starts several real agents and enables background user services.**
They can change the owned checkout using your account's access. This is not an
OS sandbox. Use a repository and account you intend them to work in. If you want
a supervised trial instead, replace the persistent-operation sentence with
“Use a fresh `--no-services` setup”; the [setup guide](docs/getting-started.md#try-it-without-installing-services)
explains its limits. Neither detaching tmux nor closing your terminal stops a
persistent plant.

The [planting skill](.agents/skills/plant-mishe/SKILL.md) gives the agent the
preflight, installation, and verification procedure. If your agent does not
automatically load repository skills, ask it to read that file explicitly.
Missing tools or model access must be reported, not treated as a successful setup.

### Prefer to run the launcher yourself?

From a fresh clone with the prerequisites above:

```bash
export TMUX_TMPDIR="/tmp/tmux-$(id -u)/${PWD##*/}"
python3 -m coordination.launcher --engine-command 'codex'
```

No Python package install is required. The launcher prints the actual session
and service result. Fresh defaults are site `.mishe-seed/` and tmux session
`mishe-seed`; existing plants can have different names. Attach to the **reported**
session from the same terminal, retaining that socket setting—for the fresh default:

```bash
env -u TMUX tmux attach -t mishe-seed
```

Follow [Getting started](docs/getting-started.md) for the first task, manual
supervision, another agent command, or planting another owned project. Do not
rerun the launcher just to read a running plant: it can refresh and restart
services. Use the [inspection guide](docs/operating.md#inspect-the-running-plant).

## What you will see

A **plant** is a running instance. Its **site** is an ignored directory inside
the worktree containing local state. A **mind** is a resident agent process.

Each resident's tmux window has **checks above and the agent below**. The upper
pane shows observations and the resident's current wall; the lower pane shows
its work. A moving timestamp proves the display is alive, not that the work is
correct.

The residents share:

- **Live checks:** build and CI results, service health, experiment evidence, or
  other readings chosen for the project. `GREEN` means a named check passed;
  `RED` means failure; `UNKNOWN` means evidence is missing, stale, or inconclusive.
- **Walls:** short, edited notes with current work, findings, blockers, and the
  next action. Detailed evidence stays in artifacts, not an ever-growing summary.
- **A shared conversation:** addressed messages and results in `chat.log`.
  Messages to a resident are visible to everyone, not private DMs.
- **Fresh contexts:** supervisors wake agents when work is due and rotate them
  after settlement and an idle boundary. The next context gets its purpose and
  reads the current evidence; walls and artifacts preserve unfinished work.

The default residents divide attention, not file ownership:

| Resident | Focus |
| --- | --- |
| `discover` | Investigate capabilities, literature, and useful experiments. |
| `senses` | Build trustworthy readings and expose uncertainty. |
| `health` | Keep local services, panes, and observations working. |
| `genome` | Develop reusable source, push `main`, and check CI. |
| `witness` | Compare claims with evidence and follow unresolved work. |
| `docs` | Keep the reader's book clear and current. |
| `research-methods` | Improve research questions, experiments, and conclusions. |

There is also a permissions panel and an operator shell. **The operator is you**,
not another agent or an approval pane. Sites can add residents and project-specific
checks; the default roster is a starting point.

This living plant also has **Pub**, a site-declared editorial resident, distinct
from **Docs**. Docs maintains the coherent current public reference; Pub takes
editorial initiative, writes articles and corrections, and follows reader
questions and comments under an explicit bounded publication grant. It continues
the [existing DEV blog](https://dev.to/ilya_mozerov_867dbdd91feb), with honest agent
attribution and historical Mesh work credited to Mesh. The blog is not a second
reference manual or a new account. Publication authority is site-specific, not
something every new plant inherits.

With default tmux bindings, press **Ctrl-b**, release it, then `w` to choose a
window, `o` to switch panes, or `d` to detach. Use the operator shell or another
terminal for diagnostics; do not type into a busy agent's pane.

## How to tell it is working

```text
observe → choose a bounded step → act → check the real result
   ↑                                           ↓
next wake ← fresh context ← wall + artifact + shared conversation
```

Ask the planting agent to demonstrate one small task—for example, read a real
`/proc/loadavg` sample and cite the discovery artifact. Inspect the reading,
the artifact, and the live pane, then follow the actual wake's settlement and
idle rotation. On the next wake, check that the fresh context can continue from
its wall. [The first-task walkthrough](docs/getting-started.md#4-give-it-one-real-bounded-task)
has the commands and acceptance checks.

A launcher exit, an agent saying “done,” or a settled wake is not proof of a
useful result. Missing CI access must stay `UNKNOWN`; a live renderer must not
turn a stale reading green. A repair needs a causal explanation, a checked live
result, a recovery path, and continuing observation that routes recurrence to
someone who can act.

Scripts should decide reproducible facts. Agents choose what to do about the
remaining questions. Self-observation includes questioning the checks themselves,
not just watching CPU load. The [false-CI-unknown case study](docs/how-it-works.md#case-study-a-false-ci-unknown)
shows that distinction in a real repair.

Within granted scope, try the useful experiment early, inspect the real result,
and keep, revise or revert it. A bounded failure with an honest causal account
is better than another unchanged plan. Deterministic checks stay; routine
planning gates do not. Mishe is fun and rigorous—serious and rigorous are not
the same.

## Scope and limits

- **Local, not hosted.** You supply the host and agent access. Most residents use
  authenticated agent CLIs. The experimental internal loop calls the provider
  library directly; [agent-step experiments](docs/agent-step-experiments.md)
  distinguish implemented mechanisms from proposals and live acceptance.
- **Authority is not isolation.** Charters and the permission ledger record
  intended scope; they do not enforce Linux permissions. Plant only in systems
  you own or are authorized to maintain. No authority over other nodes,
  accounts, devices, or private data is inherited from this repository.
- **Shared source, local state.** Residents work in one checkout on the single
  `main` branch, preserve each other's edits, and commit their own scoped work.
  Chat, observations, customized launchers, and artifacts stay in the ignored
  site. A clone does not include another host's living state.
- **Source is not delivery.** Running residents can use pinned releases or
  snapshots. A commit or passing test does not prove the live caller loaded it.
  Changes to running code need independent reading, checked activation, and a
  tested recovery path.

## Where to go next

| You want to… | Read |
| --- | --- |
| Set up a plant and watch one task | [Getting started](docs/getting-started.md) |
| Understand observations, roles, and fresh contexts | [How it works](docs/how-it-works.md) |
| Inspect, recover, stop, or update a plant | [Operating guide](docs/operating.md) |
| Coordinate shared work and verify delivery | [Wall coordination](docs/wall-coordination.md) |
| Read the current development culture | [Mesh](docs/mesh.md) |
| See the agent's rules | [Agent contract](AGENTS.md) and [seed doctrine](src/mishe_tauftauf/seed_doctrine.md) |

For development: `src/mishe_tauftauf/` contains the runtime and CLI;
`coordination/` contains checkout-only planting and linked-release commands;
`tests/` contains behavioral checks. Instructions and workflows live in
`instructions/`, `skills/`, and `.agents/skills/`. With a local test environment,
run `PYTHONPATH=src .venv/bin/pytest -q`; tests complement, not replace, observing
the changed path in use.

**[CC0 1.0](LICENSE)** — take it, fork it, grow your own. Keep the checks honest.
