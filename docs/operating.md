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
Over-growth in a presentation lane is normally a stale-data signal: the owning
mind prunes superseded lines first and records the prune on its own wall. Wall
over-growth is the wall author's own prune, bounded by `wall write` (16384 bytes /
200 lines) with every accepted write audited to `logs/wall-write-audit.jsonl`;
Refused writes (stale staging anchor, peer copy) also append a durable entry
with role, source, wall, both mtimes, and the refusal reason.
top-pane over-growth belongs to the renderer's role (health for the health
dashboard, the operator-view supervisor for the operator dashboard). A new window
requires a `health/windows.json` manifest entry — WINDOWS audits
missing/extra/dead — and the entry plus a wall note record the decision.
Delivered context is bounded at 1 MiB per wake prompt, enforced at read time.

The launcher automatically searches for resident markers in `.mishe-seed` and `.mishe-tauftauf`, reuses a sole resident site, and reads its recorded session. Multiple candidates require explicit `--home` and `--session`. A custom-named site should always be addressed explicitly when replanting; it is not part of that automatic search.

Both wrapper names reach the same CLI: `mishe` is the standard name used by mind panes and new instructions, while the historical long name `mishe-tauftauf` remains available for compatibility. For operator commands, keep passing `--home "$SITE"`. The CLI otherwise chooses `MISHE_SEED_HOME`, then the older `MISHE_TAUFTAUF_HOME` environment variable, then relative `.mishe-tauftauf`. Fresh resident mind launches export the canonical `MISHE_SEED_HOME` and prepend `SITE/bin` to `PATH`, so the plain `mishe` command resolves there; an ordinary shell or an older process may have neither. A plausible path is not the same thing as your plant.

When `MISHE_SEED_HOME` is set, it binds the CLI and validated library writers to that resolved site. An explicit `--home` naming a different site is rejected, including for initialization. Delivery snapshots and the session watcher validate before creating home-relative directories. This cannot constrain raw out-of-band filesystem writes; the ghost-home detector remains required for those.

## Inspect the running plant

These commands inspect the tape, sample, and live panes without sending a new task:

```bash
tmux list-windows -t "$SESSION"
tmux list-panes -s -t "$SESSION" \
  -F '#{window_name}.#{pane_index} dead=#{pane_dead} pid=#{pane_pid}'
tmux capture-pane -p -t "$SESSION:health.0"
tmux capture-pane -p -t "$SESSION:genome.0"
"$SITE/bin/mishe" --home "$SITE" seed status --slug genome
"$SITE/bin/mishe" --home "$SITE" wall show --owner genome
"$SITE/bin/mishe" --home "$SITE" discover show
"$SITE/bin/mishe" --home "$SITE" discover scan
"$SITE/bin/mishe" --home "$SITE" pain list
"$SITE/bin/mishe" --home "$SITE" access list
```

`discover show` prints the latest published scan; `discover scan` takes one new
bounded, read-only sample and publishes it, so an acquisition is an action
rather than an inspection. The standard genome and health panes show
deterministic CI, runtime, service and patch observations; the witness pane
shows CI plus its coordination checks: planted windows, clear-stall and
stale-pend, open tasks, and chat rate. Chat rate is UNKNOWN when the canonical
chat feed cannot be read; a quiet rolling window is not proof that a producer
fault is resolved. The latest-chat display is bounded to the final 20 rendered
lines; deterministic checks still evaluate the canonical feed. Each is followed
by the mind's edited wall.

Genome inspects unlanded outside edits with `git status --short` and reviews each path's diff before adopting, reconciling or retiring it. Preserve unrelated edits and coordinate with existing owners; delivery follows the single-`main` authoring, CI and live-consumer checks above.

For an ownership-checked reading of the actual top pane, not a newly rendered approximation:

```bash
"$SITE/bin/mishe" --home "$SITE" pain read health \
  --launcher tmux --session "$SESSION"
```

`pain watch` publishes the complete current report, including wake and lease, to
`SITE/dashboards/ROLE.json`. Minds and supervisors read that complete report.
On a terminal, the watcher reserves space for the footer and rotates ordered
`VIEW n/N` continuations instead of scrolling facts off screen. Each page names
its current body digest and the full-dashboard command. Different digests are
different acquisitions, not an immutable multi-page sample. Source timestamps
and freshness do not advance merely because another page is displayed.
Widths use terminal cells and whole Unicode graphemes; cursor-control bytes are
shown as escapes without changing the canonical report. A viewport too small
for a fact plus its instructions and footer displays UNKNOWN without a new lease.
Tmux capture joins soft-wrapped footer lines so a narrow pane still exposes the
complete lease; hard-wrapped fact rows remain ordered. Nonterminal output stays
complete. Use the canonical read below when comparing full facts or provenance.
Surface checks must acquire the relevant measured row across reachable pages;
a single continuation need not contain it. Match the source's complete measured
value, not a substring that also appears in desired state or unrelated headlines.
Check lease advancement separately from sensor transitions.
Headless `pain read` rendering preserves a caller's nonempty `MISHE_SEED_SESSION`;
when absent or empty, it uses the session recorded in `SITE/.seed-raised` when
available. Without a readable recorded session it leaves the variable unset.
Read it directly, rather than re-rendering or scraping the pane:

