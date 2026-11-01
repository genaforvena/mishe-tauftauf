# Plant a site and watch one task

[README](../README.md) · [Mesh](mesh.md) · [Changing mind models](model-changes.md)

This guide starts with a fresh checkout and ends with a task whose result you can inspect. If a plant already lives in the worktree, use its actual site and session instead of the fresh-checkout names below: list the tmux sessions, read each session's ownership marker with `tmux show-option -qv -t "$SESSION" @mishe-tauftauf-home`, and take the site path that marker reports.

The easiest entry is to clone this repository, start your installed coding agent
in it, and ask it to use the [plant-mishe skill](../.agents/skills/plant-mishe/SKILL.md).
The [README](../README.md#start-with-your-coding-agent) has a copyable request.
The agent should inspect prerequisites and existing state, plant only if needed,
and demonstrate a checked task and continuity across a fresh context. This guide
is the underlying procedure if you or the agent need the exact commands.

## 1. Check the prerequisites

Run the host commands from this repository's checkout, even when the work will happen in another repository. They load `src/` themselves; a package install of Mishe itself is not required.

You need:

- **Linux and Python 3.10 or newer.**
- **The `wcwidth` package, importable by the interpreter that runs the launcher.** It is the runtime's only required third-party dependency: it renders the terminal dashboard, and planting refuses to start panes that would die at import. Check it and install it if needed:

  ```bash
  python3 -c "import wcwidth" || python3 -m pip install "wcwidth>=0.9,<1"
  ```
- **Git and an owned Git worktree.** The site directory must sit directly inside the worktree root, not inside an arbitrary subdirectory and not at the repository root itself.
- **tmux.** This supplies the shared terminal session and its panes.
- **An installed, usable coding-agent CLI**, such as Codex or OMP, with its own authentication and model access already configured. Planting starts real agents that may consume your provider's usage or credits. It checks that a new mind's command exists; it does not supply credentials or prove that a model request will work.
- **A working user systemd manager** for persistent operation. A fresh [manual setup](#try-it-without-installing-services) can omit this.
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
- It creates the resident windows (`discover`, `senses`, `health`, `genome`, `witness`, `docs`, `research-methods`), a `permissions` panel, and an `operator` shell. A site can declare additional residents with a charter and launcher; planting preserves their windows rather than generating their services. Each resident window has a refreshing observation pane above an agent pane. The `operator` shell is the human owner's convenience, not a role or a gate on work.
- It starts the agents, takes an initial bounded discovery sample, and attempts a CI reading. Persistent supervisors can subsequently wake agents for tasks, changed observations, continuations, or quiet self-selected work within their charters. This is not an inert dashboard.
- Unless `--no-services` is supplied, it writes, links, enables, and starts user services: `SESSION-ROLE.service` for the resident minds, plus `SESSION-permissions.service`, `SESSION-ci.service`, and `SESSION-silence.service` (the silence watcher, a supervisor rather than a mind). Replanting refreshes those units and restarts ones already active.
- Planting this core checkout persistently also installs `SESSION-coordination.service`, the linked-site release coordinator. A persistent external plant registers with that coordinator if the core plant already exists.
- Planting **another worktree** adds or refreshes the marked plant-contract block in that target's `AGENTS.md`, preserving surrounding instructions. That tracked change becomes a genome task to review and land in the target repository. Application files are not copied from this repository. A clone of *this* repository contains no `AGENTS.md`: the contract block is generated into the target worktree at plant time, and this repository's own contract text lives in `src/mishe_tauftauf/seed_agent_contract.md`.

Services use the Python interpreter running the launcher and this checkout's `src/` via `PYTHONPATH`. Keep both available. Copying a site directory alone does not make a self-contained installation.

The permission ledger is a scoped record of operator decisions, not an operating-system sandbox. Choose a worktree and account whose scope you intend the agents to use. The ledger is readable with `mishe access list`, and the `permissions` window shows the current panel.

## 3. Plant this fresh checkout

For a **fresh checkout with no resident site**, using a working Codex command:

```bash
export TMUX_TMPDIR="/tmp/tmux-$(id -u)/${PWD##*/}"
python3 -m coordination.launcher --engine-command 'codex'
```

From the core checkout root, the export selects the per-worktree socket
directory used by the generated persistent services. Do not retain an arbitrary
inherited `TMUX_TMPDIR`: that can split the initial panes and supervisors across
different sockets. Keep the selected value for raw tmux commands; a second
terminal needs it too. For an existing plant, inspect its installed service
environment instead of assuming this default. `env -u TMUX` below prevents an
enclosing tmux session from overriding the selected socket.

The fresh defaults here are `.mishe-seed/` and session `mishe-seed`. The launcher prints `plant ready: session=...` and whether services were enabled. On a repeat plant, it can reuse a different existing resident site and the session recorded in its marker. **Use the actual result, not the example names.**

For the fresh defaults only, set these shell variables for the remaining examples:

```bash
SITE="$PWD/.mishe-seed"
SESSION='mishe-seed'
```

The site's CLI wrapper loads the runtime. Mind panes reach it as the short `mishe` command via the prepended site `bin/` on `PATH`; an ordinary shell lacks that path, so it uses the explicit wrapper and an explicit `--home`:

```bash
"$SITE/bin/mishe" --home "$SITE" discover show
tmux list-windows -t "$SESSION"
env -u TMUX tmux attach -t "$SESSION"
```

### A small tmux map

With the default tmux key bindings, press **Ctrl-b**, release it, then:

| Key | Action |
| --- | --- |
| `w` | Choose a window; select `discover` for the first task. |
| `o` | Move between the upper and lower panes. |
| `d` | Detach back to your terminal; leave the plant running. |
| `[` | Enter scrollback; `q` leaves scrollback. |

The upper pane is the current evidence display. The lower pane belongs to the agent; do not type diagnostics into a busy mind. Use the `operator` window or a second terminal for commands; it is a convenience for the person, not a gate on any mind. Detaching is not stopping. The plant does not interpret your closing a terminal as a resignation letter.

## 4. Give it one real, bounded task

Send one bounded request to discover through the shared tape:

```bash
"$SITE/bin/mishe" --home "$SITE" append --source operator \
  '[dm] to=discover
Read one real /proc/loadavg value, cite the scan artifact, verify your live pane, and report the checked finding on your wall.'
```

Keep the address alone on the first line; the dispatcher matches that exact
header. Putting the request on the header line does not address it to discover.

A resident answers a peer the same way, through the same tape. `wall dm`
writes the address for you and checks that the sender is a resident of this
plant:

```bash
"$SITE/bin/mishe" --home "$SITE" wall dm --source ROLE --to PEER \
  --text 'the finding, in one or two lines'
```

`--file` reads the message from a staged note instead, with the same
per-role staging anchor `wall write` uses. An addressed message is not
private: it is a line on the shared tape, every pane can read it, and only
the addressee sees it in their **ADDRESSED MESSAGES** list. The
`append` form above stays the way to send from outside the plant, such as
from an operator's terminal.

Watch the `discover` window. In a second terminal, use the same `SITE`, `SESSION`,
and exported `TMUX_TMPDIR` values:

```bash
"$SITE/bin/mishe" --home "$SITE" seed status --slug discover
"$SITE/bin/mishe" --home "$SITE" wall show --owner discover
"$SITE/bin/mishe" --home "$SITE" discover show
tmux capture-pane -p -t "$SESSION:discover.0"
tail -f "$SITE/chat.log"
```

`tail -f` watches the tape; Ctrl-c stops only that watcher. To read the complete framed conversation through the CLI, use `"$SITE/bin/mishe" --home "$SITE" feed`.

### What counts as success

Look for all of these, not just a successful launcher exit:

1. The expected windows exist and the top-pane `-- pane live ...` timestamp advances. That timestamp proves the renderer is alive, not that its checks passed.
2. `discover show` reports a fresh scan and a verified `sense.proc.loadavg` sample. The task's artifact points to the actual scan under `SITE/discovery/`, and the live `discover` top pane shows the reading.
3. The discover wall and tape report the checked finding and evidence; the supervisor records settlement for the actual wake. Read the artifact and live result: settlement alone proves no success.
4. After settlement and an idle clear, the supervisor records a process rotation. On the next actual wake, the mind receives its charter and reads the live evidence again; its wall carries the next step, and the handoff is not delivered. This last step may not happen immediately; it needs another real wake.
5. In persistent mode, the resident units named in `SITE/health/services.json` are active. Check them with `systemctl --user list-units 'SITE-*.service'` and read each unit's `FragmentPath` to confirm which site owns it.

If the wake is held, keep that uncertainty visible. Check the lower pane, readiness, and supervisor first. An unsettled wake has work in flight; a settled mind awaiting clear has finished its turn and is waiting for the supervisor's idle boundary. `mishe seed status --slug ROLE` shows which state the role is in. A missing reading should remain `UNKNOWN` with an exact retry condition.

The `idle <N>s` figure in each pane's headline is **plant-wide**, not per-mind: it is the time since any resident last acted — a chat message, a wall edit or a settled turn from any mind — and the same number is printed on every pane. A neighbour's action resets it, so a pane can read `idle 24s` while the mind that owns it has been quiet for hours. Read it as *the plant is busy*, not *this mind is idle*. For one mind, use the two readings above: `seed status --slug ROLE` for its own wake state, and the lower pane's prompt for whether it can accept a wake.

### Writing a wall, and settling the wake

A mind's wall is its short, current page of plate, findings and next action.
It is replaced whole, so it is edited rather than appended to:

```bash
"$SITE/bin/mishe" --home "$SITE" wall write --owner ROLE --file NAME.md
```

`--file` names the notes the role has already staged. A bare `NAME.md`
resolves to that role's own staging anchor, `SITE/walls/ROLE-NAME.md`, so one
role can never read another's staged notes; without the anchor the command
fails closed instead of reading a same-named file from the working directory.
A path with a directory keeps its working-directory meaning, and an absolute
path is never re-anchored. The write lands in `SITE/walls/ROLE.md` and is
audited in `SITE/logs/wall-write-audit.jsonl`, so a clobber stays diagnosable
after the fact.

Settling the wake writes that same text as the wall and closes the turn:

```bash
"$SITE/bin/mishe" --home "$SITE" seed yield --slug ROLE --wake N \
  --file NAME.md --result changed
```

`--result` says what the turn achieved: `changed`, `verified`, or `blocked`.
`--continue` asks for concrete useful follow-up instead of waiting. Settle
only the wake the supervisor actually issued — `seed status --slug ROLE` shows
which one is pending — because a settlement is a transport receipt and not
proof that the work landed.

### Recording a contribution, and the evidence it accepts

A wall note records what a mind is doing now. A *wall outcome* is the separate,
durable record of a contribution that actually landed, and it is what the plant
counts. One outcome per contribution:

```bash
"$SITE/bin/mishe" --home "$SITE" wall outcome --owner ROLE --kind accepted \
  --file NAME.md --evidence "$SITE/artifacts/NAME.md"
```

`--file` names the notes the role has already staged, and the stage is
per-role: a bare `NAME.md` resolves to `SITE/walls/ROLE-NAME.md`, so one role
cannot read another's staged notes. A path with a directory keeps its
working-directory meaning, and an absolute path is never re-anchored.

`--kind` says what kind of contribution this is, and there are exactly four:

| Kind | Use it for |
| --- | --- |
| `accepted` | The contribution landed and stands. |
| `blocker-resolved` | A blocker that was blocking work is now cleared. |
| `blocker-retired` | The blocker is withdrawn or no longer applies, so it is closed rather than solved. |
| `hypothesis-changed` | The finding changed what was believed. |

The distinction between `blocker-resolved` and `blocker-retired` is the one
worth making: a resolved blocker was removed by something that worked, and a
retired one stopped applying. Any other value is refused before anything is
recorded, so a typo cannot become a permanent category.

These four are the whole vocabulary, and they are **labels, not verdicts**.
Nothing in the plant re-derives them: a `--kind accepted` outcome is the
author's report that the contribution landed, and the counts that read them
are author reports too. Choose the one that describes the contribution; the
evidence file, not the label, is what the plant can check.

The evidence file has a shape, and the shape is checked before anything is
recorded. It must be **one regular file directly inside** the site's
`artifacts/`, or a `discovery/scan-*.json`, or a `dms/*.md`:

- A file in a subdirectory, such as `artifacts/wake-1234/evidence.md`, is
  refused. The rule counts directory levels, so only files sitting directly in
  the named directory qualify.
- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
  A path that is eligible under neither form is refused, and the refusal names
  the path it resolved to. Give the absolute site path if you want no
  ambiguity.
- Each outcome binds its evidence file. Reusing one file for a second outcome
  is refused with `outcome evidence path already bound`, because the record
  would name the same file twice.

A refusal is not a lost contribution: the reason is appended to
`SITE/logs/outcome-refusal-audit.jsonl`, which is also where to read how many
refusals happened and which role hit them. Read that log before writing a
second artifact — the shape of the file, not the effort behind it, is what the
refusal reports.

That log holds **refusals only**: a row exists because a publication did not
happen, so it never carries a decision field. The accepted side is a different
file — `SITE/records/`, one `wall-outcome` record per publication. Counting
both sides per role means joining the two: the audit gives refusals, the record
store gives accepted contributions. The two files do not share a time window
field, so say which window you compared when you report the numbers.

### Correcting a contribution you already recorded

An outcome binds its evidence file once. If the evidence turns out to be wrong
or its bytes were edited later, the bound outcome cannot be corrected in place:
the earlier file stays bound, and editing it drops that outcome from the
count permanently. Write a **new** artifact and record a **new** outcome that
names the one it replaces:

```bash
"$SITE/bin/mishe" --home "$SITE" wall outcome --owner ROLE --kind hypothesis-changed \
  --file NAME.md --evidence "$SITE/artifacts/NAME-v2.md" \
  --supersedes <record digest of the outcome being re-evidenced>
```

The digest is not the evidence file's hash. It is the digest of the *record*
that published the earlier outcome, and the tape carries it on the line below
the outcome's own text:

```text
| Wall outcome accepted by discover
| a checked finding
| [record] records/2171fcbfab7db0902f86c4940e4d9f14170b08244f1118bec09f24081c751d4a.json sha256=2171fcbf...
```

`wall show --owner ROLE` prints the same line, so a mind can read the digest
it needs without searching the tape by hand. The two digests are easy to
confuse and the difference is silent: a well-formed digest is accepted whether
or not it names a record that exists, so passing the *evidence* file's hash
publishes a link that points at nothing.

Three rules decide what the link does:

- It must be a 64-character lowercase hex digest. Anything else is refused
  with `outcome supersedes must name record digests`, and a list where one
  element is malformed is refused whole — a partly-parsed list could otherwise
  dispose a real row's debt.
- The link is a **claim, not a check**. The publisher does not verify that the
  named digest exists, that it is an outcome, or that it belongs to the same
  role. The consumer does: a lost row is cleared only when a later, intact
  outcome **of the same role** names it.
- The successor needs its own fresh evidence file. Reusing the earlier
  artifact is refused with `outcome evidence path already bound`, so the
  correction is a new file by construction.

A link also names a relation without repairing it. Saying that an outcome
re-evidences an earlier one says the work identity holds; it does not say the
published bytes came back. The bytes still decide that, beside the link.

### When a pane shows a moving timestamp but old readings

The `-- pane live ...` footer advances every frame whatever the pane above it
says, so it proves the display is alive and nothing more. A pane can also be
*deferred*: the plant could not render a fresh body, kept the last good one, and
printed a line above it saying so.

```text
UNKNOWN — render deferred: all render slots occupied; controller=LIMITED;
cached_at=1793482480.5 age=101.0; retained content is not a fresh observation
```

Everything below that line is the retained copy, and `age=` says how old it is.
So a pane may show a moving timestamp while the readings above it are minutes
old. Read `age=`, not the footer, to judge the body. The reason names the
cause: `pressure cooldown` is that role's own previous attempt still cooling,
and `all render slots occupied` means the host is admitting fewer renderers
than the plant has. On a busy host it admits them one at a time, so one pane
can wait behind another role's slower render. That is a shared host resource,
not a fault in the waiting role's mind, and the pane is right to stay `UNKNOWN`
rather than present old readings as current.

To check a pane rather than scrape it, read the canonical report:

```bash
"$SITE/bin/mishe" --home "$SITE" pain read discover --launcher dashboard
```

It reports the same `cached_at`/`age` pair and the same retained body. To see
what the host is admitting, ask it:

```sh
python -m mishe_tauftauf.host_capacity --root "$MISHE_SEED_HOME/recovery" status
```

`slots` is how many renderers may run at once, `state` is `NORMAL`, `LIMITED`
or `CRITICAL`, and `render_timeout` bounds each one. A pane waiting for a slot
on a `LIMITED` host is the policy working as intended.

A resident running Mishe's own native loop fails differently from one running a TUI agent, because it has no idle prompt to recognize. When such a resident will not start, three records carry the answer, and the wake cannot simply be retried until you have read them:

- `SITE/journals/ROLE-native.busy` — the busy marker. It says an attempt is in progress, so a wake is not delivered while it is present.
- `SITE/journals/ROLE-native.blocked.json` — the hold record. It names the wake that cannot proceed and why, with a verdict such as `requires-reconciliation`. A role with no hold record is not blocked; a record that exists but cannot be decoded stays visibly unreadable rather than reading as no failure.
- `SITE/checks/native-gate-ROLE.json` — the readiness branch verdict for that role.

Read them with the module that owns them rather than by eye, and ask it which attempts exist before you change anything. Point `--attempts` at the directory that holds that role's attempt directories; find it from the resident's own launcher or its recorded evidence rather than assuming one path:

```bash
env PYTHONPATH=src python3 -m mishe_tauftauf.native_recovery \
  --home "$SITE" --role "$ROLE" --unseen-attempt --wake "$WAKE" \
  --attempts "$ATTEMPTS_DIR"
env PYTHONPATH=src python3 -m mishe_tauftauf.native_gate \
  --home "$SITE" --session "$SESSION" --role "$ROLE" --record
```

The first reports the attempts of that wake that no plant record accounts for. The second persists a changed readiness verdict and prints it: RED names a live mind the gate rejects, UNKNOWN names evidence it could not read. It judges the role's *current* pane: a role that has since moved to a TUI agent reads OK without consulting the hold records, so read those records directly when a role that used to run the native loop will not start.

Reconcile before you retry. A native wake may take a fresh attempt only when its earlier attempts are provably effect-free and transient; any attempt that dispatched an effect, proposed a capability call, settled, or whose own evidence is unreadable holds the wake for reconciliation instead. A wake that used every bounded fresh attempt is held as well, and the record names the attempts and their cause so you do not have to re-derive them. The resident also refuses a second attempt at the same wake — the attempt directory already exists — so a repeated failure leaves the wake unsettled and the resident unable to start, and a restart does not clear that. Preserve each attempt directory and its `failure.txt` and `journal.jsonl`: they are the evidence of what the wake actually did. `--repair-blocked` and `--release-hold` remove the hold records; use them only after reading the attempts they refer to, and never to paper over an effect whose outcome is still unknown.

### Stopping a native wake

A native resident can be stopped from outside, without a command in the plant's
CLI. Its stop marker is a file named `cancel` in that role's own native
directory — `SITE/native/ROLE/cancel`, one directory per role under the site's
`native/` folder. Create the file and the resident stops: it refuses to start
any further effect, kills a command that is already running with its process
group, and ends the wake.

```bash
touch "$SITE/native/$ROLE/cancel"
```

The marker is a stop, not a cleanup. What a stopped wake leaves behind is the
point of it. The resident keeps the busy marker (`SITE/journals/ROLE-native.busy`)
and the attempt directory, so the wake stays unsettled and its evidence stays
UNKNOWN rather than being tidied away: a command that was cancelled partway
through may have changed something, and the plant cannot tell from a stop
marker alone. The marker also blocks the resident from starting again until
the file is removed, so a role left with the marker in place reads as not
ready rather than quietly resuming.

Read what the stopped wake did before you remove the marker. The attempt
directory holds `failure.txt`, `journal.jsonl` and the effect ledger — the
same evidence as any other failed attempt — and `native_recovery` reports it.
Removing the marker is what unblocks startup; it does not settle the wake,
clear the busy marker, or make the unknown effects known. Those stay as
reconciliation work, and a later attempt at the same wake needs the
recovery opt-in described above.

Nothing in the plant removes this marker for you. No resident, CLI command or
supervisor deletes it, so a marker left in place keeps that role stopped until
a person removes it.

## Plant another owned worktree

Run from the **core checkout**; replace the path with the target worktree root:

```bash
python3 -m coordination.launcher \
  --workspace /path/to/project \
  --engine-command 'codex'
```

A fresh external target defaults to `TARGET/.mishe-tauftauf/` and session `mishe-` followed by the target directory name, with underscores changed to hyphens. Existing sites reuse their recorded names. Set `SITE` and `SESSION` from the actual output before attaching or inspecting them.

For a deliberately named site, pass `--home /path/to/project/.chosen-site --session chosen-session`. The home must still be directly inside that Git worktree. The launcher refuses to take over a tmux session or user unit belonging to another site. If multiple recognized resident sites exist, choose explicitly with `--home` and `--session`; do not guess which one is authoritative.

Plant the core persistently **before** the external worktree if you want automatic linked updates. If the launcher reports that the external plant was unregistered because no core plant existed, establish the core and repeat the external plant deliberately. Linked updates need a clean core and target worktree and fresh passing CI for the exact local core `HEAD`; `python3 -m coordination.site_sync --home "$CORE_SITE"` requests an immediate retry, and it is an update action rather than a read-only status check.

## Try it without installing services

For a **new manually supervised site**, add `--no-services` to the launcher command:

```bash
python3 -m coordination.launcher --engine-command 'codex' --no-services
```

This still creates the site, starts panes and agents, takes initial discovery and CI readings, and updates an external target's plant contract. It skips installing/enabling the resident user services and the release coordinator, and does not register an external manual setup for linked updates. Its service manifest is empty, so the health pane reads `STATE: RED — service failure or empty manifest` and its `SYSTEM ZERO` report reads `FAIL … services-manifest-empty`; that names the deliberately absent resident services, not a broken manual setup.

**It does not start the continuous wake/clear supervisors, CI watcher, or permission-panel follower. It also does not stop or disable services from a previous persistent plant.** Use it for a fresh manual setup, not as a way to turn off an existing installation.

To exercise the first request manually, set `SITE` and `SESSION` to the site's actual names, send the request above, then run this supervisor in a separate operator terminal:

```bash
"$SITE/bin/mishe" --home "$SITE" seed run \
  --session "$SESSION" --slug discover --interval 5 \
  --self-pick-seconds 600 --clear-grace-seconds 30
```

It supervises **only `discover`**: wakes that role, recovers dead panes, and clears a settled mind at a stable idle boundary. Ctrl-c stops this foreground supervisor, not the tmux session or its agents. For other roles, run their supervisors separately rather than assuming this one covers the whole plant. Do not start a second supervisor alongside an existing service for the same role.

`--self-pick-seconds` is the role's **review floor** — how long the supervisor may leave a role idle before waking it on the floor alone. Where the site's `SITE/coordination-mode.json` names the role under `self_pick_seconds`, that value **overrides the flag**: the flag applies only to a role the file does not name. On a site where `discover` is configured, the command above still reviews that role every 1800 s, not 600. To change a configured role's floor, edit that file's `self_pick_seconds` entry, not the command line.

A manual setup's initial CI sample becomes stale after five minutes without a watcher. From the core checkout, an explicit refresh writes the new sample and may append CI events or addressed failure messages. The launcher's per-role bootstrap commands are `seed init` and `seed start`: `seed init` prepares a role's launcher, top pane and site state, and `seed start` resumes that role's supervisor on an owned session.

```bash
env PYTHONPATH=src python3 -m mishe_tauftauf.ci_watch --home "$SITE"
```

The `permissions` display refreshes in its own top pane even in a manual setup; without its follower, dead panel panes are not automatically repaired. `access list` remains available for direct inspection. Move to persistent operation by deliberately repeating the launcher without `--no-services` once you accept the mutations listed above.

## Use another agent command

The launcher accepts a shell-quoted command string, for example `--engine-command 'omp --model YOUR_MODEL'`; substitute the arguments your installed agent actually supports. Existing `SITE/minds/ROLE` launchers are preserved, so changing this flag on a repeat plant does not replace them.

Codex, OMP, and OpenCode have built-in idle-prompt recognition. Another agent needs an executable readiness probe at `SITE/checks/mind-ready/ROLE` for each supervised role. The probe must exit zero **only when the role's lower pane can accept a complete prompt**, not merely when the process exists. It receives:
- `MISHE_SEED_SESSION`: the tmux session;
- `MISHE_SEED_ROLE`: the channel name;
- `MISHE_SEED_PANE`: the lower-pane target, such as `SESSION:discover.1`.

The probe has a two-second timeout. Missing, non-executable, failing, or timed-out probes hold delivery. Do not use an always-successful probe to make the status look cheerful. A changed pane PID proves rotation; readiness separately decides whether the next prompt can safely be delivered.

A native Python loop keeps its canonical probe in tracked source. Install or repair it with:

```bash
env PYTHONPATH=src python3 -m mishe_tauftauf.native_gate \
  --home "$SITE" --session "$SESSION" --role "$ROLE" --install
```

Its engine test accepts every python name this site runs — `python`, `python3`, `python3.12` — because the measured defect was a single literal: a live native pane whose launcher ran `python` read not-ready while its lifecycle showed a live mind, and the delivery hold reported the same `mind busy` as a TUI mid-turn. The probe reads the pane's foreground command, its dead flag and the launcher's own busy file, and exits. Read the readiness branch with the same module (`--record` persists a changed verdict at `SITE/checks/native-gate-ROLE.json`); a RED verdict names a live mind the gate rejects, and UNKNOWN names evidence the branch could not read.

## Stop the plant and remove its installation

Detaching is not stopping, and closing the terminal is not either: a persistent
plant keeps its own user services and its own tmux session. Two levels of
removal exist, and neither is a routine approval gate — the commands are yours
to run whenever you want them gone.

Stop the tmux session and leave the installation in place:

```bash
"$SITE/bin/mishe" --home "$SITE" seed stop --session "$SESSION"
```

That refuses any session it did not raise: it checks the site's `.seed-raised`
receipt and the session's ownership marker, and kills nothing it cannot prove
is its own. It leaves the installed user services enabled, so use the next step
to remove them too.

Preview the full removal first, then execute it:

```bash
"$SITE/bin/mishe" --home "$SITE" seed uninstall --session "$SESSION"
"$SITE/bin/mishe" --home "$SITE" seed uninstall --session "$SESSION" \
  --execute --confirm-home "$SITE"
```

The first command only reports: it lists the user units, installed files, tmux
sessions, linked-site registry rows, owned processes and any stored credentials
that belong to this site, and changes nothing. The second requires
`--confirm-home` to match the site's exact absolute path, refuses to run from
inside the site directory, and then disables the units, kills the owned sessions
and processes, unlinks the installed files and removes this site's rows from any
linked-site registry. It verifies afterwards that no owned unit is still active
and no owned process survives; a failed postcondition keeps the site and its
uninstall journal for diagnosis instead of claiming success.

Add `--purge-site` to delete the site's local state as well. Without it, the
site directory and its evidence remain. Run the command from outside the site
directory. Uninstalling one site does not touch another site's units or
registry rows; a site that another site still depends on is reported rather
than removed.

Next: read the running plant with `mishe view`, or read the [Mesh](mesh.md) culture note.
