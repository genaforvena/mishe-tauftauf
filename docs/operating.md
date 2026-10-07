# Inspect, recover, and update a plant

[README](../README.md) · [Mesh](mesh.md) · [Getting started](getting-started.md) · [How it works](how-it-works.md) · [Wall coordination](wall-coordination.md)

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

`.seed-raised` records the session name and tmux session identity. A receipt lost, mismatched or unreadable while the owned session lives is re-derived from that session on the next `seed run` pass, so the readers that fall back to it — senses, the CLI's default session, headless renderer probes and `seed stop`'s ownership guard — recover instead of reading `UNKNOWN` indefinitely. If the session is absent, use the known site's marker and launch output; a missing session is a fault to diagnose, not permission to adopt another one. The runtime refuses a session whose `@mishe-tauftauf-home` ownership marker does not match the resolved site path. A session that exists without a marker yet is not foreign: a concurrent raise creates the session before recording its owner, so the runtime waits up to 15 seconds for the owner to appear and refuses at once only when a different home is recorded or the wait expires.
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
The standard genome and health panes show deterministic CI, runtime, service and
patch observations; the witness pane shows CI plus its coordination checks:
planted windows, clear-stall and stale-pend, and open tasks. Each is followed by
the mind's edited wall.

Genome inspects unlanded outside edits with `git status --short` and reviews each path's diff before adopting, reconciling or retiring it. Preserve unrelated edits and coordinate with existing owners; delivery follows the single-`main` authoring, CI and live-consumer checks above.

For an ownership-checked reading of the actual top pane, not a newly rendered approximation:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" pain read health \
  --launcher tmux --session "$SESSION"
```

A supervisor's `pain watch` publishes the exact frame it renders to
`SITE/dashboards/ROLE.json`. That frame is what the pane shows, what the mind is
told to read, and the sensor text the supervisor digests for change detection.
Headless `pain read` rendering preserves a caller's nonempty `MISHE_SEED_SESSION`;
when absent or empty, it uses the session recorded in `SITE/.seed-raised` when
available. Without a readable recorded session it leaves the variable unset.
Read it directly, rather than re-rendering or scraping the pane:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" pain read health --launcher dashboard
```