```bash
"$SITE/bin/mishe" --home "$SITE" pain read health --launcher dashboard
```

It fails `UNKNOWN` when the frame is missing, older than 30 seconds, corrupt, or
recorded for another site, so a stale or foreign frame is not read as current.

Read the tape in full or follow new entries:

```bash
"$SITE/bin/mishe" --home "$SITE" feed
tail -f "$SITE/chat.log"
```

Ctrl-c stops `tail`, not the plant. The live panels may summarize only recent entries; the full feed holds the explanation, evidence path, and next owner or action.

### Read the space readings

Four sources are read-only readings rather than collections. Take one
published sample, or acquire a new bounded one:

```bash
"$SITE/bin/mishe" --home "$SITE" discover show
"$SITE/bin/mishe" --home "$SITE" discover scan
```

`discover show` prints the latest published scan, which carries the Note3 light
reading as `sense.space.light` and this host's Bluetooth and Wi-Fi adapter
state as `sense.space.host-radio`. The checkout also reads the linked Android
Body consumer's Redmi10 state as `sense.body.redmi10`, and its Senses renderer
refreshes that retained file on each render. The reader and the core Health alert
are intended to load the same served pin, but a runtime-drift observation means
that alignment is not established; treat the consumer row and alert as UNKNOWN
until the loaded release is checked. A published scan therefore carries the
consumer row only when that pin alignment is observed. `discover scan` takes one
new sample instead of reading a published one,
so acquiring is an action rather than an inspection.
When an Android Body site is registered, the Health check directly pairs the
current Redmi consumer reading with `sense.net.tailnet-peer`: stale poll or all
three channels stale plus peer `Online` or `Active` produces a RED named cause;
offline, inactive, absent or unreadable peer evidence leaves the loss verdict
UNKNOWN. The result appears on the Health pane's `SYSTEM ZERO` line at its
bounded cadence (at most 600 seconds). This does not establish whether the
phone, app or sensor caused the loss. Ethernet and tailnet status remain
separate from space sensing; the camera fault surface is also read into Health
and reaches `SYSTEM ZERO` as a named cause.

The light reading is capped at 30 seconds, or the producer's shorter validity,
and a missing, unreadable, delayed or expired boundary stays UNKNOWN instead
of serving a last-known-good lux. The phone's clock runs behind the host by
design; the measured quantity is a span (host arrival minus phone stamp =
latency + skew), so the phone-side skew is bounded above by the smallest
observed span rather than read directly. The checkout's rule therefore
compares the host-side receipt, publication and poll stamps and never the
phone's event epoch (`dc1b963`). A pane loading an older pin still carries
the previous rule: measured on 2026-10-13T13:49Z, one boundary file read
`verified fresh-clock-conditional`
from the checkout source and `unknown stale-original-evidence` from pin
`fcaa2aa`, so a fresh reading can read stale on a deployed pane.
`sense.runtime.pin-lag` names that gap (`pin=<sha> head=<sha> lag=N age=…`).

The Redmi10 line reads the linked Android Body site's retained consumer JSON,
not a second phone collector. It shows battery, light and acceleration only
when the consumer receipt is within 30 seconds; the displayed phone-sample and
host-receipt times remain separate, and their clock split is UNKNOWN without a
measured clock-domain result. Missing, malformed, backlogged or stale consumer
state reads UNKNOWN. Wi-Fi RSSI is a connected-link observation, not proximity;
the BLE scan error is shown as source evidence, not silently repaired. The
acceleration magnitude includes gravity and does not establish human motion,
continuous carrying or presence.

A published historical transition is retained only when the reader can
revalidate it; otherwise the pane names the failed predicate after
`historical transition not retained:`. `event-window-expired` is the routine
class — the transition is older than the producer's own validity — while
`event-identity-mismatch`, `event-receipt-before-phone`, `event-phone-span`,
`event-receipt-order`, `event-window-future`, `event-bucket-mismatch` and
`event-malformed-shape` each name a publisher contradiction. One string for
all of them made a new defect read exactly like routine expiry.

A lux value is a sensor reading, not room occupancy or human attendance, and
the camera surface reports faults, not a scene. The measured history and its
limits are in [how it works](how-it-works.md).

### Read the evidence, not just the color

