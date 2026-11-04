---
name: plant-mishe
description: Use when someone clones Mishe and asks an agent to set it up, or when planting or repairing Mishe in this checkout or another owned Git worktree.
---

# Plant Mishe from a checkout

Turn "I cloned Mishe; set it up" into a checked first wake, not just a successful install.
The human should not need to learn the internal commands first. Use this workflow for the
observation-driven runtime (signals, short wakes, systemd triggers); do not substitute a
separate one-shot loop or demo.

## 1. Check what exists before changing anything

Resolve the intended site from local evidence or the person, never from a remembered
path. Then read, without changing:

```sh
export MISHE_SEED_HOME="$PWD/.mishe-tauftauf"
./bin/mishe doctor
systemctl --user list-units 'mishe*'
```

Reuse an existing plant rather than starting a duplicate. If units from an older plant
exist, say so and stop; replacing them is the owner's decision.

## 2. Prerequisites, stated honestly

- Linux with a working user systemd manager (`systemctl --user status`), Python 3.10+, Git.
- At least one authenticated coding agent: `claude` and/or `omp`. Mishe supplies neither
  the model nor credentials, and agent usage counts against the person's limits.
- Optional: `gh` with repository access for the CI reading; without it CI stays UNKNOWN.

Missing tools or model access are reported, not treated as a successful setup. Explain
before installing that waking agents run with the account's access and can change this
checkout: this is not an OS sandbox.

## 3. Install, held

```sh
./bin/mishe init
./bin/mishe hold on --reason "first install"
./bin/mishe sync --repo "$PWD" --pythonpath "$PWD/src"
./bin/mishe doctor
```

Nothing calls a model while the hold is on. `doctor` must exit 0; if it does not, read
what it says and repair that rather than continuing.

## 4. First wake

Create one bounded, real piece of work, release the hold, and follow it through:

```sh
./bin/mishe wall dm --source operator --to genome --text "TASK in the person's words"
./bin/mishe hold off
journalctl --user -u mishe-wake@genome.service -f
```

Check, with evidence: the dispatch is `completed`, the wall changed, the message was
answered, and the wake's `*.out.json` records turns and cost. A wake that ended
`unknown` is not a pass; read `SITE_HOME/wakes/` and say what happened.

## 5. Hand back

Show the person how to inspect it (`mishe doctor`, `systemctl --user list-units 'mishe*'`,
`mishe signal show NAME`), how to pause it (`mishe hold on`) and how to remove it
(`mishe sync --uninstall`). Report anything failed, missing or still unverified,
including that the host guard may need its own configuration for a plant with no
tmux session.
