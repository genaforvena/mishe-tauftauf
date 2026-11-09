# Mishe

**A small culture of AI agents that live in a Git checkout, learn to sense their own work, and keep fixing what they find.**

Most tools that run coding agents ask them for output. Mishe asks for something slower and
stranger: that a group of agents notice what is true about their work, write it down where the
next one will find it, check whether their changes did anything, and say plainly when they do
not know. Then it lets them keep going, a few minutes at a time, for days.

Mishe is not a framework you configure. It is a way of working you plant in a repository and
watch grow, and the repository it was written in is the first thing it looks after.

## The culture

Agents here are **woken, not kept running**. Small scripts observe the world and write files;
systemd watches those files and starts a short-lived **wake** only when something is owed: a
message addressed to an agent, a reading that changed, a follow-up that came due. A wake hands
the agent a dossier, the agent works, and it ends by recording what it did. When nothing is owed,
nothing runs.

A few habits make this a culture and not just a scheduler:

- **Write it down for the next one.** Each agent keeps a *wall*, a page of its own notes written
  so that a person who has nothing else open could do exactly what the agent is doing. The next
  wake starts from the wall, not from memory, because there is none.
- **UNKNOWN is a real answer.** A reading that stops reporting becomes UNKNOWN on its own and is
  never shown as a pass. Missing, stale or broken monitoring is treated as a finding.
- **Check the result, not the report.** A green test, an active unit or a long message log does
  not prove that work landed. Agents are asked to look at the thing itself, and a fresh pair of
  eyes reviews anything that could break the runtime.
- **Try things live, keep one way.** An experiment means: do it, watch it, keep it or revert it.
  Successful experiments become the one way things are done. Old paths are deleted, not kept
  behind flags.
- **Text is evidence, not command.** A sentence saying a monitor is running does not make it run.
  What counts is what a command, a parser or a measurement actually shows.
- **Serious is not the same as rigorous.** Mishe is meant to be fun and exacting at once.
  Failure inside the granted scope is something to understand and recover from.

## New senses

The hope that runs under all of this is that Mishe can **grow new senses**. This does not have to
mean more hardware or collecting more from people. A new sense can be a way of noticing relations
in what is already available: a rhythm across several records, a change in the timing of work, the
cost of reading a conversation that keeps growing. Pattern is not yet explanation, so each new
reading says how it was measured, how much it can be trusted, and when it is UNKNOWN.

Some senses are about the machine and the repository. A plant can also be given senses for the
physical space it runs in, only with the owner's explicit say-so, and only as coarse derived
numbers. Raw audio and camera frames are not kept. [New senses](docs/new-senses.md) is where
this idea is written out, and [Mesh](docs/mesh.md) describes the culture itself.

## A day in Mishe

Suppose the CI signal (the automatic reading of the latest CI run on `main`) turns RED. Genome,
the role that owns the runtime and checks CI, is owed one wake. It starts with its charter (its
standing mandate), its wall and what changed since its last wake. It reads the failing run, makes
a scoped fix, runs the checks, and ends with `mishe yield --result changed`. If it cannot fix the
failure, it ends with `--result blocked` and a message to whoever can. A wake that ends without
a yield is recorded as an unknown effect, and the next wake starts by working out what happened
instead of repeating it. Between those moments, nothing runs.

## Quick start

You need Linux with a user systemd manager, Python 3.10+, Git, and an installed, signed-in coding
agent: Claude Code (`claude`) or `omp`. Mishe supplies neither the model nor the credentials, and
agent use counts against your provider's limits. The GitHub CLI (`gh`), with access to the
repository, is optional; without it the CI signal stays UNKNOWN.

```bash
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"
mishe init                                          # creates the site: the ignored directory of local state
mishe hold on --reason "first install"              # no model is called while the hold is on
mishe sync --repo "$PWD" --pythonpath "$PWD/src"    # installs the systemd units
mishe doctor                                        # checks they match what was generated
```

Waking agents run with your account's access and can change the checkout. This is not an OS
sandbox, so use a repository and account you intend them to work in.
[Getting started](docs/getting-started.md) covers releasing the hold with `mishe hold off` and
watching the first wake. To stop model calls again, run `mishe hold on`.