- **GREEN/PASS** means the displayed check passed for its stated scope. It does not certify a whole application, model, or host.
- **RED/FAIL** means a check found a fault. Keep the failure visible while its owner reproduces and repairs it.
- **UNKNOWN** means evidence is missing, stale, unavailable, or conflicting. It is not a pass with a shy personality.
- The advancing `-- pane live ...` footer proves the renderer is alive. Compare captures a few seconds apart; an advancing lease does not erase a failed check.
- The health pane's `PANE LEASE:` line reads each resident renderer's footer lease and turns RED when one is older than `max(120 s, 6 × refresh)`; a frozen lease also makes the health verdict RED with cause `pane-lease`, while a missing or unreadable lease is UNKNOWN. A window with no renderer script (the operator shell, the log tail) carries no lease and is not a fault.
- The health pane's `CLEAR JOURNAL:` line names a wall-clear rotation abandoned past its stall budget: a journal left in `rotating`/`rotated` reads `clear-journal-stuck=<n>`, and a committed journal naming a settled turn the feed has not cleared reads `clear-journal-collision=<n>`; both turn the verdict RED with the named cause on `SYSTEM ZERO`, while an unreadable journal stays UNKNOWN rather than passing.
- `pane_dead=0` proves a process is alive, not that an agent is ready for a prompt or making progress.
- The `sense.mind.wedge-suspect` reading names a mind whose open omp continue chain is at least 3 `agent.continue scheduled` events of any source and at least 15 minutes with no completed turn (`agent_end maintenance routing` `stopReason="stop"`) between (`suspects=<window>(pid=…,session=<session>,chain=…,span=…min,src=<source>:<count>,cause=<cause>)`, or `suspects=0`). Each suspect also names the tmux `session` hosting its pane: two sites render on this one host and share every window name, so a window and pid alone cannot say which plant a suspect belongs to. Counting every source keeps a wedge that manifests as stream stalls or unexpected stops from escaping; `src=` shows the class breakdown and `cause` names the dominant class of the chain's `agent turn ended with provider error` records (`provider-error:<status>`, the HTTP status when the record or its message names one, `unknown` when neither names one, or `none` when the chain holds no such record), so a retry loop against a failing upstream reads differently from a hung pane. Rule R2 catches the instant-provider-error failure mode (e.g. a 403): the turn fails immediately with provider errors but no continue chain, so R cannot fire — SUSPECT when the chain is 0 with provider errors and the last error is at least 5 minutes old. An R2 suspect reads `window(pid=…,session=…,rule=provider-error,chain=0,error_age=…min,cause=provider-error:<status>)`, versus R's `window(pid=…,session=…,chain=…,span=…min,src=…,cause=…)`. The chain helper also reports `last_error_ts`/`error_count`/`foreign`, reset with the chain on a completed turn. It parses omp's session log under `~/.omp/logs/`, and a pane with no such log is not assessed; the sample ends with `panes=N with_log=M foreign=K`, and when no enumerated pane yields a log the reading is `UNKNOWN` rather than a clean bill, so a log-path or format change cannot pass as healthy. A failed pane enumeration is also `UNKNOWN`. A named suspect is a prompt to inspect that pane, not proof of a wedge.
  A provider error counts only when its record names the model the pane's own process runs, read from its live command line: an omp session log also carries the title generator's and subagents' turns, whose model differs, and a failure there does not mean the mind is wedged. An unreadable command line or a record with no model is still counted, so an unattributable failure is never hidden; the records excluded this way are counted as `foreign` in the coverage tier, so a name-spelling drift that would suppress own-model suspects is visible rather than silent. A `--model` with no usable value (the flag last, an empty `--model=`, or another option as its value) reads as an unreadable command line, so its records are counted too.
