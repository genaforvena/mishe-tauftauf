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
"$SITE/bin/mishe-tauftauf" --home "$SITE" task show
"$SITE/bin/mishe-tauftauf" --home "$SITE" discover show
"$SITE/bin/mishe-tauftauf" --home "$SITE" access list
```

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

The core plant also has a `SESSION-coordination.service` release follower. Manual trials have no newly installed resident services, so absence of those units is not itself a failed trial. Conversely, `--no-services` does not deactivate units from an earlier persistent plant.

### Doctor is a check with a repair side effect

`doctor` checks feed framing, executable local programs, the site's Git boundary, and optional adapters. It can also **rebuild missing or mismatched handoff projections from the tape**. It is not strictly read-only:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor \
  --panes --session "$SESSION"
```

The pane check waits for live-pane evidence (11 seconds by default). Optional `laya` being unavailable is not a missing dependency for the default plant. Do not add hosted-adapter live flags unless you intend to invoke those services.

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

In a manual trial, read the foreground supervisor's output instead of expecting a service journal. Inspect the task's cited artifact and `SITE/handoffs/ROLE.md` too. A static-looking screen may still belong to an active tool; unchanged pixels are not permission to kill it.

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

Each site's CI watcher asks GitHub for the repository's default branch and checks Actions runs for the exact SHA of its local `origin/BRANCH` reference. It does **not** turn a pass for an older commit into a pass for the current one. It considers the latest observed run for each returned workflow, records transitions as `[ci]`, and opens a genome task on failure.

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

The public CLI is **`access`**, not `permit`. Requests record a stable ID, owner, task, capability, reason, and one or more paths the decision would unblock. The permissions pane and tape show the requests and operator decisions.

The generated permissions shell also puts a site-local `permit` wrapper on `PATH`; it forwards to that site's `mishe-tauftauf --home SITE access`. That shorthand is why the panel may advertise `permit grant ID`. It is not a top-level `mishe-tauftauf permit` subcommand. The explicit `access` commands below work from an ordinary operator shell without relying on that extra `PATH`.

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
- Authors commit scoped isolated candidates excluding site state, obtain independent review, publish branches and check exact CI. Genome integrates ready commits; authors verify final main CI and rollout. The task stays open until delivery is complete; CI repairs also need the successful replacement run.
- Preserve the append-only tape and handoff trail during diagnosis. They explain what happened and which action remains owed.

The local agent contract is [AGENTS.md](../AGENTS.md). Tracked [seed doctrine](../src/mishe_tauftauf/seed_doctrine.md) is loaded at restore and mirrored into `SITE/doctrine.md` at planting. The [mesh culture mapping](../instructions/mesh-culture.md) describes the source rules this narrower seed carries; this plant does not claim fleet authority or the larger mesh's native lifecycle accounting.

For first-task proof, return to [Getting started](getting-started.md#what-counts-as-success). For charters, task protocol, and the observation-to-action loop, see [How it works](how-it-works.md).

## Author-owned source delivery

Source authors retain delivery ownership. Prepare each candidate in a separate linked
worktree of this plant's repository, based on current origin/main. Keep one active
candidate per author/repository; blocked candidates remain visible and free that slot.
Shared checkout drafts are exceptional recovery work and cannot hold clean candidates.

Run the required checks, commit scoped source bytes, obtain independent review of that
exact base/head, and push the author branch. Store the review under SITE/artifacts as
JSON with `base`, `head`, `reviewer` (a role different from the author), and `verdict`:
`"pass"`. The artifact records the review result; its immutable digest binds submission
and integration. Preserve the reviewer's full findings alongside it. The current
workflow requirement is a successful push run named `CI` for the exact branch and SHA.

```sh
"$SITE/bin/mishe-tauftauf" --home "$SITE" delivery submit TASK_ID \
  --owner senses --repo "$CANDIDATE" --base "$BASE_SHA" \
  --branch "$AUTHOR_BRANCH" --review "$SITE/artifacts/review.json"
"$SITE/bin/mishe-tauftauf" --home "$SITE" delivery show
```

Submission records the author's task waiting on `delivery-TASK_ID-updated`. The
existing CI watcher checks candidate refs and exact branch CI every minute. Pending
CI creates no genome task or model polling. A real failure returns work to the author;
a passing reviewed commit creates one integration attempt per readiness transition.
Genome receives only that integration step:

```sh
"$SITE/bin/mishe-tauftauf" --home "$SITE" delivery integrate TASK_ID --source genome
```

The command revalidates review, clean exact-head candidate, published branch and CI,
then pushes a fast-forward to main with an exact-base lease. It changes no shared
checkout bytes or index. If main advanced, the author rebases, reruns checks, renews
review and publishes a new revision before resubmitting. After a crash, the watcher
reconciles actual remote ancestry and repairs missing receipts before another push.
Once genome/operator has initiated an exact integration, the watcher may resume that
same leased push after revalidating refs, review and CI. A persistent rejection stays
blocked without model retries; a new candidate revision requires a new initiation.
An active integration attempt prevents candidate replacement until it settles.
Delivery records and the exact main lease are serialized separately from slow private
publication checks. A saved fact may precede its admitted feed projection. Final
commit guards reject a projection if its delivery snapshot or task registration
changed during review. Reconcile the saved record after a refusal; do not repeat a
successful remote push or overwrite a newer task wait. The watcher retries pending
projections from current facts without retaining the global delivery lock during
model work.

The author wakes after integration, checks the exact main push CI, deploys the clean
release only to owned consumers, and verifies their live checks. Store a JSON rollout
receipt with `sha`, `state`: `"pass"`, and a nonempty `consumers` list identifying the
checked artifacts/targets. Then complete the author's delivery:

```sh
"$SITE/bin/mishe-tauftauf" --home "$SITE" delivery finish TASK_ID \
  --owner senses --evidence "$SITE/artifacts/rollout.json"
```

Final main CI must pass; unfinished author child tasks prevent completion. The
consumer receipt is the author's evidence claim and needs live verification. Records
and immutable transition snapshots stay inside the ignored site. `delivery check
TASK_ID` performs a bounded manual reconciliation; the watcher normally owns it.
Historical `task landing-status` remains readable and `task production-check` reports
source production allowed. New raw landing registrations are retired; no recursive
landing graph or global HOLD remains.

## Temporary branch retirement

After `delivery finish`, the existing CI watcher checks retirement each tick. It verifies completed rollout, ancestry against actual remote main, exact unchanged local and remote candidate refs, a clean worktree, and no live process or installed runtime reference. It saves and verifies a recovery bundle under `SITE/retired-candidates/` before deleting the remote branch with an exact lease, atomically relocating the entire candidate directory and its exact Git administrative registration into private recovery archives, and atomically deleting the exact local ref. It preserves the shared checkout and detached runtime releases.

A busy process, new dirty files, advanced branch, unavailable check, or failed removal stays visible in `delivery show` as retirement pending. The watcher retries the same concrete condition; it does not create repeated model work. Run `delivery retire ID` for a bounded manual reconciliation from outside the candidate directory. Retirement resumes from the saved recovery edge after a crash. Full-directory relocation preserves ignored files even if they arrived after the admission check; no recursive deletion or broad registration pruning occurs. Useful unmerged or dirty work must first be reconciled, adopted into active delivery, or archived with a checked supersession reason. Main is the durable branch; temporary branches have an active task and are retired once their checked outcome is delivered.


Runtime refresh samples CI without running candidate task projections. The persistent CI watcher continues to own delivery reconciliation and retirement; its configured publication checks may wait independently while the installer restarts services onto the selected clean release.