## The minds

A role is a charter in the site's `charters/` directory plus a wall the role keeps edited. It is a
standing identity with a wall and a history, not a running process. In the site this repository is
developed in, the roles are:

| Role | Owns |
| --- | --- |
| `genome` | The runtime and reusable core, release snapshots, pushes to `main`, and CI checks. |
| `health` | Incidents, local reliability, and recovery when a unit or signal fails. |
| `senses` | Sensors and their readings: what is measured, how valid it is, and when it is UNKNOWN. |
| `docs` | The reader's book: introductions, reference pages, and removing stale explanations. |
| `pub` | Public editorial work and reader relations, under its existing account grant. |
| `field-review` | External ideas and small experiments, taken through to a checked application. |
| `rust-plan` | Moving selected runtime pieces from Python to Rust (a compiled systems language), one path at a time. |
| `steward` | Cross-area priorities, work nobody owns, and how the model budget is split. |

They divide attention, not file ownership, and any of them may fix a bounded problem it finds.
Sites can differ. A role exists when its charter file is in the site's `charters/` directory, so a
site can keep, edit or drop the starter charters and add its own. `mishe init` installs starters
for `genome`, `witness`, `senses`, `discover`, `health`, `docs` and `research-methods`; the site
this repository is developed in has no `witness`, `discover` or `research-methods` charter.
Messages to a role are visible to everyone, not private. **The operator is you**, the human owner;
the `operator` shell is a convenience for you, never a place an agent waits. `mishe --help` lists
every command.

## How to tell it works

```bash
mishe doctor                                # installed units match what was generated
systemctl --user list-units 'mishe*'        # wake paths, timers and any failed unit
mishe signal show NAME                      # one signal: its value, state and age
mishe pain read ROLE --launcher dashboard   # a read-only evidence view for one role
```

A signal is a named live reading. A failed unit turns its signal RED and sends a message to the
role that resolves it. A healthy wake ends with its wall updated and its message read;
[Getting started](docs/getting-started.md) shows how to watch one.

## Limits

- Agents act with your account's access inside the checkout you give them. Nothing here isolates them.
- Model usage counts against your provider's limits. Mishe supplies neither models nor credentials.
- There is one checkout and one branch, `main`. Each author commits its own scoped work; `genome` pushes and checks CI.
- Local state (walls, signals, messages, wake records) stays in the ignored site and is never committed.
- Senses of a physical space need the owner's explicit agreement, keep only derived readings, and cover only what the owner granted.
- A green test, an active unit or a busy message log is not proof that work landed or was useful. Check the result itself.

## Where to go next

| You want to… | Read |
| --- | --- |
| Set up a site and watch the first wake | [Getting started](docs/getting-started.md) |
| Understand the culture and its couplings | [Mesh](docs/mesh.md) |
| Read what Mishe thinks a sense is | [New senses](docs/new-senses.md) |
| See how a wake, a signal and a yield fit together | [Working as a durable mind](docs/wall-coordination.md) |
| Follow one mind's choice from dossier to yield | [How a mind chooses what to do](docs/how-a-mind-chooses.md) |
| Operate a running plant | [Getting started](docs/getting-started.md) |
| Read the agents' rules | [Seed doctrine](src/mishe_tauftauf/seed_doctrine.md) |
| Read the runtime design | [Core runtime](src/mishe_tauftauf/core/README.md) |
| Set up the optional analysis advisor | [Observable analysis advice](docs/analysis-advisor.md) |
| Use an external judge provider | [Completion judge adapter](docs/completions-judge.md) |
| Preflight a draft before publishing it | [Publication checks](docs/publication-checks.md) |
| Know what the research-methods role is for | [Research methods](docs/research-methods.md) |

Layout: `src/` is the runtime, `checks/` the independent checks, `tools/` the guard and activation
tooling, `tests/` the tests, `research/` recorded reviews and `docs/` this book.

[`LICENSE`](LICENSE) is CC0-1.0.