- The `sense.ledger.delivery-invariant` reading flags a boolean `delivery_verified` on a patch-record phase that is not a delivery outcome (`violations=<phase>:<count> …`, or `violations=0`). The accepted set — `applied`, `review-refused`, `reverted`, `revert-failed`, `revert-observation-failed` — is observed, not code-derived: `review-refused` stays because one frozen pre-2026-10-01 record carries a boolean dv there, and no current producer path produces that combination. The sense flags; it does not decide — a new legitimate delivery-outcome phase and a genuine collapse both read as "outside the set", so disposition stays with a mind. The verified sample ends with the coverage tier `records=N bool_dv=M`; an empty, missing or unreadable patch store reads `UNKNOWN` rather than a clean bill, with an unreadable store appending `unreadable=<file>`; the UNKNOWN branch still carries violations found in the readable records (trailing `violations=<phase>:<count>`), so a collapse co-occurring with a torn record is not dropped.
- The `sense.ledger.dv-binding` reading flags a patch record that claims `delivery_verified: true` without the `verification` dict that proves it (command, exit code, evidence): `unbound=<phase> records=N bool_dv=M`, or `unbound=0 …`, and the reading is `drift` when any record is unbound. Exactly one frozen record (`health-stale-pend-detection`, phase `applied`) carries that shape and no current producer path makes it, so `unbound=applied` is expected until a mind dispositions it; the reading monitors recurrence. Because the discover and senses panes count a `drift` reading against their verdict, this one holds both at `UNKNOWN` while it reads `drift`. An empty, missing or unreadable patch store reads `UNKNOWN` (`unreadable=<file>`), not a clean bill.
- The `sense.runtime.pin-lag` reading counts the commits between the runtime pin and the checkout HEAD and the age of the oldest unactivated commit (`pin=<sha> head=<sha> lag=N age=<age>`, or `lag=0` when they are equal). It makes the pin-versus-checkout gap described under [Advance the core plant's own runtime pin](#advance-the-core-plants-own-runtime-pin) measurable rather than assumed. The reading also stats the release directory the pin names: an absent directory reads `UNKNOWN` as `release-source-missing` with the path, because a process whose PYTHONPATH names a missing release silently falls back to the checkout's editable install; a record with no usable `source` string reads `pin release source unnamed` rather than skipping the check. A pin that is not an ancestor of HEAD (a rebase or a foreign checkout), a missing or unreadable pin file, or a git failure reads `UNKNOWN`, never `lag=0`.
- The `sense.coord.dm-disposition-age` reading measures how long addressed DMs wait for a disposition: over the last 6 hours a DM is dispositioned when its target replies by DM to the sender (`reply_edge`), records a wall outcome whose body cites the DM's seq token (`seq_credit`), or records any later wall outcome (`wall_join`, credited at its first tape citation) — `dms=N reply_edge=R seq_credit=S credit_only=C wall_only=W dispositioned=D open=K stall=G p50=<m>m p90=<m>m oldest_open=<role>:<age> broadcast_excluded=B nonmind_excluded=M`. The decomposition makes the construct visible: `credit_only` counts DMs the seq rule credits beyond a reply edge, `wall_only` the ones the wall join credits beyond a reply edge. `stall` is the age-gated `open` — only DMs at least 30 minutes old — so a DM recorded minutes before the window end is not read as a stall. `wall_join` is a broad activity/age proxy only; it proves neither that a target saw or semantically handled a particular DM nor a causal relation between the DM and later wall work. DMs to `chat` or `all` (broadcasts) and to targets outside the site's role set — a watcher, a feed or the human shell, such as `ci`, `operator-view`, `session-watch`, `silence-watch` and `operator` — are counted separately as `broadcast_excluded` and `nonmind_excluded` and left out of the stall estimate: neither class can ever reply or record a wall outcome, so counting them as resolver waits would hold the gate above zero for the whole window. The role set is the built-in roles plus the residents the site declares with a `charters/<slug>.md` charter and an executable `minds/<slug>` launcher, so a renamed or new role reads excluded until it is seated here again. A missing or unreadable tape or outcome store reads `UNKNOWN`.
- The `sense.seed.settle-result-mix` reading reports the mix of settlement results from the tape's own receipts: `n=<N> verified=<V> changed=<C> blocked=<B> other=<O> incidents=<I> since_last=<S>` over the trailing 100 settlements. `incidents` clusters the window's `blocked` receipts at a 60-minute gap; `since_last` counts settlements after the most recent `blocked` on the whole tape, or `none` when none is recorded; `other` counts a settled result outside the known three. It is `verified` when the window holds no `blocked`, `drift` when one does, and `unknown` when the tape is absent or unreadable. A `seed yield` entry with no settled line is not a settlement.
- The `sense.ledger.evidence-binding` reading checks that post-clause wall outcomes bind write-once evidence: a `wall-outcome` record binds its per-outcome evidence file with `payload.evidence.{path,sha256}` at record time, and the clause (`docs/wall-coordination.md`) prescribes four independent requirements — the path is under `artifacts/` (A: the stored path is resolved before the test, so a `..` segment cannot pass lexically), the file is unedited (B: the bound digest still matches), the bound file exists (C: a deleted bound file reads C — absence is flagged, not digest-compared), and the path is cited by no other outcome (D: distinct stored path strings). The sense re-hashes every tape-referenced post-clause outcome's evidence (`bound=`); `read_at=` stamps the sense's own read moment, so a reader can tell a stale live-window claim from a current one. It reports two windows: the latest outcome per role as live compliance (`latest_roles=`/`latest_bad=`, with `latest_bad_roles=` naming the non-compliant roles and their classes and `latest_bad_last=` their last outcome times) and the whole post-clause set as a standing audit (`all_bad=`, `violating=`), because the store is append-only and a repaired violation stays flagged. `count_excluded=` counts the retained-bytes rows (B) only — the class the trial's evidenced-outcome count drops — so a deleted bound file reads C in the standing audit without dropping from that count.
- The `sense.ledger.evidence-binding` sample's field order is the pane's visible-prefix order — the standing-audit cause (`all_bad=`, `count_excluded=`) first, then the live window, then `read_at=`, then the coverage tier — because the pane clips a sample to 120 cells and a drift verdict must carry its cause inside that prefix.
- The `sense.repo.branch-inventory` reading checks the checkout against the single-`main` regime: `local=<n> extra=<names> remote=<n> extra_remote=<names> worktrees=<n> nonmain=<k> dirty=<j> strays=<up to three> nonmain_names=<up to three> dirty_names=<up to three>` (`none` for an empty list, `strays_more=+N`/`nonmain_more=+N`/`dirty_more=+N` when truncated). It is `verified` when `main` is the only local and remote head and every non-main worktree is under `SITE/worktrees/`; `drift` when a local or remote ref other than `main` exists, a release worktree sits at a commit `main` cannot reach, or a non-main worktree lies outside the sanctioned `SITE/worktrees/` area. `dirty` counts non-release worktrees with uncommitted bytes; release snapshots are exempt. `strays` names the shared checkout's untracked, non-ignored entries — a local note or evidence file written against a repo-root-relative path lands there, where a broad `git add` would commit it — and, like `dirty`, does not by itself change the state. A workspace that is not a Git repository, an unreadable remote, or a failed status inspection of any listed non-release worktree reads `UNKNOWN`; a failed inspection does not establish drift or a benign absent worktree.
- The `sense.repo.release-retention` reading reports the plant-owned release-worktree accumulation against a retention bound: `worktrees=<n> live=<l> reclaimable=<r> legacy=<g> kept=<k> over=<o> oldest=<age>d created7d=<c> bytes=<size>` (`bytes=unread` when `du` cannot measure the tree). `coordination/site_sync.py` creates one detached worktree at `releases/<sha>` per green SHA applied to a linked site and prunes any release no live pin, unit or process still names after each sync cycle, so `branch-inventory`'s worktree count cannot show an unbounded leak. The reading classifies each `releases/` entry as live (named by a live process `PYTHONPATH` or an installed unit's `Environment`; the unit *files* are read because `systemctl show` exposes only running units) or reclaimable, and is `drift` when the unreferenced set exceeds `_RELEASE_RETENTION_KEEP` because a coordinator that cannot prune leaves a leak.

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

