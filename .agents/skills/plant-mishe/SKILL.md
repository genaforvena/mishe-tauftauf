---
name: plant-mishe
description: Use when someone clones Mishe and asks an agent to set it up, or when planting or repairing resident Mishe in this checkout or another owned Git worktree.
---

# Plant Mishe from a checkout

Turn “I cloned Mishe; set it up” into a checked first task, not just a successful
launcher exit. The human should not need to learn the internal commands first.
Use this workflow for the resident tmux culture; do not substitute a separate
one-shot loop or demo.

## 1. Establish scope and inspect before changing anything

Read the checkout's `README.md`,
`src/mishe_tauftauf/seed_doctrine.md`, and the relevant sections of
`docs/getting-started.md`. Follow installed local instructions, including command
wrappers. Run host commands from this core checkout: `coordination/` loads its
`src/` without a package install.

A clone does not contain an `AGENTS.md`: the plant contract is generated into
the *target* worktree when you plant it, and this repository's own contract
lives in `src/mishe_tauftauf/seed_agent_contract.md`. Read the target's
`AGENTS.md` only after planting it, when it exists.

For “plant here,” the target is this checkout. An external target must be an
explicitly authorized, separate Git worktree. Confirm its root with Git; the
site must be directly inside that root, not the root itself. Use a path without
whitespace. Inspect existing tracked and untracked changes and preserve them.

Before choosing new names, inspect recognized site directories `.mishe-seed/`
and `.mishe-tauftauf/`, any explicitly named site, its `.seed-raised` marker,
tmux `@mishe-tauftauf-home` ownership, and user services' `FragmentPath` values.
For site discovery and inspection, read the site's own
`operator/brief.md` and `operator/commands.md` once the plant exists, and use
`mishe pain read ROLE --launcher dashboard` for the live pane. If multiple
resident sites exist, resolve the intended site from local evidence or the
human's scope; do not guess or adopt an unrelated session or unit.

**If a plant already exists, inspect and tend it rather than planting again.**
Read its current operator dashboard and installed brief/commands. A request to
improve docs or inspect status is not a reason to rerun the launcher: repeating
it can refresh units and restart active services. Preserve custom charters,
`minds/ROLE` launchers, handoffs, top programs, active work, and unrelated services.
For a repair, retain the original failure, diagnose its cause and limits, and
use the narrow recovery procedure before considering a replant.

## 2. Check the host and the actual agent

Use the setup guide's read-only prerequisite checks:

- Linux, Python 3.10+, Git, and tmux. There are no required third-party Python
  dependencies for planting.
- An installed agent CLI with usable authentication and model access. Prefer
  the human's working command; inspect its actual supported invocation rather
  than guessing flags. Command existence alone does not prove provider access.
  Do not print credentials or change account/security settings to make setup pass.
- A working user systemd manager for persistent operation.
- `gh` access and a matching Actions run for CI evidence. Missing access or a
  missing run is `UNKNOWN`, not a reason to invent a green CI result.

Explain before launch: the default starts seven resident agents, creates tmux
panes and ignored local state, and enables background user services. Agents can
consume provider usage and modify the owned worktree with the account's access;
charters are not an OS sandbox. Honor the requested setup mode. Ask only for
missing authority or choices that local evidence cannot resolve; do not invent
a cost budget or silently install host packages.

A fresh `--no-services` setup is a supervised trial, not an inert preview. It
still starts real agents and panes. It has no continuous wake/clear supervisors,
CI watcher, or permission follower, and deliberately reports the absent service
manifest as RED. It does not disable an older persistent plant's services.

## 3. Plant once, using the chosen command

Select and record the site's tmux socket environment before launching. From the
core checkout root, a fresh default with a working Codex command uses:

```bash
export TMUX_TMPDIR="/tmp/tmux-$(id -u)/${PWD##*/}"
python3 -m coordination.launcher --engine-command 'codex'
```

Generated persistent services use `/tmp/tmux-UID/WORKTREE_BASENAME`; for an
external target, derive it from that target, not the core directory. For a fresh
persistent plant, set that exact directory rather than retaining an arbitrary
inherited `TMUX_TMPDIR`, which would split initial panes from their supervisors.
For an existing plant, inspect the actual installed environment before any
repair. Use the same environment for raw tmux inspection and attachment, with
`env -u TMUX` to avoid an enclosing session's socket. Hand back a working,
socket-qualified attach command for a new terminal, not just a session name.

Substitute the checked agent command, for example `--engine-command 'omp'`.
Append `--no-services` only for the requested fresh manual trial. A fresh core
uses `.mishe-seed/` and session `mishe-seed`; record the actual output rather
than assuming those names. Pass explicit `--home` and `--session` when the
existing site or chosen names require them. The site's short wrapper is
`SITE/bin/mishe`; the historical long-named `mishe-tauftauf` entry point also
works. Operator shells need explicit `--home SITE` in either form.

