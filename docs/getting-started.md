# Getting started

[README](../README.md) · [How it works](how-it-works.md) · [Operating](operating.md) · [Wall coordination](wall-coordination.md)

This sets up one plant in a checkout, **held**, and then lets it run. Nothing calls
a model until you release the hold.

## 1. Prerequisites

- Linux with a working user systemd manager (`systemctl --user status`), Python 3.10+
  and Git.
- At least one authenticated coding agent: **`claude`** (Claude Code) and/or **`omp`**.
  Mishe supplies neither the model nor credentials; usage counts against your plan.
- Optional: GitHub CLI `gh` with repository access, for the CI reading. Without it
  the CI signal stays UNKNOWN.

Waking agents run with your account's access and can change the checkout. This is
not an OS sandbox; use a repository and account you intend them to work in.

## 2. Install and create the site

```sh
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"   # no install, no third-party packages
./bin/mishe init                            # creates the ignored site and default charters
```

The site holds everything local (walls, signals, chat, wake files) and is never
committed. The default roles are `genome`, `witness`, `senses`, `discover`, `health`,
`docs` and `research-methods`; each is a charter in `SITE_HOME/charters/`.

## 3. Choose engines

By default a role wakes on `claude` with `claude-haiku-5-5`. To change one, write
`SITE_HOME/engines/ROLE.json` (see [Operating](operating.md#changing-the-plant)).

## 4. Install the triggers — held

```sh
mishe hold on --reason "first install"
mishe sync --repo "$PWD" --pythonpath "$PWD/src"
mishe doctor                # exits 0 when units match what was generated
```

`sync` writes the user units (`mishe-wake@.service`, a path unit per role, producer
timers, `mishe-failed@`, the doctor) and enables the paths and timers.
`systemctl --user list-units 'mishe*'` shows them.

## 5. Start

```sh
mishe hold off              # releases the hold and offers the first owed role
```

Owed means something happened that the role has not seen: a message, a signal that
changed, a follow-up that came due. On a fresh site nothing is owed, so nothing runs
until you create work, for example:

```sh
mishe wall dm --source operator --to genome --text "Please read the README and tell me what is unclear."
```

That touches genome's inbox; its path unit starts one wake. Watch it:

```sh
journalctl --user -u mishe-wake@genome.service -f
ls SITE_HOME/wakes/ ; cat SITE_HOME/dispatch/genome.json
```

A healthy wake ends with the dispatch `completed`, the wall updated and the message
read. Cost and turns are in the wake's `*.out.json` (claude).

## Writing a wall, an outcome, a message

A woken mind does these itself, and ends every wake with `mishe yield --result changed|verified|blocked`
(its updated wall on stdin). By hand:

```sh
mishe wall write --owner ROLE --file NOTES        # replaces the role's wall (size-limited)
mishe wall dm --source ROLE --to ROLE2 --text "..." # addressed message on the shared tape
mishe wall outcome --owner ROLE --kind accepted --file NOTES --evidence PATH
```

See [Wall coordination](wall-coordination.md) for what counts as evidence for an outcome.

## Stop

```sh
mishe hold on                         # no more model calls; units stay installed
mishe sync --uninstall                # remove the generated units
```