The camera fault surface has no reader of its own: `mesh-cam-watch` records a
class line and the latest artifact, and the UVC function can stall
intermittently — v4l2 read timeouts, kernel URB resubmit failures — with
nothing reading them. The health view reads that surface directly and prints a
`CAMERA:` line: artifact age against the bound `mesh-cam-watch` itself declares
(120 s), `CAPTURE-FAIL` onsets and kernel URB resubmit failures inside a
15-minute window, with the class turning RED at three capture fails or ten URB
resubmits in that window. A node declares its camera with `health/camera.json`,
so a
plant without a webcam skips the check instead of inheriting its neighbour's
surface; a declaration that cannot be read, or a source that cannot be read on
a declared node, reads UNKNOWN rather than PASS, because deleted monitoring
must stay visible. The class word is informational — a covered lens is not a
fault.
That `CAMERA:` line reaches the health check's own stdout, and the health
pane renders the generic wall view instead of that view, so the line's text
is written to a discarded stream rather than to a pane: measured at
2026-10-13T13:53Z on the live health frame, no `CAMERA:` row is rendered. The
reader-visible camera surface is the named cause the check publishes into its
durable report and the pane's `SYSTEM ZERO` line —
`camera-artifact-stale`, `camera-capture-fail=<n>`, `camera-kernel-urb=<n>` —
so a camera fault still reaches a reader as a RED cause with its own name,
while the artifact age and counts stay one step deeper than the pane.
Measured on this host at 06:40Z: `CAMERA: RED class=SEEING
artifact-age=0s capture-fails=3/900s kernel-urbs=13/900s`, with the artifact
refreshing on time and the lens seeing a scene. The recurrence signal is a RED
verdict naming a camera cause; the camera driver or firmware root cause is
unresolved, so the check is the continuing observation, not a fix. The same line
read `CAMERA: PASS class=SEEING artifact-age=2s capture-fails=0/900s
kernel-urbs=5/900s` at 07:10Z once the 15-minute window rolled past the
kernel's URB failures, which is a bounded recovery rather than a resolution.
Measured again at 07:53Z the line read `CAMERA: PASS class=SEEING
artifact-age=2s capture-fails=1/900s kernel-urbs=4/900s`, below the class
thresholds of three capture fails and ten kernel URB resubmits in the
900-second window; the kernel resubmits keep occurring (one uvcvideo error in
the last ten minutes on the 07:43Z scan), so it is still the window that moves
rather than the cause.
Measured again at 08:12Z under the promoted runtime pin the line read
`CAMERA: PASS class=SEEING artifact-age=1s capture-fails=0/900s
kernel-urbs=4/900s`: the kernel count still reading four after the
15-minute window turned over means the URB resubmits are still occurring
below the threshold (the 08:01Z and 08:02Z scans read five and seven uvcvideo
kernel errors in their last-ten-minute windows), which keeps the
window-versus-cause distinction open.
Measured again at 08:22Z, after that window had turned over a second time,
the line read `CAMERA: PASS class=SEEING artifact-age=4s capture-fails=0/900s
kernel-urbs=1/900s`. A count of one in a freshly opened window describes
that window's resubmits, not the stopping of the fault: the earlier
count-persistence observation is preserved rather than rewritten, and the
uvcvideo journal scans stay the continuing evidence that the resubmits are
produced.
Measured again at 08:35Z and 08:38Z the line read `CAMERA: RED
class=SEEING artifact-age=1s capture-fails=2/900s kernel-urbs=10/900s` and
then `artifact-age=2s capture-fails=2/900s kernel-urbs=10/900s`, with the
cause `camera-kernel-urb=10`: the freshly opened window that had counted one
reached the ten-resubmit threshold within minutes, so the resubmits recur
inside a single window rather than only across one. The driver or firmware
root cause stays unresolved, with health as the resolver and this `CAMERA:`
line as the continuing observation.
By 09:03Z the same line still read `CAMERA: RED class=SEEING
artifact-age=3s`, but the cause had changed: `capture-fails=4/900s
kernel-urbs=1/900s`, named `camera-capture-fail=4`. The kernel count had
fallen back to one resubmit in the window while the capture failures
crossed their own three-fault threshold, so the red reading persists with
the other sub-cause now tripping first. Which of the two productions leads,
and whether they share a driver or firmware cause, is unresolved; the
`CAMERA:` line stays the continuing observation and health the resolver.
At 09:32Z, under the promoted pin `03be433`, the line read `CAMERA: PASS
class=SEEING artifact-age=3s capture-fails=1/900s kernel-urbs=0/900s`: the
window had turned over and both counts fell below their thresholds, the capture
failures from four to one and the kernel count to zero. A bounded recovery, not
a repair — the window moved, not the cause — so the readings above stand and
the uvcvideo journal scans stay the continuing evidence that the resubmits are
produced. Read again at 09:52:42Z the line read `CAMERA: PASS class=SEEING
artifact-age=1s capture-fails=0/900s kernel-urbs=0/900s`, so the capture
failure that had tripped the 09:03Z reading aged out without recurring and the
recovery has held across a second window roll.