The launcher creates `genome`, `witness`, `discover`, `senses`, `health`, `docs`,
`research-methods`, `permissions`, and an operator window, takes discovery and
CI readings, and installs services unless omitted. Inspect any partial failure
before retrying; a failed launch may already have created state or started work.

Codex, OMP, and OpenCode have built-in idle-prompt recognition. For another CLI,
install an executable `SITE/checks/mind-ready/ROLE` for every supervised role.
It receives `MISHE_SEED_SESSION`, `MISHE_SEED_ROLE`, and `MISHE_SEED_PANE`, and
must exit zero only when the lower pane accepts a complete prompt. Missing,
failing, non-executable, or timed-out probes hold delivery. Never use an
always-successful probe. Verify readiness against the actual launched agent;
PID existence and a screenshot of a prompt are not a completed model task.

### Another owned project

Only when that target is requested, run from the core checkout:

```bash
python3 -m coordination.launcher --workspace /absolute/path/to/project --engine-command 'codex'
```

Plant the core persistently first if automatic linked updates are wanted. The
core gets the coordination service; a persistent external plant registers in
the core site's `health/linked-sites.json` and does not get its own coordinator.
Verify the core unit and the registry's exact target site, session, and applied SHA.
Updates require clean core and target worktrees and fresh passing CI for the
exact local core `HEAD`. The coordinator checks feed, panes, and services and
records a result or retryable hold in core `chat.log`.
`python3 -m coordination.site_sync --home CORE_SITE` requests an immediate
retry; it is an update action, not a read-only status check.

An external plant adds or refreshes a marked block in the target's `AGENTS.md`.
Preserve surrounding instructions and route that tracked diff to target genome
for review and landing. Never copy core application code into the target.

## 4. Prove one real task and continuity

Follow the setup guide's first-task walkthrough, using actual `SITE` and
`SESSION` values. Prefer a bounded local task such as reading `/proc/loadavg`
and citing its discovery artifact; do not invent an unrelated project goal.

1. Inspect every required live upper and lower pane, window manifest, service
   state, discovery sample, and CI reading. Check that the top-pane lease advances;
   it proves presentation liveness only. Missing or stale evidence stays UNKNOWN
   or RED. In manual mode, report the expected absent-service RED explicitly.
2. Address the task through the canonical CLI and observe readiness, delivery,
   the actual artifact, and the checked finding on the resident's wall and tape.
   A manual trial needs the guide's foreground `seed run` for the selected role;
   never add a second supervisor beside that role's existing service.
3. Follow the exact wake's settlement, stable idle clear, and process rotation.
   Then send a second bounded request through the CLI, if no real next wake has
   arrived, and verify that the new context reads current evidence and continues
   from its wall. A saved handoff is not automatically delivered by this wall
   workflow. Do not force-clear a busy or unsettled mind to manufacture proof.
4. Verify the relevant checks detect a controlled failure and report missing or
   stale evidence honestly, without disrupting live shared services. Preserve
   the evidence and name the detector, resolver, and detection interval. Use an
   isolated fixture for unsafe controls. A single good sample is not recurrence
   coverage. For a repair, verify the original changed path and its recovery.

If CI is failing, retain the run URL in chat, address genome with a bounded
repair, and check the replacement run after it lands. Do not describe an open
failure as a completed healthy plant. Remove only specifically identified obsolete
windows after replacements are checked live; never kill the shared tmux server.

## 5. Hand back an inspectable result

Keep local chat, checks, notes, artifacts, launchers, and service units in the
ignored site. Put reusable changes in tracked source; inspect the scoped diff
and confirm `git ls-files SITE` is empty before committing. Configure the
project's actual build, CI, deployment, research, or data checks rather than
claiming the generic host checks cover its goals. Running-code changes require
deterministic checks, independent reading, snapshot activation, visible failure
observation, and tested recovery; use the reviewed `verify` recovery exercise
before claiming verified delivery. Installation alone is not that claim.

Report:

- target, site, actual tmux session, resident windows, and service mode/states;
- how to attach, switch windows, read the current dashboard, and detach;
- the first task's artifact, observed result, exact wake settlement, rotation,
  and next-context continuity evidence;
- every RED/UNKNOWN or unexercised criterion, with resolver, bounded next action,
  and disposition time or named retry trigger.

Explain that detaching or closing a terminal does not stop persistent agents;
point to the setup guide's scoped stop procedure (`seed stop` for the session,
`seed uninstall --session SESSION` to preview and then remove the installed
units, files and sessions). Never broaden scope to other nodes, accounts,
devices, or linked sites to make setup green.
