# Inspect, recover, and update a plant

[README](../README.md) · [Getting started](getting-started.md) · [How it works](how-it-works.md)

Use this guide after planting. Commands below distinguish inspection from actions that append records, rotate an agent, or refresh installed services. The examples use your site's actual paths, not a universal `.mishe-seed` address.

Jump to: [find your site](#find-the-site-you-mean) ·
[inspect it](#inspect-the-running-plant) ·
[recover a held wake](#recover-a-held-wake-or-clear) ·
[replant](#repeat-a-plant-without-erasing-local-work) ·
[CI and releases](#understand-ci-and-linked-releases) ·
[permissions](#decide-a-scoped-permission) ·
[local state](#keep-local-state-out-of-shared-source).

## Find the site you mean

A **site** is a directory directly inside an owned Git worktree. A **session** is the tmux session owned by that site. The launcher prints the session, but the first planting defaults are not a reliable address for an existing plant.

From an operator terminal, inspect available sessions and their ownership markers:

```bash
tmux list-sessions -F '#{session_name}'
```

Choose the session you intend to operate, then inspect its site marker; replace the example value:

```bash
SESSION='actual-session-name'
tmux show-option -qv -t "$SESSION" @mishe-tauftauf-home
```

Set `SITE` to the exact absolute path reported. Check the site's marker and expected windows:

```bash
SITE='/absolute/path/to/worktree/.actual-site'
cat "$SITE/.seed-raised"
cat "$SITE/health/windows.json"
cat "$SITE/health/services.json"
```

`.seed-raised` records the session name and tmux session identity. If the session is absent, use the known site's marker and launch output; a missing session is a fault to diagnose, not permission to adopt another one. The runtime refuses a session whose `@mishe-tauftauf-home` ownership marker does not match the resolved site path.
`health/windows.json` is reconciled during planting: required role windows are always expected, previously recorded extra windows remain expected only while live, and retired names are dropped. Replant after renaming or closing an extra window so the health check no longer treats it as required.

The launcher automatically searches for resident markers in `.mishe-seed` and `.mishe-tauftauf`, reuses a sole resident site, and reads its recorded session. Multiple candidates require explicit `--home` and `--session`. A custom-named site should always be addressed explicitly when replanting; it is not part of that automatic search.

For operator commands, keep passing `--home "$SITE"`. The CLI otherwise chooses `MISHE_SEED_HOME`, then the older `MISHE_TAUFTAUF_HOME` environment variable, then relative `.mishe-tauftauf`. Fresh resident mind launches export the canonical `MISHE_SEED_HOME` and prepend `SITE/bin` to `PATH`; an ordinary shell or an older process may not have either. A plausible path is not the same thing as your plant.

## Inspect the running plant

These commands inspect the tape, sample, and live panes without sending a new task:

```bash
tmux list-windows -t "$SESSION"
tmux list-panes -s -t "$SESSION" \
  -F '#{window_name}.#{pane_index} dead=#{pane_dead} pid=#{pane_pid}'
tmux capture-pane -p -t "$SESSION:health.0"
tmux capture-pane -p -t "$SESSION:genome.0"
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed status --slug genome
"$SITE/bin/mishe-tauftauf" --home "$SITE" wall show --owner genome
"$SITE/bin/mishe-tauftauf" --home "$SITE" discover show
"$SITE/bin/mishe-tauftauf" --home "$SITE" access list
```
The standard genome, health and witness panes show deterministic CI, runtime,
service and patch observations, followed by the mind's edited wall.

For an ownership-checked reading of the actual top pane, not a newly rendered approximation:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" pain read health \
  --launcher tmux --session "$SESSION"
```

Read the tape in full or follow new entries:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" feed
tail -f "$SITE/chat.log"
```

Ctrl-c stops `tail`, not the plant. The live panels may summarize only recent entries; the full feed holds the explanation, evidence path, and next owner or action.

### Read the evidence, not just the color

- **GREEN/PASS** means the displayed check passed for its stated scope. It does not certify a whole application, model, or host.
- **RED/FAIL** means a check found a fault. Keep the failure visible while its owner reproduces and repairs it.
- **UNKNOWN** means evidence is missing, stale, unavailable, or conflicting. It is not a pass with a shy personality.
- The advancing `-- pane live ...` footer proves the renderer is alive. Compare captures a few seconds apart; an advancing lease does not erase a failed check.
- `pane_dead=0` proves a process is alive, not that an agent is ready for a prompt or making progress.

`seed status` reports tape identities: `pending` is an unsettled wake; `yield` is the last settled wake; `clear` is the settled wake whose process has been rotated; `continue` records a continuation request. They are entry/wake identities, not durations. `none` means no such recorded event.

For a persistent plant, use the actual units in `SITE/health/services.json`. Default role units are named from the session:

```bash
ROLE='genome'
systemctl --user status "$SESSION-$ROLE.service" "$SESSION-health.service"
journalctl --user -u "$SESSION-$ROLE.service" -n 100 --no-pager
```

The core plant also has a `SESSION-coordination.service` release follower. Manual setups have no newly installed resident services, so absence of those units is not itself a failed setup. Conversely, `--no-services` does not deactivate units from an earlier persistent plant.
The health dashboard's `STATE: UNKNOWN — services unavailable: Failed to connect to bus: No medium found` means its renderer could not reach the user's systemd bus; it does not prove that a listed unit is down. Diagnose unit states as the same user with a reachable user bus (usually `XDG_RUNTIME_DIR=/run/user/$(id -u)`) before attributing UNKNOWN to service health. The dashboard supplies this runtime directory only when unset; genuine query failures remain UNKNOWN.

### Doctor is a check with a repair side effect

`doctor` checks feed framing, executable local programs, the site's Git boundary, and optional adapters. It can also **rebuild missing or mismatched handoff projections from the tape**. It is not strictly read-only:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor \
  --panes --session "$SESSION"
```

The pane check waits for live-pane evidence (11 seconds by default). Optional `laya` being unavailable is not a missing dependency for the default plant. Do not add hosted-adapter live flags unless you intend to invoke those services.
The optional witness analysis advisor asks a local Laya model which investigation to try; [observable analysis advice](analysis-advisor.md) covers its setup, feedback and handoff repeat check.

If doctor reports site files staged or tracked in Git, resolve that boundary before landing work. If it reports corrupt feed framing, preserve the tape and diagnose the reported entry rather than appending invented receipts or deleting history to turn the pane green.

## Recover a held wake or clear

The supervisor's `seed run` loop starts or recovers resident panes, delivers eligible wakes, and clears settled minds at an idle boundary. Generated role services use a five-second loop and a 30-second clear grace. A direct `seed run` uses a 15-second grace unless you supply another value.

A held result usually asks a specific question: is the wake still unsettled, is the agent busy, is the readiness probe failing, or is the supervisor loading an old runtime? Answer that question before trying a restart.

### 1. Capture the state

Use the affected role, not necessarily `genome`:

```bash
ROLE='genome'
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed status --slug "$ROLE"
tmux capture-pane -p -t "$SESSION:$ROLE.1"
tmux display-message -p -t "$SESSION:$ROLE.1" \
  'command=#{pane_current_command} dead=#{pane_dead} pid=#{pane_pid}'
journalctl --user -u "$SESSION-$ROLE.service" -n 100 --no-pager
```

In a manual setup, read the foreground supervisor's output instead of expecting a service journal. Inspect the task's cited artifact and `SITE/handoffs/ROLE.md` too. A static-looking screen may still belong to an active tool; unchanged pixels are not permission to kill it.

### 2. Distinguish pending delivery from awaiting clear

| Observation | Meaning and next action |
| --- | --- |
| `pending` names a wake | It has not been settled. Read its task, lower pane, and receipts. Do not clear it or fabricate a yield. |
| `held ... unsettled` | The supervisor waits at least 60 seconds after the last delivery before considering redelivery. Let the running agent finish. |
| `held ... mind busy` or `HOLD ... resident mind busy` | Readiness is not established. Check the agent screen and, for another engine, its probe. |
| `yield` differs from `clear`, with no pending wake | A settled mind awaits rotation. Check the grace interval, handoff, and stable idle prompt. |
| A resident pane is dead | A running `seed run` supervisor recreates dead panes. Check why the process exited and whether its supervisor is active. |
| Supervisor journal names paths from an old release | Repairing checkout source has not necessarily changed the source loaded by this service. Inspect its unit before restarting it. |

An unsettled wake is redelivered only after 60 seconds and only when readiness says the mind is idle. The wake and handoff remain available through recovery. Codex recognition rejects working/executing states; OMP recognizes idle prompts including tokens such as `INSERT y >` and rejects working spinners. A custom engine's probe must return zero only when safe; see [agent readiness](getting-started.md#use-another-agent-command).

For a clear, the supervisor also checks that the pane remains unchanged over a short interval at a recognized idle prompt. Process rotation must produce a different PID and a live pane. A PID change proves a new process, not prompt readiness; the next wake still passes the readiness gate.

### 3. Check which runtime the supervisor loads

These are inspection commands:

```bash
systemctl --user cat "$SESSION-$ROLE.service"
systemctl --user show "$SESSION-$ROLE.service" \
  -p FragmentPath -p ExecStart -p Environment -p WorkingDirectory
```

Confirm the interpreter, `WorkingDirectory`, and especially `PYTHONPATH` point to the intended runtime. Restarting a service pinned to an old checkout loads the old code again. Replanting refreshes generated services, but is a mutation with [preservation rules](#repeat-a-plant-without-erasing-local-work), not a harmless status check. For linked plants, prefer the checked [release path](#understand-ci-and-linked-releases).

If the unit is already correct and its supervisor needs restarting, this affects the role's **supervisor**, not a direct process kill:

```bash
systemctl --user restart "$SESSION-$ROLE.service"
```

Then reread its journal and `seed status`. Do not use broad process-kill commands or delete a tmux session to cure an unexplained hold.

### 4. Retry a settled clear normally

Only after verifying a settled wake, a present handoff, and a stable idle mind:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed clear \
  --session "$SESSION" --slug "$ROLE"
```

This **rotates the idle agent process in its existing lower pane**. It preserves the handoff and records the clear; the next real wake carries the charter and handoff into the fresh process. It refuses an unsettled wake, a missing handoff, a dead pane, or an unstable/busy mind. Treat a refusal as diagnostic evidence, not an obstacle to bypass.

Success is a `clear seed ROLE after ...` receipt, a changed live lower-pane PID, and a later readiness-checked wake that restores the task's next step. The top pane should continue refreshing throughout.

## Repeat a plant without erasing local work

Run host coordination commands from this core checkout. Address a known site explicitly when needed:

```bash
python3 -m coordination.launcher \
  --workspace /absolute/path/to/worktree \
  --home "$SITE" --session "$SESSION" \
  --engine-command 'codex'
```

This is an **installation refresh**: it can refresh default instructions, rewrite and restart resident services, take discovery/CI readings, and update an external `AGENTS.md` contract. Review that scope first. Unlike checked linked deployment, direct planting is not a CI-gated release operation.

Repeat planting preserves existing mind launchers, top-pane programs, live panes, handoffs, and customized local instruction additions. Default doctrine/charter copies that still match their recorded baseline can be updated; custom additions are preserved and accompany the current tracked rules during restoration. Supplying a new `--engine-command` does not replace existing `SITE/minds/ROLE` files. The site's CLI wrapper is also created only if absent; do not assume a repeat plant rewrites every local launcher.

The target's generated `AGENTS.md` block is refreshed without overwriting surrounding application instructions. Genome receives a scoped task to review and land that contract change. No application tree is copied from the kernel.

Generated checks are a starting point. Add the target's real build, test, service, and data checks to its local pane programs as needed. Each consequential gate should name its source, show a verdict, and have an actual failure state. A successful unit test verifies a protocol; it does not prove the live panes and services are wired correctly.

## Understand CI and linked releases

### CI readings

Each site's CI watcher asks GitHub for the repository's default branch and checks Actions runs for the exact SHA of its local `origin/BRANCH` reference. It does **not** turn a pass for an older commit into a pass for the current one. It considers the latest observed run for each returned workflow, records transitions as `[ci]`, and addresses genome on failure.

Unavailable `gh`, failed queries, no run for the SHA, and samples older than five minutes remain `UNKNOWN`. A pending run remains pending. The reading is not proof that every conceivable workflow exists, nor does the watcher itself fetch remote Git refs. Inspect the cited full SHA and run URL when diagnosing a mismatch.

Genome keeps a CI task open through reproduction, repair, review, commit, push, and a green replacement run. Each target may add its own CI or deployment checks; the stock pane is not a substitute for the application's release criteria.

### Automatic linked refresh

A persistent external plant registers its site and session in the core site's ignored `health/linked-sites.json`. The core release coordinator follows cached CI readings at most five minutes old and retries roughly once a minute. It refreshes registered plants only when:

1. CI is passing for the exact core checkout `HEAD`.
2. The core checkout has no unlanded changes.
3. The target is a separate direct Git worktree with a matching site marker and an existing tmux session owned by that site.
4. For a normal refresh, the target worktree also has no unlanded changes.

An already applied SHA without a recorded error is skipped. A hold is explained in the core `chat.log`; later eligible passes retry it. After refreshing, the coordinator checks the target's feed, expected top panes, live mind panes, advancing leases, and named services before recording the applied SHA.

A changed generated target contract becomes a target genome task to review, commit, push, and verify in that repository. Thus the refresh itself may create the tracked change that genome must subsequently land. Application files are never copied from the core.

To retry now after resolving a hold, run this from the core checkout, with `CORE_SITE` set to the **core's** actual resident site, not the target's:

```bash
CORE_SITE='/absolute/path/to/core-checkout/.actual-core-site'
python3 -m coordination.site_sync --home "$CORE_SITE"
```

This **can refresh all eligible registered sites**, not just the one you inspected. A one-shot invocation queries CI directly; `--follow` uses fresh cached watcher readings. There is no per-target CLI flag here. Check the registry's scope before invoking it, then read the emitted outcomes and core tape.

### Runtime-only refresh while application work is dirty

For an operator-requested update when targets have unrelated application changes:

```bash
python3 -m coordination.site_sync --home "$CORE_SITE" --runtime-only
```

This still requires clean core source, passing CI for its exact commit, and an existing owned target session. It allows target worktree changes and preserves the target's `AGENTS.md` and application work while refreshing ignored runtime state and generated services. It operates on the registered eligible sites and retains the already-applied-SHA skip. It is not an unconditional reinstall or permission to deploy unreviewed core changes. Normal automatic refreshes retain the clean-target gate.

The core deployment report describes CI freshness and recorded applied releases. It is evidence of **deployment convergence**, not an approval gate for reviewing or landing code.

### Advance the core plant's own runtime pin

The core plant's minds, panes and services import the release named in
`SITE/health/runtime-release.json`, not the shared checkout, so a committed
kernel change becomes running code only when that pin moves. Point the core at a
clean detached worktree of the exact commit — the release coordinator creates
these under `SITE/releases/<sha>` — with the launcher:

```bash
python3 -m coordination.launcher \
  --home "$CORE_SITE" --session "$SESSION" \
  --runtime-only --runtime-source "$CORE_SITE/releases/<sha>"
```

The launcher requires an existing owned session and refuses a source that is not
a clean Git worktree root or that lacks the runtime package. It writes the pin,
repoints the site CLI, respawns the upper evidence panes, and reconciles the
generated and covered services onto the release, leaving lower minds' work and
the checkout's `AGENTS.md` untouched. Then confirm the dashboards' `RUNTIME` line
reads `MATCH` — `DRIFT` names a service still importing another root — and check
the actual consumer and its live pane. Moving the pin is not a push or a CI
result; keep source, CI and running code as separate evidence.

The launcher writes the pin before it restarts the covered services, so a
discovery scan that lands inside that window reports every session unit as stale
against the new pin — the swap transient, not persistent drift. Re-read the
`RUNTIME` line and the across-sites sample (`sense.runtime.drift-across-sites`)
after the launcher returns before treating the units as drifted.

### Linked health is a separate live check

The core health pane reads each registered site's `health/services.json` and checks those named units on **this host's user systemd manager**. It is read-only with respect to linked services and independent of the recorded applied SHA. It is not remote fleet monitoring.

- An inactive/non-active named service makes that linked site's health RED.
- A missing or malformed registry, site record, or registered site's service manifest makes the result UNKNOWN.
- Unavailable service status or an exhausted probe budget is UNKNOWN, not proof of failure or success.
- The supported registry is at most **16 sites**, with at most **32 units per site**. Oversized data is UNKNOWN rather than silently treated as complete coverage.
- Linked service probes share a **five-second budget** per render. A slow probe can leave later units UNKNOWN; check the named units individually to distinguish time exhaustion from a stopped service.
- An absent registry means no linked sites are configured, as on an external plant. Once a site is registered, its missing manifest is still UNKNOWN.

A deployment SHA tells you what was recorded as applied. A live service check tells you whether the named services run now. Keep both; neither can do the other's job.

## Decide a scoped permission

The public CLI is **`access`** (also **`permit`**). Requests record a stable ID, owner, task, capability, reason, and one or more paths the decision would unblock. The permissions pane and tape show the requests and operator decisions.

The permissions window opens a keyboard selector in its bottom pane. Use Up/Down to select a request, read its owner, capability, reason and affected paths, then press Enter to grant that request. Page Up/Down scroll long scopes. A request recorded with `permit recover` also shows its resolver, missing evidence, after-grant action, alternatives and cutoff; a grant permits a retry, and success still needs evidence. The selector remains open for the next request and refreshes as new requests arrive; it never grants a batch automatically. Press `q` to return to the operator shell.

The generated shell puts a site-local `permit` wrapper on `PATH`. `permit`, `permit menu`, or `permit grant` opens the grant selector; `permit revoke` selects from granted requests. Outside that shell, use `"$SITE/bin/mishe-tauftauf" --home "$SITE" access menu`. An interactive terminal is required; automation must supply an exact ID. Explicit `permit grant ID` and `permit revoke ID` remain supported. A near-miss ID suggests the closest existing ID without granting it. If the displayed scope or decision changes before a selection is recorded, the selector refuses that stale decision and refreshes for review.

Inspect first:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" access list
"$SITE/bin/mishe-tauftauf" --home "$SITE" access check REQUEST_ID
```

Replace `REQUEST_ID` with a real ID. `access check` exits zero only for `granted`; pending and revoked decisions exit nonzero. An unknown ID is an error, not an implicit grant.

For example, a role requesting an unavailable local capability would append a scoped request like this; replace the task and paths with the actual need before using it:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" access request battery-read \
  --owner senses --task battery-observation --capability local-device-read \
  --unblocks /sys/class/power_supply \
  --reason 'Verify a battery reading for this local task.'
```

`--unblocks` can be repeated. Reusing the same ID with different scope is rejected. A request is not permission; the operator must review the exact scope before deciding.

The following commands **record an operator decision** for an existing request:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" access grant REQUEST_ID
# Or withdraw that scoped authority:
"$SITE/bin/mishe-tauftauf" --home "$SITE" access revoke REQUEST_ID
```

These write `[permission]` entries in `chat.log`; they do not change Unix permissions, create credentials, open devices, or enforce a kernel sandbox. A granted capability is ready to use only where the host actually supplies it. Pending or revoked requests confer no authority to act. Granting one task's scoped capability is not blanket authority over the host or another plant.

Discovery itself begins with bounded read-only samples: commands on `PATH`, `/proc` and `/sys`, disk space, tmux windows, and input activity counters. The counters do not capture key content. A command being present is a candidate, not evidence that a useful reading or a permission-sensitive action will work.

## Keep local state out of shared source

Tracked Git content carries reusable runtime code, tests, setup, general instructions, and the default culture. Ignored site content carries the current plant's tape, charters, handoffs, artifacts, drafts, checks, unit files, observations, and access ledger. A fresh clone should not inherit another node's running state.

Keep these boundaries when inspecting or landing work:

- Do not stage or commit the site directory. Doctor checks for tracked/staged site files; an ignore rule alone does not untrack already committed files.
- Do not promote a local observation, artifact, or private permission decision into shared source merely because it helped once.
- A reusable lesson becomes shared behavior through a deliberate source change, verification, independent review, and scoped landing.
- Commit scoped work to the single `main` branch; there are no publication or candidate branches. Genome pushes `main` and checks the pushed commit's CI; authors verify rollout and deployed consumers. Delivery remains incomplete until live verification succeeds; CI repairs also need the successful replacement run.
- Preserve the append-only tape and handoff trail during diagnosis. They explain what happened and which action remains owed.

The local agent contract is [AGENTS.md](../AGENTS.md). Tracked [seed doctrine](../src/mishe_tauftauf/seed_doctrine.md) is loaded at restore and mirrored into `SITE/doctrine.md` at planting. The [mesh culture mapping](../instructions/mesh-culture.md) describes the source rules this narrower seed carries; this plant does not claim fleet authority or the larger mesh's native lifecycle accounting.

For first-task proof, return to [Getting started](getting-started.md#what-counts-as-success). For charters, task protocol, and the observation-to-action loop, see [How it works](how-it-works.md).

## Landing reviewed source

`main` is the sole branch, locally and on the remote. Authors commit their own scoped
work directly on it in the shared checkout and report the exact commit. There are no
candidate or publication branches. Genome pushes `main` and checks the pushed commit's
CI and its live consumers; it neither authors nor gates another mind's commit. No
reviewed work is parked uncommitted waiting on genome or on an operator, who is the
human owner rather than a pane.

Before committing, run the required checks and keep the index free of site state. Store
the checks and any independent reading under `SITE/artifacts`; the immutable digest binds
the commit and its verification. Preserve the reviewer's full findings. After the push,
the author deploys the clean release to owned consumers and verifies their live checks.

```sh
# Commit scoped work on main, then hand the push to genome.
git -C "$WORKSPACE" add PATH...
git -C "$WORKSPACE" commit -m 'Scoped change and evidence'
git -C "$WORKSPACE" rev-parse HEAD   # report this exact commit

# Genome pushes the single branch and checks its CI.
git -C "$WORKSPACE" push origin main
env PYTHONPATH=src python3 -m mishe_tauftauf.ci_watch --home "$SITE"   # or read the live CI pane
```

If main advanced while an author worked, inspect the current diff and preserve
peers' edits; rerun checks when changes affect reviewed bytes. Record exact
committed and deployed revisions, live consumers and failures under the ignored
site. Historical task and delivery reports remain available read-only for
recovery; they do not provide another publication workflow.

Running-code delivery uses reviewed snapshots, observable activation and the
tested recovery exercise in [wall coordination](wall-coordination.md).
Applied bytes alone are incomplete delivery.

## Live experiments

Within granted owned scope, try consequential ideas live. Name the prediction,
affected consumers, bounded observation and keep/revise/revert decision. Checks,
independent reading and recovery enable high-risk, high-reward work without a
routine approval gate. Preserve failed observations and delete superseded code;
Git history retains prior implementations instead of dormant feature flags.