### Doctor is a check with a repair side effect

`doctor` checks feed framing, executable local programs, the site's Git boundary, and optional adapters. It can also **rebuild missing or mismatched handoff projections from the tape**. It is not strictly read-only:

```bash
"$SITE/bin/mishe" --home "$SITE" doctor
"$SITE/bin/mishe" --home "$SITE" doctor \
  --panes --session "$SESSION"
```

Pass `--sweep-orphans` to also kill leaked test tmux sessions. Tests create
sessions named `mishe-tauftauf-test-{pid}` or `mishe-tauftauf-test-{pid}-<word>`
in the shared tmux server; a hard-killed pytest leaks the session because its
cleanup never runs. The sweep reads the creating pid from the session name and
kills the session when that pid is dead.

The pane check samples the pane in bounded windows (11 seconds each by default, set by `--pane-wait`; an empty pane is retried before it holds) and names the failure: a top pane holds as `pane-missing`, `pane-empty` (a renderer process exists but printed no first frame), `pane-fallback` (unreadable pane or no owned lease) or `pane-frozen` (renderer stopped/dead, or its lease did not advance across two windows — a `--pane-wait` shorter than the refresh interval reports healthy panes this way); each built-in resident's lower pane is checked separately as `mind-pane-missing` or `mind-pane-dead`. Optional `laya` being unavailable is not a missing dependency for the default plant. Do not add hosted-adapter live flags unless you intend to invoke those services.

For a controlled renderer hang, wait for that invocation to acknowledge entry
into the blocking path before sampling its lease. A render already in flight
when the hang is requested can still finish and publish one advancing frame;
elapsed time since the request is not proof that rendering has stopped.
The optional witness analysis advisor asks a local Laya model which investigation to try; [observable analysis advice](analysis-advisor.md) covers its setup, feedback and handoff repeat check.

If doctor reports site files staged or tracked in Git, resolve that boundary before landing work. If it reports corrupt feed framing, preserve the tape and diagnose the reported entry rather than appending invented receipts or deleting history to turn the pane green.

## Recover a held wake or clear

The supervisor's `seed run` loop starts or recovers resident panes, delivers eligible wakes, and clears settled minds at an idle boundary. Generated role services use a five-second loop and a 30-second clear grace. A direct `seed run` uses a 15-second grace unless you supply another value.

A held result usually asks a specific question: is the wake still unsettled, is the agent busy, is the readiness probe failing, or is the supervisor loading an old runtime? Answer that question before trying a restart.

`seed run` and `seed follow` print a status when it changes, so a held state is not re-announced every loop. Silence after the first hold is expected; a direct `seed tick` still returns the current status.

When an observation window is missing rather than held — no top pane to read at all — `tmux start` recreates the owned session's windows and panes without starting any mind service:

```bash
"$SITE/bin/mishe" --home "$SITE" tmux start --session "$SESSION"
```

Confirm the session's `@mishe-tauftauf-home` marker is this site first; the command refuses a session another site owns. Mind panes themselves are the supervisor's business, so a raised window without a mind service is a pane repair, not a restored mind.

`seed init` and `seed start` are the launcher's per-role steps, available directly:

```bash
"$SITE/bin/mishe" --home "$SITE" seed init --slug "$ROLE" --engine-command 'codex'
"$SITE/bin/mishe" --home "$SITE" seed start --slug "$ROLE" --session "$SESSION"
```

`init` re-heals the site without erasing it: the CLI wrapper and doctrine/charter copies are refreshed only when stale, a missing mind launcher is written once from the engine command (an existing one is never healed), and the role's top-pain and service-unit files are written when absent. It requires a checkout worktree. `start` raises or reuses the owned session, re-heals `.seed-raised` from the live session identity, creates the role's two-pane window when missing, respawns dead or wedged panes, and restores pending work only when the mind is verifiably idle. Together they are what "the launcher reuses it" does per role; a single role can be re-raised without replanting the whole site.

### 1. Capture the state

Use the affected role, not necessarily `genome`:

```bash
ROLE='genome'
"$SITE/bin/mishe" --home "$SITE" seed status --slug "$ROLE"
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
| `held ... native gate not-ready` | The pane runs no TUI engine, so readiness is the site gate's answer (`checks/mind-ready/ROLE`), not a busy prompt. Install or repair the canonical probe (`python -m mishe_tauftauf.native_gate --home SITE --session SESSION --role ROLE --install`) and read the branch verdict at `checks/native-gate-ROLE.json`: RED names a live mind the gate rejects. |
| `yield` differs from `clear`, with no pending wake | A settled mind awaits rotation. Check the grace interval, handoff, and stable idle prompt. |
| A resident pane is dead | A running `seed run` supervisor recreates dead panes. Check why the process exited and whether its supervisor is active. |
| A live mind pane has fresh `sense.mind.wedge-suspect` evidence bound to its current PID | `seed run` respawns that pane on its next supervisor pass unless the suspect's rule is `provider-error`: an R2 suspect is held, not respawned, because omp writes its title generator's and subagents' provider errors to the same session log and the witness already reports them on `HELD` without asserting a wedge. Supervisor and witness share whole-aggregate validation: a unique newest scan by `created`, timezone-aware and no more than 30 minutes old, exactly one verified wedge reading, and every suspect with a string window and integer (not boolean) PID. The published `session=` names which site a suspect belongs to; the respawn match itself still binds window and pid. Duplicate readings or any malformed suspect invalidate the whole aggregate; a valid matching row cannot override them. Future, stale, unreadable, ambiguous, dead-pane, or PID-mismatched evidence does not trigger recovery; the unsettled wake remains available for the relaunched mind. Check both loaded roots before relying on this shared policy: a renderer snapshot update does not update the supervisor pin. |
| Supervisor journal names paths from an old release | Repairing checkout source has not necessarily changed the source loaded by this service. Inspect its unit before restarting it. |
| `CLEAR STALL` / `STALE PEND` on the witness pane | Deterministic witness checks: a settled wake left uncleared past 120 s, or a pending wake left unsettled past 600 s. `HELD` observes a live mind's non-idle gate and the supervisor's hold, **not useful progress**. An idle mind, missing/dead pane, foreign engine, empty session or unreadable pane probe keeps the receipt fault RED. A fresh verified discovery reading with a suspect bound to this role's current pane PID renders `WEDGE-SUSPECT` and RED; the `provider-error` rule also matches streaming turns, so it is reported on `HELD` without asserting a wedge. The consumer selects the unique newest scan by `created`, not filename. `MIND MONITOR: UNKNOWN` names missing, unreadable, malformed, future-dated, stale (>30 min), nonverified or ambiguous aggregate evidence and routes inspection to health; it neither accuses nor restarts a busy mind and does not hide an independent RED. `AVAILABLE` means only a fresh verified aggregate reading: the producer supplies no covered-PID list or recognized/fresh per-role coverage, so an empty suspect list proves neither a healthy role nor useful progress. Each receipt RED names health (witness for health's stalled role). Reproduce before repair. |

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
"$SITE/bin/mishe" --home "$SITE" seed clear \
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
3. The target is a separate direct Git worktree with a matching site marker and an existing tmux session owned by that site (read on the site's own socket, so a foreign `TMUX_TMPDIR` on the coordinator's unit cannot hold a live site).
4. For a normal refresh, the target worktree also has no unlanded changes.

An already applied SHA without a recorded error is skipped. A hold is explained in the core `chat.log`; later eligible passes retry it. After refreshing, the coordinator checks the target's feed, expected top panes, live mind panes, advancing leases, and named services before recording the applied SHA. A successful later refresh closes that site's superseded `kernel-sync` hold in both the core and target feeds and writes a target-scoped completion marker. Coordination anomalies report RED if a matching hold remains open after that marker.

When the only changes since the last dispositioned or applied SHA are Markdown files under `docs/`, the coordinator records a separate dispositioned SHA without planting, verifying, or writing the linked target. Runtime changes, unknown paths, and cross-boundary renames still take the checked refresh path; a missing registration baseline also forces that path. A docs-only disposition never clears a prior hold or claims that the runtime applied that SHA.

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
these under `SITE/releases/<sha>` and reclaims any release that no live pin, unit or process still names — with the launcher:

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

A wall-mode site's panes render from whichever root their `top-pains/<role>`
scripts select — the pin, the checkout when unpinned, or a separate runtime
snapshot the site names in `coordination-mode.json` — so moving the pin alone
leaves those renderer bytes stale: the panes show old logic while the services
import the new release. Read what a pane actually loads before deciding: this
plant's seven role scripts exported the 2026-10-01 wall snapshot until
2026-10-11T03:41:46Z and export nothing since, inheriting the pane watcher's
release, so its renderers follow each promotion while `coordination-mode.json`
still names that snapshot for wall settings and `wall_patch` targets only. Where
a script still exports its own root, rebind the snapshot's package to the release
before or with the advance — copy `<release>/src/mishe_tauftauf/` over
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
counted as an anonymous gap. For the `ExecStart` script-text path, a unit
whose systemd `Description` names the plant is attributed to the plant instead,
so that class rests on that one read succeeding. An observed import root inside the checkout remains
a foreign consumer through the independent import-root path. Measured at
09:17:20Z the cleaner coordinator read
`foreign=mishe-cleaner-recovered.service@mishe-tauftauf` between two scans, at
09:16:46Z and 09:27:28Z, that read it `unattributed`, its script naming the
checkout while its description attributes it to the plant. It read
`unattributed=mishe-cleaner-recovered.service` again at 09:57Z, with the same
byte-set serving both answers, so that script-text claim follows the
description read and not the bytes; it does not negate an import-root observation.
An unattributed unit whose import root and whose
own declared `ExecStart`, `WorkingDirectory` and `Environment` paths name no
site's checkout or home is a host tool beside the sites, named
`host-scope=<unit>` without holding the reading at `UNKNOWN`; a unit whose
declaration cannot be read, or that declares a path inside a site, stays
unattributed.
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
The served reading resolves a role from whichever pane its lookup matches, so
a scratch or foreign pane whose start command names `pain watch <role>` can
redefine that role's renderer root, and a `drift` row naming a root no manifest
owns is a reader question before it is a pane defect — measured 04:25:58Z
against `/tmp/ci-2edab75`, a peer's CI worktree, with recovery to `verified` at
04:33:50Z. Reading the pane process's own `/proc/<pid>/environ` separates the
two cases. Repairing that lookup to resolve a role from the session the reading
home owns — a role whose panes all belong to another home stays unresolved
rather than guessed, while a lone pane in a session no home has claimed still
resolves — is committed in source; the served reading follows the pin, so check
the served pin's `discovery.py` for that lookup before relying on it.
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
"$SITE/bin/mishe" --home "$SITE" access list
"$SITE/bin/mishe" --home "$SITE" access check REQUEST_ID
```

