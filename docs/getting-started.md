# Plant a site and watch one task

[README](../README.md) · [Operating guide](operating.md) · [How it works](how-it-works.md)

This guide starts with a fresh checkout and ends with a task whose result you can inspect. If a plant already lives in the worktree, use [its actual site and session](operating.md#find-the-site-you-mean) instead of the fresh-checkout names below.

## 1. Check the prerequisites

Run the host commands from this repository's checkout, even when the work will happen in another repository. They load `src/` themselves; a package install is not required.

You need:

- **Linux and Python 3.10 or newer.** The runtime has no required third-party Python dependencies.
- **Git and an owned Git worktree.** The site directory must sit directly inside the worktree root, not inside an arbitrary subdirectory and not at the repository root itself.
- **tmux.** This supplies the shared terminal session and its panes.
- **An installed, usable coding-agent CLI**, such as Codex or OMP, with its own authentication and model access already configured. Planting starts real agents that may consume your provider's usage or credits. It checks that a new mind's command exists; it does not supply credentials or prove that a model request will work.
- **A working user systemd manager** for persistent operation. A fresh [manual trial](#try-it-without-installing-services) can omit this.
- **GitHub CLI (`gh`) and access to the repository's Actions runs** for CI readings and checked linked releases. Without them the CI result is `UNKNOWN`; local panes can still run. A repository without a matching Actions run also remains `UNKNOWN`.

These read-only checks catch common missing prerequisites:

```bash
python3 --version
git --version
git rev-parse --show-toplevel
tmux -V
command -v codex                  # Substitute your installed agent command.
systemctl --user show-environment # For persistent operation.
gh auth status                   # For GitHub CI readings.
```

Use a site path without whitespace: generated service units do not support whitespace in it. Inspect help without creating a plant:

```bash
python3 -m coordination.launcher --help
python3 -m coordination.site_sync --help
```

## 2. Know what planting changes

A **site** is the local directory containing this plant's state. A **session** is its tmux session. A **mind** is the agent process in a role's lower pane; it runs with your account's existing operating-system access.

Before running the launcher, understand its scope:

- It creates an ignored site with launchers, charters, checks, observations, handoffs, artifacts, a permission ledger, and an append-only `chat.log`. If necessary, it adds a local ignore rule to Git's `info/exclude`; it does not commit that state.
- It creates five resident windows (`discover`, `senses`, `health`, `genome`, `witness`), a `permissions` panel, and an `operator` shell. Each resident window has a refreshing observation pane above an agent pane.
- It starts the agents, takes an initial bounded discovery sample, and attempts a CI reading. Persistent supervisors can subsequently wake agents for tasks, changed observations, continuations, or quiet self-selected work within their charters. This is not an inert dashboard.
- Unless `--no-services` is supplied, it writes, links, enables, and starts user services: `SESSION-ROLE.service` for the five minds, plus `SESSION-permissions.service` and `SESSION-ci.service`. Replanting refreshes those units and restarts ones already active.
- Planting this core checkout persistently also installs `SESSION-coordination.service`, the linked-site release coordinator. A persistent external plant registers with that coordinator if the core plant already exists.
- Planting **another worktree** adds or refreshes the marked plant-contract block in its `AGENTS.md`, preserving surrounding instructions. That tracked change becomes a genome task to review and land in the target repository. Application files are not copied from this repository.

Services use the Python interpreter running the launcher and this checkout's `src/` via `PYTHONPATH`. Keep both available. Copying a site directory alone does not make a self-contained installation.

The permission ledger is a scoped record of operator decisions, not an operating-system sandbox. Choose a worktree and account whose scope you intend the agents to use. See [permissions and local state](operating.md#decide-a-scoped-permission).

## 3. Plant this fresh checkout

For a **fresh checkout with no resident site**, using a working Codex command:

```bash
python3 -m coordination.launcher --engine-command 'codex'
```

The fresh defaults here are `.mishe-seed/` and session `mishe-seed`. The launcher prints `plant ready: session=...` and whether services were enabled. On a repeat plant, it can reuse a different existing resident site and the session recorded in its marker. **Use the actual result, not the example names.**

For the fresh defaults only, set these shell variables for the remaining examples:

```bash
SITE="$PWD/.mishe-seed"
SESSION='mishe-seed'
```

The site's CLI wrapper loads the runtime; ordinary operator shells still need the explicit `--home` argument:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" discover show
tmux list-windows -t "$SESSION"
tmux attach -t "$SESSION"
```

### A small tmux map

With the default tmux key bindings, press **Ctrl-b**, release it, then:

| Key | Action |
| --- | --- |
| `w` | Choose a window; select `discover` for the first task. |
| `o` | Move between the upper and lower panes. |
| `d` | Detach back to your terminal; leave the plant running. |
| `[` | Enter scrollback; `q` leaves scrollback. |

The upper pane is the current evidence display. The lower pane belongs to the agent; do not type diagnostics into a busy mind. Use the `operator` window or a second terminal for commands. Detaching is not stopping. The plant does not interpret your closing a terminal as a resignation letter.

## 4. Give it one real, bounded task

This command **appends an obligation and can wake `discover`**. It asks for a real local reading, not a source-code change. Run it once; choose a new task ID if you want a separate later trial.

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" append --source operator \
  '[task] hello-world owner=discover source=/proc/loadavg acceptance=fresh-scan-and-live-pane retry=next-scan. Read one real load value, cite the scan artifact, verify the discover top pane, and mark this task done.'
```

Watch the `discover` window. In a second terminal, with the same `SITE` and `SESSION` variables:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed status --slug discover
"$SITE/bin/mishe-tauftauf" --home "$SITE" task show --owner discover
"$SITE/bin/mishe-tauftauf" --home "$SITE" discover show
tmux capture-pane -p -t "$SESSION:discover.0"
tail -f "$SITE/chat.log"
```

`tail -f` watches the tape; Ctrl-c stops only that watcher. To read the complete framed conversation through the CLI, use `"$SITE/bin/mishe-tauftauf" --home "$SITE" feed`.

### What counts as success

Look for all of these, not just a successful launcher exit:

1. The expected windows exist and the top-pane `-- pane live ...` timestamp advances. That timestamp proves the renderer is alive, not that its checks passed.
2. `discover show` reports a fresh scan and a verified `sense.proc.loadavg` sample. The task's artifact points to the actual scan under `SITE/discovery/`, and the live `discover` top pane shows the reading.
3. The tape shows `[taking]` for the task, a checked result and handoff, a supervisor `[work]` receipt, and `[done]` for the same ID. Read the receipt's evidence rather than treating the tag alone as proof.
4. After settlement and an idle clear, the supervisor records a process rotation. On the next actual wake, the mind receives its charter and handoff and reads the live evidence again. This last step may not happen immediately; it needs another real wake.
5. In persistent mode, the resident units named in `SITE/health/services.json` are active. The [operating checklist](operating.md#inspect-the-running-plant) covers those checks.

If the task is held, do not manufacture a `[done]` entry. Check the lower pane, readiness, and supervisor first; [recovery](operating.md#recover-a-held-wake-or-clear) explains the distinction between an unsettled wake and a settled mind awaiting clear. A missing reading should remain `UNKNOWN` with an exact retry condition.

## Plant another owned worktree

Run from the **core checkout**; replace the path with the target worktree root:

```bash
python3 -m coordination.launcher \
  --workspace /path/to/project \
  --engine-command 'codex'
```

A fresh external target defaults to `TARGET/.mishe-tauftauf/` and session `mishe-` followed by the target directory name, with underscores changed to hyphens. Existing sites reuse their recorded names. Set `SITE` and `SESSION` from the actual output before attaching or inspecting them.

For a deliberately named site, pass `--home /path/to/project/.chosen-site --session chosen-session`. The home must still be directly inside that Git worktree. The launcher refuses to take over a tmux session or user unit belonging to another site. If multiple recognized resident sites exist, choose explicitly with `--home` and `--session`; do not guess which one is authoritative.

Plant the core persistently **before** the external worktree if you want automatic linked updates. If the launcher reports that the external plant was unregistered because no core plant existed, establish the core and repeat the external plant deliberately. [Linked release rules](operating.md#understand-ci-and-linked-releases) cover the gates and the generated-contract task.

## Try it without installing services

For a **new trial site**, add `--no-services` to the launcher command:

```bash
python3 -m coordination.launcher --engine-command 'codex' --no-services
```

This still creates the site, starts panes and agents, takes initial discovery and CI readings, and updates an external target's plant contract. It skips installing/enabling the resident user services and the release coordinator, and does not register an external trial for linked updates. Its service manifest is empty.

**It does not start the continuous wake/clear supervisors, CI watcher, or permission-panel follower. It also does not stop or disable services from a previous persistent plant.** Use it for a fresh trial, not as a way to turn off an existing installation.

To exercise the first task manually, set `SITE` and `SESSION` to the trial's actual names, append the task above, then run this supervisor in a separate operator terminal:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed run \
  --session "$SESSION" --slug discover --interval 5 \
  --self-pick-seconds 600 --clear-grace-seconds 30
```

It supervises **only `discover`**: wakes that role, recovers dead panes, and clears a settled mind at a stable idle boundary. Ctrl-c stops this foreground supervisor, not the tmux session or its agents. For other roles, run their supervisors separately rather than assuming this one covers the whole plant. Do not start a second supervisor alongside an existing service for the same role.

A manual trial's initial CI sample becomes stale after five minutes without a watcher. From the core checkout, an explicit refresh writes the new sample and may append CI events or failure tasks:

```bash
env PYTHONPATH=src python3 -m mishe_tauftauf.ci_watch --home "$SITE"
```

The `permissions` display refreshes in its own top pane even in a manual trial; without its follower, dead panel panes are not automatically repaired. `access list` remains available for direct inspection. Move to persistent operation by deliberately repeating the launcher without `--no-services` once you accept the mutations listed above.

## Use another agent command

The launcher accepts a shell-quoted command string, for example `--engine-command 'omp --model YOUR_MODEL'`; substitute the arguments your installed agent actually supports. Existing `SITE/minds/ROLE` launchers are preserved, so changing this flag on a repeat plant does not replace them.

Codex and OMP have built-in idle-prompt recognition. Another agent needs an executable readiness probe at `SITE/checks/mind-ready/ROLE` for each supervised role. The probe must exit zero **only when the role's lower pane can accept a complete prompt**, not merely when the process exists. It receives:

- `MISHE_SEED_SESSION`: the tmux session;
- `MISHE_SEED_ROLE`: the channel name;
- `MISHE_SEED_PANE`: the lower-pane target, such as `SESSION:discover.1`.

The probe has a two-second timeout. Missing, non-executable, failing, or timed-out probes hold delivery. Do not use an always-successful probe to make the status look cheerful. A changed pane PID proves rotation; readiness separately decides whether the next prompt can safely be delivered.

Next: [inspect and recover the plant](operating.md), or read [the runtime model](how-it-works.md).