It fails `UNKNOWN` when the frame is missing, older than 30 seconds, corrupt, or
recorded for another site, so a stale or foreign frame is not read as current.

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
- The health pane's `PANE LEASE:` line reads each resident renderer's footer lease and turns RED when one is older than `max(120 s, 6 × refresh)`; a frozen lease also makes the health verdict RED with cause `pane-lease`, while a missing or unreadable lease is UNKNOWN. A window with no renderer script (the operator shell, the log tail) carries no lease and is not a fault.
- `pane_dead=0` proves a process is alive, not that an agent is ready for a prompt or making progress.
- The `sense.mind.wedge-suspect` reading names a mind whose open omp continue chain is at least 3 `agent.continue scheduled` events of any source and at least 15 minutes with no completed turn (`agent_end maintenance routing` `stopReason="stop"`) between (`suspects=<window>(pid=…,chain=…,span=…min,src=<source>:<count>,cause=<cause>)`, or `suspects=0`). Counting every source keeps a wedge that manifests as stream stalls or unexpected stops from escaping; `src=` shows the class breakdown and `cause` names the dominant class of the chain's `agent turn ended with provider error` records (`provider-error:<status>`, the HTTP status when the record or its message names one, `unknown` when neither names one, or `none` when the chain holds no such record), so a retry loop against a failing upstream reads differently from a hung pane. Rule R2 catches the instant-provider-error failure mode (e.g. a 403): the turn fails immediately with provider errors but no continue chain, so R cannot fire — SUSPECT when the chain is 0 with provider errors and the last error is at least 5 minutes old. An R2 suspect reads `window(pid=…,rule=provider-error,chain=0,error_age=…min,cause=provider-error:<status>)`, versus R's `window(pid=…,chain=…,span=…min,src=…,cause=…)`. The chain helper also reports `last_error_ts`/`error_count`, reset with the chain on a completed turn. It parses omp's session log under `~/.omp/logs/`, and a pane with no such log is not assessed; the sample ends with `panes=N with_log=M`, and when no enumerated pane yields a log the reading is `UNKNOWN` rather than a clean bill, so a log-path or format change cannot pass as healthy. A failed pane enumeration is also `UNKNOWN`. A named suspect is a prompt to inspect that pane, not proof of a wedge.
- The `sense.ledger.delivery-invariant` reading flags a boolean `delivery_verified` on a patch-record phase that is not a delivery outcome (`violations=<phase>:<count> …`, or `violations=0`). The accepted set — `applied`, `review-refused`, `reverted`, `revert-failed`, `revert-observation-failed` — is observed, not code-derived: `review-refused` stays because one frozen pre-2026-10-01 record carries a boolean dv there, and no current producer path produces that combination. The sense flags; it does not decide — a new legitimate delivery-outcome phase and a genuine collapse both read as "outside the set", so disposition stays with a mind. The verified sample ends with the coverage tier `records=N bool_dv=M`; an empty, missing or unreadable patch store reads `UNKNOWN` rather than a clean bill, with an unreadable store appending `unreadable=<file>`; the UNKNOWN branch still carries violations found in the readable records (trailing `violations=<phase>:<count>`), so a collapse co-occurring with a torn record is not dropped.
- The `sense.ledger.dv-binding` reading flags a patch record that claims `delivery_verified: true` without the `verification` dict that proves it (command, exit code, evidence): `unbound=<phase> records=N bool_dv=M`, or `unbound=0 …`, and the reading is `drift` when any record is unbound. Exactly one frozen record (`health-stale-pend-detection`, phase `applied`) carries that shape and no current producer path makes it, so `unbound=applied` is expected until a mind dispositions it; the reading monitors recurrence. Because the discover and senses panes count a `drift` reading against their verdict, this one holds both at `UNKNOWN` while it reads `drift`. An empty, missing or unreadable patch store reads `UNKNOWN` (`unreadable=<file>`), not a clean bill.
- The `sense.runtime.pin-lag` reading counts the commits between the runtime pin and the checkout HEAD and the age of the oldest unactivated commit (`pin=<sha> head=<sha> lag=N age=<age>`, or `lag=0` when they are equal). It makes the pin-versus-checkout gap described under [Advance the core plant's own runtime pin](#advance-the-core-plants-own-runtime-pin) measurable rather than assumed. A pin that is not an ancestor of HEAD (a rebase or a foreign checkout), a missing or unreadable pin file, or a git failure reads `UNKNOWN`, never `lag=0`.
- The `sense.coord.dm-disposition-age` reading measures how long addressed DMs wait for a disposition: over the last 6 hours a DM is dispositioned when its target replies by DM to the sender (`reply_edge`), records a wall outcome whose body cites the DM's seq token (`seq_credit`), or records any later wall outcome (`wall_join`, credited at its first tape citation) — `dms=N reply_edge=R seq_credit=S credit_only=C wall_only=W dispositioned=D open=K stall=G p50=<m>m p90=<m>m oldest_open=<role>:<age>`. The decomposition makes the construct visible: `credit_only` counts DMs the seq rule credits beyond a reply edge, `wall_only` the ones the wall join credits beyond a reply edge. `stall` is the age-gated `open` — only DMs at least 30 minutes old — so a DM recorded minutes before the window end is not read as a stall. The wall join separates a real stall from correlated silence — a mind busy on wall work, not ignoring the message. A missing or unreadable tape or outcome store reads `UNKNOWN`.
- The `sense.seed.settle-result-mix` reading reports the mix of settlement results from the tape's own receipts: `n=<N> verified=<V> changed=<C> blocked=<B> other=<O> incidents=<I> since_last=<S>` over the trailing 100 settlements. `incidents` clusters the window's `blocked` receipts at a 60-minute gap; `since_last` counts settlements after the most recent `blocked` on the whole tape, or `none` when none is recorded; `other` counts a settled result outside the known three. It is `verified` when the window holds no `blocked`, `drift` when one does, and `unknown` when the tape is absent or unreadable. A `seed yield` entry with no settled line is not a settlement.
- The `sense.ledger.evidence-binding` reading checks that post-clause wall outcomes bind write-once evidence: a `wall-outcome` record binds its per-outcome evidence file with `payload.evidence.{path,sha256}` at record time, and the clause (`docs/wall-coordination.md`) prescribes three independent requirements — the path is under `artifacts/` (A: the stored path is resolved before the test, so a `..` segment cannot pass lexically), the file is unedited (B: the bound digest still matches), and the path is cited by no other outcome (D: distinct stored path strings). The sense re-hashes every tape-referenced post-clause outcome's evidence and reports two windows: the latest outcome per role (`latest_roles`/`latest_bad`, live compliance) and the whole post-clause set (`bound`/`all_bad`, standing audit — the store is append-only, so a repaired violation stays flagged) — `bound=N latest_roles=R latest_bad=B all_bad=M violating=<role>@<iso>:<basename>(<classes>) …`. The reading is `drift` when any checked outcome violates, `verified` when none do; a missing or unreadable tape or outcome store, or no post-clause outcome, reads `UNKNOWN`. The standing audit never empties — its A and D violations are permanent in an append-only store — so the reading stays `drift` and, like `dv-binding`, counts against the discover and senses verdicts, holding both at `UNKNOWN`.
- The `sense.repo.branch-inventory` reading checks the checkout against the single-`main` regime: `local=<n> extra=<names> remote=<n> extra_remote=<names> worktrees=<n> nonmain=<k> dirty=<j> strays=<up to three> nonmain_names=<up to three> dirty_names=<up to three>` (`none` for an empty list, `strays_more=+N`/`nonmain_more=+N`/`dirty_more=+N` when truncated). It is `verified` when `main` is the only local and remote head and every non-main worktree is under `SITE/worktrees/`; `drift` when a local or remote ref other than `main` exists, a release worktree sits at a commit `main` cannot reach, or a non-main worktree lies outside the sanctioned `SITE/worktrees/` area. `dirty` counts non-release worktrees with uncommitted bytes; release snapshots are exempt. `strays` names the shared checkout's untracked, non-ignored entries — a local note or evidence file written against a repo-root-relative path lands there, where a broad `git add` would commit it — and, like `dirty`, does not by itself change the state. A workspace that is not a Git repository, or an unreadable remote, reads `UNKNOWN`.

`seed status` reports tape identities: `pending` is an unsettled wake; `yield` is the last settled wake; `clear` is the settled wake whose process has been rotated; `continue` records a continuation request. They are entry/wake identities, not durations. `none` means no such recorded event.

For a persistent plant, use the actual units in `SITE/health/services.json`. Default role units are named from the session:

```bash
ROLE='genome'
systemctl --user status "$SESSION-$ROLE.service" "$SESSION-health.service"
journalctl --user -u "$SESSION-$ROLE.service" -n 100 --no-pager
```

The core plant also has a `SESSION-coordination.service` release follower. Manual setups have no newly installed resident services, so absence of those units is not itself a failed setup: their deliberately empty `services.json` makes the health pane and its durable report read `RED`/`FAIL` for `services-manifest-empty`. Conversely, `--no-services` does not deactivate units from an earlier persistent plant.
The health dashboard's `STATE: UNKNOWN — services unavailable: Failed to connect to bus: No medium found` means its renderer could not reach the user's systemd bus; it does not prove that a listed unit is down. Diagnose unit states as the same user with a reachable user bus (usually `XDG_RUNTIME_DIR=/run/user/$(id -u)`) before attributing UNKNOWN to service health. The dashboard supplies this runtime directory only when unset; genuine query failures remain UNKNOWN.
Rendered `SYSTEM ZERO` check reports retain their verdict text and gain a
`STALE` marker when the report file is older than 900 seconds, its mtime is
ahead of the wall clock (a backward clock step is not freshness), or its mtime
cannot be read. A failing health report names the check that failed and when
it was computed, so a latched RED stays diagnosable: its window predicates
compare live windows with `health/windows.json` (`windows-missing`,
`windows-extra`) and flag a dead renderer or a chartered mind's lower pane
(`windows-dead`), which can wedge while its renderer lease stays green. Roles
without a deterministic report producer show that disposition without attaching
legacy report files. A `tmux` read that fails or times out is reported as
`pane read failed` and reads UNKNOWN rather than claiming the whole manifest is
missing; only a readable pane list can raise `windows-missing` or
`windows-extra`.

### Doctor is a check with a repair side effect

`doctor` checks feed framing, executable local programs, the site's Git boundary, and optional adapters. It can also **rebuild missing or mismatched handoff projections from the tape**. It is not strictly read-only:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor
"$SITE/bin/mishe-tauftauf" --home "$SITE" doctor \
  --panes --session "$SESSION"
```

Pass `--sweep-orphans` to also kill leaked test tmux sessions. Tests create
sessions named `mishe-tauftauf-test-{pid}` or `mishe-tauftauf-test-{pid}-<word>`
in the shared tmux server; a hard-killed pytest leaks the session because its
cleanup never runs. The sweep reads the creating pid from the session name and
kills the session when that pid is dead.

The pane check samples the pane in bounded windows (11 seconds each by default, set by `--pane-wait`; an empty pane is retried before it holds) and names the failure: a top pane holds as `pane-missing`, `pane-empty` (a renderer process exists but printed no first frame), `pane-fallback` (unreadable pane or no owned lease) or `pane-frozen` (renderer stopped/dead, or its lease did not advance across two windows — a `--pane-wait` shorter than the refresh interval reports healthy panes this way); each built-in resident's lower pane is checked separately as `mind-pane-missing` or `mind-pane-dead`. Optional `laya` being unavailable is not a missing dependency for the default plant. Do not add hosted-adapter live flags unless you intend to invoke those services.
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
| `held ... unsettled` | The supervisor holds an unsettled turn before redelivering it: 600 seconds after a recorded delivery, 60 seconds after an attempt that recorded none. Let the running agent finish. |
| `held ... mind busy` or `HOLD ... resident mind busy` | Readiness is not established. Check the agent screen and, for another engine, its probe. |
| `yield` differs from `clear`, with no pending wake | A settled mind awaits rotation. Check the grace interval, handoff, and stable idle prompt. |
| A resident pane is dead | A running `seed run` supervisor recreates dead panes. Check why the process exited and whether its supervisor is active. |
| A live mind pane has fresh `sense.mind.wedge-suspect` evidence bound to its current PID | `seed run` respawns that pane on its next supervisor pass, using the newest scan by its `created` timestamp. Equal-timestamp scans are ambiguous and do not trigger recovery. Stale, malformed, dead-pane, or PID-mismatched evidence does not trigger recovery; the unsettled wake remains available for the relaunched mind. |
| Supervisor journal names paths from an old release | Repairing checkout source has not necessarily changed the source loaded by this service. Inspect its unit before restarting it. |
| `CLEAR STALL` / `STALE PEND` on the witness pane | Deterministic witness checks, not mind reports: a settled wake left uncleared past 120 s, or a pending wake left unsettled past 600 s. A live, mid-turn mind reads `CLEAR STALL: HELD` — the supervisor is holding the rotation, not failing — or `STALE PEND: HELD`, a turn still running, while an idle mind, a missing or dead pane, a foreign engine, an empty session, or an unreadable probe keeps the fault. A live pane that the newest discovery scan (at most 30 minutes old) names in `sense.mind.wedge-suspect` *and whose reported pid is this session's pane for that role* instead reads `WEDGE-SUSPECT` with that reading's `chain`/`span`/`cause`, because an open provider-error retry chain never ends the turn and the supervisor cannot settle the wake; the pane verdict then reads `FAIL witness wedge-suspect mind <roles> needs checked recovery`. The reading's `provider-error` rule, which also fires on a pane still streaming a long turn, is reported on the `HELD` line as `wedge-suspect=…` and asserts nothing. Missing, unreadable, aged-out or oddly shaped wedge evidence keeps the `HELD` line. Each RED line names the owner (`health`, or `witness` when health is the stalled role). Reproduce before repair. |

An unsettled wake is redelivered only after the hold above and only when readiness says the mind is idle. The wake and handoff remain available through recovery. Codex recognition rejects working/executing states; OMP recognizes idle prompts including tokens such as `INSERT y >` and rejects working spinners; OpenCode recognizes its idle input box — the `Ask anything` placeholder on an empty session, or the same box with an empty line above its footer once the session has history — and rejects the running-turn `esc interrupt` status and working spinners. A custom engine's probe must return zero only when safe; see [agent readiness](getting-started.md#use-another-agent-command).

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

Only after verifying a settled wake and a stable idle mind:

```bash
"$SITE/bin/mishe-tauftauf" --home "$SITE" seed clear \
  --session "$SESSION" --slug "$ROLE"
```

This **rotates the idle agent process in its existing lower pane**. It preserves the handoff on disk and records the clear; the next real wake carries the charter into the fresh process, and the wall carries the next step. It refuses an unsettled wake, a dead pane, or an unstable/busy mind. Treat a refusal as diagnostic evidence, not an obstacle to bypass.

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

The core plant's minds and services import the release named in
`SITE/health/runtime-release.json`, not the shared checkout, so a committed
kernel change becomes running code only when that pin moves. The evidence panes
render from the root their own `top-pains/<role>` scripts import — the pin, the
checkout when unpinned, or a separate runtime snapshot a wall-mode site names in
`coordination-mode.json` — so a respawn does not repoint a pane whose script
selects another root ([wall coordination](wall-coordination.md)). Point the core at a
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
reads `MATCH` when every covered service imports the pin, `DECLARED` when the
only divergence is the release coordinator's intended checkout root (the core
plant's own reading, which keeps the state line GREEN), and `DRIFT` for any other
service still importing another root. A missing or invalid `health/services.json`
leaves the service roots unread, so the line reports `services=UNKNOWN`, not
`MATCH`, `DECLARED` or `DRIFT`. Check the actual consumer and its live
pane. Moving the pin is not a push or a CI result; keep source, CI and running
code as separate evidence.

A wall-mode site's panes render from the snapshot named in
`coordination-mode.json` (`runtime`), not from the pin, so moving the pin alone
leaves those renderer bytes stale: the panes show old logic while the services
import the new release. Rebind the snapshot's package to the release before or
with the advance — copy `<release>/src/mishe_tauftauf/` over
`<snapshot>/src/mishe_tauftauf/`, after saving the old package under the ignored
site — and let `sense.runtime.renderer-coverage` confirm it: the sense hashes
each renderer's import closure in the renderer root and in the pin and reports
`drift` naming every module that differs, so a partial rebind is visible rather
than silent. The snapshot's files outside those closures are inert to the panes.
A patch that must survive a pin advance belongs on `main`; the rebind replaces
the snapshot's patched bytes with the release's.

The launcher writes the pin, then reconciles the covered services onto it,
before it runs its own discovery scan, so the launcher's own post-return reading
is the reconciled state, not the swap transient. A scan that samples during the
pin-write→reconcile window — a concurrent renewal or an explicit scan — can still
report every session unit stale against the new pin: the swap transient, not
persistent drift. Re-read the `RUNTIME` line and the across-sites sample
(`sense.runtime.drift-across-sites`) after the launcher returns, and treat a
stale reading as drift only when it was not sampled during that window.

The across-sites drift sense also reports `UNKNOWN` with `uninspectable=<unit>`
when a running session unit's import root cannot be discovered from systemd,
its main process, or the main process's direct children. No root means the pin
comparison is unevidenced, not clean.
The same `UNKNOWN` reading names a foreign consumer of the checkout as
`foreign=<unit>@<checkout>` when the unit's import root or its `ExecStart` script
names the plant, so a live consumer of the moving source is visible rather than
counted as an anonymous gap.
The scanning site's own units are read even when the registry lists no other
sites, so a unit its manifest omits is still compared with its own pin rather
than left unnamed. An empty site list is therefore not itself `UNKNOWN` as long
as the scanning plant's own session can be named. An unreadable registry, a
non-list `sites` value, or a registry that names no site with both a home and a
session is `UNKNOWN`.

The service comparison covers only the roots the listed services import. A pane
renderer that exports its own `PYTHONPATH` runs from a root no manifest names,
so a stale snapshot there would show old logic in the pane while the dashboard
still read verified. The renderer coverage sense
(`sense.runtime.renderer-coverage`) reads each Top Pain's exported root and
entry module, hashes that module's package import closure in the renderer root
and in the pin, and reports `drift` naming the modules that differ or are
missing on either side; the sample leads with that list, deduplicated and capped
at two rows (`drift=<entry>=<modules> … +Nmore`, since rows sharing a root,
entry and module set are one violation). The state reads `drift` even when
another renderer's
root is unread, with the unread names kept in the sample
(`script_unknown=…`), so a definite violation is never demoted to `unknown`
by an unread sibling. The sample opens with `renderers=N`, the renderers it
checked. A renderer that does not export `PYTHONPATH` inherits its root from the
pane watcher's environment; the sense reads that root from the watcher process's
live environment (falling back to the pane's start command) and reports
`unknown`, naming the role in `inherited_unknown`, when it cannot. An export that
is conditional or built from the inherited `$PYTHONPATH` may not decide the
root, so it is `unknown` too, named `conditional_unknown`. The identity records
each renderer's effective root, so an inherited renderer that resolved shows
the root the pane environment supplied, letting a reader distinguish one that
resolved to the pin from one that resolved to a different root that happens to
match. An export that names
no usable root at all (an empty `PYTHONPATH` or a bare separator) leaves the
renderer's effective root unread, so it is `unknown` too, named
`export_unknown`. Renderers that run no package module are named `uncovered`,
so the sense shows its scope rather than only the renderers it covers. An
uncovered renderer can still execute package code through a site-home script
(`site / "X.py"` or `from X import`); the sense follows one level of that chain
and names it `indirect_package=<role>:mishe_tauftauf.<module>`, hashing the
script's closure against the pin from the root the pane environment resolves. An
indirect renderer whose script root cannot be resolved — no live pane runs it —
is `unknown`, named `script_unknown=<role>`, and a root other than the pin reads
`drift` even when the closure matches. The
pane's cwd is checked for a
`mishe_tauftauf/` package that would shadow the `PYTHONPATH` root, since
`python -m` inserts the cwd before `PYTHONPATH` in `sys.path`. `unavailable`
means no renderer runs a package module at all.
The covered services' imported roots are also compared with the pin's sensor
set by `sense.runtime.sensor-coverage`: it parses each root's `discovery.py`
for the `sense.*` ids that root can emit and names `missing=` or `added=` per
root against the pin. The coordinator's declared checkout root is exempt from
`added=` only (it may lead the pin by a commit); `missing=` on that root still
reads drift. A release that silently drops or adds a reading —
changing what the plant can observe — cannot pass merely because its import
root still matches. A root that cannot be parsed adds `unreadable=<root>` and
reads `drift`; no imported root, an all-unreadable root set, or an unreadable
pin module is `UNKNOWN`, not a clean bill.

The journal unit-failure-count sense (`sense.journal.unit-failure-count`) reads
the system journal for `Failed with result '<class>'` messages from systemd,
grouping raw counts by failed unit and class over a fixed 600-second window.
The count is plant-scoped by session prefix: a unit counts as plant when its
name starts with the session prefix this site's `.seed-raised` receipt records
(the same prefix the drift-across-sites sense uses to attribute units) or with
`tmux-spawn-` (the cgroups its tmux server creates for quick-exit panes); a unit
matching neither is disclosed as `foreign=<name>:<count>` and excluded from the
count, so another site's units or another node's test harness cannot read as a
plant failure. When the plant's session cannot be read the reading is `unknown`
rather than attributing an unowned unit to the plant.
For each failed unit systemd still reports as loaded, the sample also carries
restart context, `<unit>:restarts=N,active=<state>`, so a unit that exits
nonzero by design and recovers (`active=running`) reads differently from a
crash-loop (`active=failed`); the context is supplementary and omitted when
`systemctl` is unavailable or the unit is no longer loaded — an exited
transient scope reads `not-found`, leaving `unit_context` empty.
The kernel-error-count sense (`-k -p err`) never sees these records because
they are logged at warning priority, not as kernel messages. The kernel sense
groups its entries by each message's reporting source (the text before the
first `': '`), so a window dominated by one repeating driver message cannot be
read as a fault count; the sample names the largest sources and folds the rest
into `other`, and `classes` carries the full breakdown. The unit-failure window
matches the scan renewal threshold, so consecutive scans cover the boot without
a gap.

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