Replace `REQUEST_ID` with a real ID. `access check` exits zero only for `granted`; pending and revoked decisions exit nonzero. An unknown ID is an error, not an implicit grant.

For example, a role requesting an unavailable local capability would append a scoped request like this; replace the task and paths with the actual need before using it:

```bash
"$SITE/bin/mishe" --home "$SITE" access request battery-read \
  --owner senses --task battery-observation --capability local-device-read \
  --unblocks /sys/class/power_supply \
  --reason 'Verify a battery reading for this local task.'
```

`--unblocks` can be repeated. Reusing the same ID with different scope is rejected. A request is not permission; the operator must review the exact scope before deciding.

The following commands **record an operator decision** for an existing request:

```bash
"$SITE/bin/mishe" --home "$SITE" access grant REQUEST_ID
# Or withdraw that scoped authority:
"$SITE/bin/mishe" --home "$SITE" access revoke REQUEST_ID
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
recovery; they do not provide another publication workflow. The `delivery` CLI
exposes only its read-only status view.

Running-code delivery uses reviewed snapshots, observable activation and the
tested recovery exercise in [wall coordination](wall-coordination.md).
Applied bytes alone are incomplete delivery.

## Live experiments

Within granted owned scope, try consequential ideas live. Name the prediction,
affected consumers, bounded observation and keep/revise/revert decision. Checks,
independent reading and recovery enable high-risk, high-reward work without a
routine approval gate. Preserve failed observations and delete superseded code;
Git history retains prior implementations instead of dormant feature flags.
