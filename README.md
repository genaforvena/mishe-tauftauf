# Mishe

**A local development culture for agents that observe their work, act on it, and check the result.**

Mishe runs coding agents alongside an owned Git checkout. They share live checks, a
conversation, and durable notes: what matters, what they tried, what the evidence says,
and what needs to happen next. A fresh agent context picks that work up rather than
starting over.

Use it to develop software, carry an experimental study forward, or improve Mishe itself.
The point is not to keep agents busy or dashboards green. It is to make useful progress,
and to notice when the work or the checks are wrong.

## How it runs

Agents here are **woken, not kept running**. Small scripts observe the world and write
files; systemd watches those files and starts a short-lived **wake** only when something
is owed: an addressed message, a signal that changed, a follow-up that came due. A wake
hands the agent a dossier (its charter, its wall, what changed), the agent works, and it
ends by calling `mishe yield`. When nothing is owed, nothing runs. A wake that ends without
a yield is recorded as an unknown effect, and the next wake of that agent starts by
reconciling it; nothing is ever replayed blindly.
The design is in `src/mishe_tauftauf/core/README.md`.

## Start with your coding agent

You need Linux with a user systemd manager, Python 3.10+, Git, and an installed,
authenticated coding agent: **Claude Code (`claude`)** and/or **`omp`**. Mishe supplies
neither the model nor credentials, and agent usage counts against your provider's limits.
GitHub CLI (`gh`) with repository access enables the CI reading; without it CI stays `UNKNOWN`.

```bash
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"   # no install, no third-party packages
./bin/mishe init
mishe hold on --reason "first install"
mishe sync --repo "$PWD" --pythonpath "$PWD/src"
mishe doctor
```

Nothing calls a model while the hold is on. **Waking agents run with your account's access
and can change the checkout; this is not an OS sandbox.** Use a repository and account you
intend them to work in. [Getting started](docs/getting-started.md) walks through releasing
the hold and watching the first wake.

## The agents

Each agent is a charter in the ignored site directory and a wall it keeps edited:

| Agent | Focus |
| --- | --- |
| `discover` | Investigate capabilities, literature, and useful experiments. |
| `senses` | Build trustworthy readings and expose uncertainty. |
| `health` | Keep local units, signals, and observations working. |
| `genome` | Develop reusable source, push `main`, and check CI. |
| `witness` | Compare claims with evidence and follow unresolved work. |
| `docs` | Keep the reader's book clear and current. |
| `research-methods` | Improve research questions, experiments, and conclusions. |

They divide attention, not file ownership. Sites can declare more. Messages to an agent are
visible to everyone, not private. **The operator is you**, the human owner; the `operator`
shell is a convenience for you, never a place an agent waits.

## How to tell it is working

```bash
mishe doctor                                # installed units match what was generated
systemctl --user list-units 'mishe*'        # wake paths, producer timers, any failure
mishe signal show NAME                      # one signal: value, state, age
mishe pain read ROLE --launcher dashboard   # a read-only evidence view for one agent
```

A signal that has stopped reporting becomes `UNKNOWN` on its own; missing, stale or broken
monitoring is never reported as healthy. A failed unit raises a RED signal and a message to the
agent that resolves it, and returns until someone repairs and clears it.
`mishe --help` lists every command.

## Scope and limits

- Agents work inside the owned checkout and site only; no other node, account or device
  authority is implied. A sibling plant on the same host is a different site.
- Local state (walls, signals, chat, wake files) lives in the ignored site directory and is never committed.
- There is one Git checkout and one branch, `main`. Authors commit their scoped work;
  `genome` pushes and checks CI.
- A green test or an active unit is not proof of live delivery or of useful work.

`src/` is the runtime, `checks/` the independent checks, `tools/` guard and activation tooling,
`tests/` the tests, `research/` recorded reviews, `docs/` the book.

## Where to go next

| You want to… | Read |
| --- | --- |
| Set up a plant and watch the first wake | [Getting started](docs/getting-started.md) |
| Read the current development culture | [Mesh](docs/mesh.md) |
| Set up the optional analysis advisor | [Observable analysis advice](docs/analysis-advisor.md) |
| Use an external judge provider | [Completion judge adapter](docs/completions-judge.md) |
| Preflight a draft before publishing it | [Publication checks](docs/publication-checks.md) |
| Know what the research-methods agent is for | [Research methods](docs/research-methods.md) |
| Read what Mishe thinks a sense is | [New senses](docs/new-senses.md) |
| Read the agents' rules | [Seed doctrine](src/mishe_tauftauf/seed_doctrine.md) |

[`LICENSE`](LICENSE) is CC0-1.0.
