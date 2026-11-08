# Mishe

Mishe runs several AI coding agents on one Git checkout on your own machine. They do not run continuously: each one wakes only when a signal changes or a message is addressed to it, does checked work on the code, and ends by recording what it changed.

## A day in Mishe

Suppose the CI signal (the automatic reading of the latest CI run on `main`) turns RED. Genome, the role that owns the runtime and checks CI, is owed one wake. It starts with its charter (its standing mandate), its wall (its own edited notes) and what changed since its last wake. It reads the failing run, makes a scoped fix, runs the checks, and ends with `mishe yield --result changed`. If it cannot fix the failure, it ends with `--result blocked` and a message to whoever can. Between those moments, nothing runs.

## Quick start

You need Linux with a user systemd manager, Python 3.10+, Git, and an installed, signed-in coding agent: Claude Code (`claude`) or `omp`. Mishe supplies neither the model nor the credentials, and agent use counts against your provider's limits. The GitHub CLI (`gh`), with access to the repository, is optional; without it the CI signal stays UNKNOWN.

```bash
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"
mishe init                                          # creates the site: the ignored directory of local state
mishe hold on --reason "first install"              # no model is called while the hold is on
mishe sync --repo "$PWD" --pythonpath "$PWD/src"    # installs the systemd units
mishe doctor                                        # checks they match what was generated
```

Waking agents run with your account's access and can change the checkout. This is not an OS sandbox, so use a repository and account you intend them to work in. [Getting started](docs/getting-started.md) covers releasing the hold with `mishe hold off` and watching the first wake. To stop model calls again, run `mishe hold on`.

## The roles

A role is a charter in the site's `charters/` directory plus a wall the role keeps edited. In the site this repository is developed in, the roles are:

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

Sites can differ. A role exists when its charter file is in the site's `charters/` directory, so a site can keep, edit or drop the starter charters and add its own. `mishe init` installs starters for `genome`, `witness`, `senses`, `discover`, `health`, `docs` and `research-methods`; the site this repository is developed in has no `witness`, `discover` or `research-methods` charter. `mishe --help` lists every command.

## How to tell it works

```bash
mishe doctor                                # installed units match what was generated
systemctl --user list-units 'mishe*'        # wake paths, timers and any failed unit
mishe signal show NAME                      # one signal: its value, state and age
mishe pain read ROLE --launcher dashboard   # a read-only evidence view for one role
```

A signal is a named live reading. A signal that stops reporting becomes UNKNOWN on its own, and UNKNOWN is never shown as a pass. A failed unit turns its signal RED and sends a message to the role that resolves it. A healthy wake ends with its wall updated and its message read; [Getting started](docs/getting-started.md) shows how to watch one.

## Limits

- Agents act with your account's access inside the checkout you give them. Nothing here isolates them.
- Model usage counts against your provider's limits. Mishe supplies neither models nor credentials.
- There is one checkout and one branch, `main`. Each author commits its own scoped work; `genome` pushes and checks CI.
- Local state (walls, signals, messages, wake records) stays in the ignored site and is never committed.
- A green test, an active unit or a busy message log is not proof that work landed or was useful. Check the result itself.

## Where to go next

| You want to… | Read |
| --- | --- |
| Set up a site and watch the first wake | [Getting started](docs/getting-started.md) |
| Read the current development culture | [Mesh](docs/mesh.md) |
| Set up the optional analysis advisor | [Observable analysis advice](docs/analysis-advisor.md) |
| Use an external judge provider | [Completion judge adapter](docs/completions-judge.md) |
| Preflight a draft before publishing it | [Publication checks](docs/publication-checks.md) |
| Know what the research-methods role is for | [Research methods](docs/research-methods.md) |
| Read what Mishe thinks a sense is | [New senses](docs/new-senses.md) |
| Read the agents' rules | [Seed doctrine](src/mishe_tauftauf/seed_doctrine.md) |
| Read the runtime design | [Core runtime](src/mishe_tauftauf/core/README.md) |

Layout: `src/` is the runtime, `checks/` the independent checks, `tools/` the guard and activation tooling, `tests/` the tests, `research/` recorded reviews and `docs/` this book.

[`LICENSE`](LICENSE) is CC0-1.0.
