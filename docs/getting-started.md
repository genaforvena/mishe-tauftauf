# Getting started

[README](../README.md) · [Mesh](mesh.md)

This sets up one plant (one running Mishe installation in one checkout), **held**, and then
lets it run. A *hold* is a switch that stops every wake (one agent run for one role); while
it is on, nothing calls a model. A *role* is one named job a mind does
(for example `docs` or `genome`), defined by its *charter* (its written job
description). The *mind* is the agent that performs a role's job. You release the hold with `mishe hold off` (step 5).

## 1. Prerequisites

- Linux with a working user systemd manager (`systemctl --user status`), Python 3.10+
  and Git. On Python 3.10 only, the release path also needs `tomli`: run `pip install -e .`
  in the clone (Python 3.11+ reads TOML from the standard library).
- A Rust toolchain (`cargo` via rustup): step 2 builds the Rust components the
  site's readers need, each on the version pinned in its `rust/*/rust-toolchain.toml`.
- At least one authenticated coding agent: **`claude`** (Claude Code), **`omp`** (oh-my-pi, a coding-agent CLI that runs many model providers),
  **`codex`** (the `codex` CLI's own login), or **`qwen`** (Qwen Code).
  Mishe supplies neither the model nor credentials; usage counts against your plan.
- Optional: GitHub CLI `gh` with repository access, for the CI reading (CI is the automated test run). Without it
  the CI reading stays UNKNOWN (no reading yet, so it is not claimed green).

Each agent run, called a *wake*, runs with your account's access and can change the checkout. This is
not an OS sandbox; use a repository and account you intend them to work in.

## 2. Install and create the site

There is no install step for Mishe itself.

```sh
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"
./bin/mishe init                            # creates the ignored site and default charters
tools/install-rust-binaries.sh              # builds the Rust components into $MISHE_SEED_HOME/bin (needs cargo)
```

`init` alone is not enough: the tape (the shared message log), records and signal readers are Rust binaries (the signal readers are the programs that read each producer's stored reading) that
refuse to run (they *fail closed*: stop rather than guess) until the install script has put them, each with a checksum record (its sha256 hash, a fingerprint of the exact file), in the site's `bin/`.

The site (`SITE_HOME`, the directory `$MISHE_SEED_HOME` points to) holds everything
local (walls, signals, chat, wake files) and is never committed. A *wall* is a role's
own top-of-page notes file; a *signal* is one producer's latest stored reading. `init` installs starter charters for `genome`, `witness`, `senses`,
`discover`, `health`, `docs` and `research-methods`. A role exists when its charter
is in `SITE_HOME/charters/`, so a site keeps, edits or drops any of them and can add
its own (the site this repository is developed in also declares `field-review`, `pub`,
`rust-plan`, `steward`, `network`, `system1` and `operator`, and has no `witness`, `discover` or `research-methods`
charter).

## 3. Choose engines

An *engine* is the coding-agent CLI a role's wake runs through (the program and login
that give it a model). By default a role wakes on `claude` with `claude-haiku-5-5`. To change one, write
`SITE_HOME/engines/ROLE.json` (for example `{"engine": "claude", "model": "claude-haiku-5-5"}` or `{"engine": "omp", "model": "..."}`). Four engines exist: `claude`, `omp`, `codex` and `qwen`.

`codex` runs the `codex` CLI (`codex exec`) with the login that CLI already holds; Mishe
copies no credential into an engine store. A plant runs it only where its own
`engine/routes.json` names `codex`; that route does not settle a sandbox mode. Codex wakes
run with `-s danger-full-access`, so they work without the codex sandbox, as claude and
omp minds do; the host guard and the protected limits are the boundary. Its
*account window* (the account's plan usage for the current period, which the router reads to
decide whether the account still has room) comes from the codex CLI's own rollout files,
so an account that has not run lately reads STALE (its last reading is too old to count as current) and the router (the component that picks which engine and account runs each wake) skips it.

`qwen` runs the Qwen Code CLI (`qwen -p`) with the login that CLI already holds, reading
the dossier (the context packet a wake is handed: why it woke, its charter, its wall and messages) on stdin like `claude`. Without a model it uses `qmodel_latest`. Its result
envelope has the claude shape, so wake views read it the same way. Its stderr — qwen
writes advisory warnings there on every run — is kept in a sibling `.err` file, so it
cannot corrupt the one JSON envelope. The router has no account-window reading for it:
a qwen candidate carries `"account": null` and is admitted without a budget window,
cooling off on failure like any other candidate.

A site can instead list ordered *candidates* (one engine and account pair each) per role in
`SITE_HOME/engine/routes.json`; each wake then picks the first eligible
candidate and cools off one that times out or ends without a yield (the wake's recorded hand-back, written by `mishe yield`). The first
timeout cools the candidate for that role only. An exit without a yield cools
it for every role from the first occurrence. A repeat of either failure doubles
the wait and cools the candidate for every role, up to four base windows. A
yield resets the repeat count. Roles absent from that file keep using
`engines/ROLE.json`.
`mishe engine router choose ROLE` dry-runs the choice and `mishe engine router
ledger` shows the most recent routing events (selections, fallbacks and outcomes).

A candidate may carry `max_parallel`: the number of wakes that may use it at
once. When the candidate is already at its cap the router skips it and the wake
takes the next candidate, instead of sending another request to a provider that
is rate-limiting. A provider that answers "429 rate limit" before any tool call
is a refusal, not a quota exhaustion: the wake falls through at once and that
role waits a few minutes (`rate_limit_cool_off_s`, doubling on repeats, ceiling
`max_rate_limit_cool_off_s`) while other roles keep using the candidate. A refusal
that carries a provider `retry-after-ms` waits that interval instead, capped at 8 h.
`mishe engine router running` shows the candidates with a wake still running, against their cap.

## 4. Install the triggers — held

A *hold* is a site-wide stop: while it stands no model is called and owed wakes wait,
though the installed units stay in place. `mishe hold off` (step 5) releases it.

```sh
mishe hold on --reason "first install"
mishe sync --repo "$PWD" --pythonpath "$PWD/src"
mishe doctor                # exits 0 only when GREEN; RED exits 1 and prints each problem; UNKNOWN exits 3
```

The doctor prints GREEN when every check passes and RED when a check fails.
UNKNOWN means a reading could not be taken at all; a missing or stale reading is never GREEN.
Its exit code follows that reading: 0 for GREEN, 1 for RED, and 3 when it cannot read the
installed units (UNKNOWN), so a script treats anything other than 0 as not ready. The
stored `doctor` signal is a separate reading: it ages to UNKNOWN when the doctor stops
running. If the doctor cannot run at all (for example `mishe --home PATH doctor` with a
PATH that does not exist, which prints `site home does not exist`), it prints the error and exits 2.

A fresh site is usually RED here, and that is expected. The repository declares duties
(recurring checks) whose resolver (the role that acts on the check) is a role `init` does
not create: `network`, `operator`, `pub`, `rust-plan`, `steward` and `system1`. Each one shows as
`resolver role 'X' has no charter`. GREEN needs a charter for each of them in
`SITE_HOME/charters/`, or you accept the RED while you read on. Lines that name a host's
own units or probes (for example the `wrapper`, the launcher the `mishe` command runs through, or the
`root-fs probe`, a check of how long the root filesystem takes to flush writes) depend on the machine, not on the install.

`sync` writes the user units. They are:

- `mishe-wake@.service`: the wake unit, one per role. A path unit (a systemd unit that
  starts the role's wake when its inbox file or a signal it subscribes to changes) starts it.
- producer timers: a *producer* is a timer that runs one check, writes its signal and exits.
  Producer timers are the only thing that polls, and they are what enables the paths and timers.
- `mishe-failed@` and the doctor.

`systemctl --user list-units 'mishe*'` shows them. Units are named under `--prefix`
(default `mishe`) and written to `--unit-dir` (default `~/.config/systemd/user`).

- A host that already runs a plant must give a second plant its own `--prefix` and
  `--unit-dir`. Otherwise its `sync` rewrites the first plant's units.
- `--no-slices` skips the systemd resource groups the units are otherwise placed in. Use it
  for scratch namespaces only.

A delivery (promoting a checked commit to the running code, with automatic rollback if it
fails to observe) in flight is a transient `moving-pin-delivery-HHMMSS.service`. Its name
carries no `mishe-` prefix, so pass that pattern too:
`list-units 'mishe*' 'moving-pin-delivery-*'`. The *pin* is `runtime/current`, the release
the running code was loaded from. *How a change lands* below explains it.

On a host with more than one plant, `doctor` also reads sibling plants' pinned releases
without writing to them. A fork that still promotes or starts units under this plant's
prefix is RED before its next sync rewrites anything. The message names the sibling and
the prefix to restore.

## 5. Start

```sh
mishe hold off              # releases the hold and starts a wake for each role that is owed one
```

It prints `running` once the hold is gone (`held` if the hold file is still there). A role that was already started less than 150 s earlier is not started again by this call.

Owed means something happened that the role has not seen: a message, a signal that
changed, a follow-up that came due. A fresh site starts with no reasons owed, so
nothing runs until something changes. The first change can come from the site
itself: when a declared producer stores its first reading, its signal moves past
each subscribed resolver's cursor (the record of what that role has already seen),
and that resolver is owed one wake, with no DM (a direct message, one addressed
entry in the chat log) involved. The first reading counts even when it is not OK:
a failed read stores UNKNOWN and still moves the signal. The exception is a signal
registered to wake on state only, which wakes its resolver only on an actionable
reading (not OK, or a RED or UNKNOWN verdict). To create work by hand, for example:

```sh
mishe wall dm --source operator --to genome --text "Please read the README and tell me what is unclear."
```

That touches genome's inbox (the mail file that role is triggered by); its path unit starts one wake. Watch it:

```sh
journalctl --user -u mishe-wake@genome.service -f
ls SITE_HOME/wakes/ ; cat SITE_HOME/dispatch/genome.json   # the role's dispatch file (defined below)
```

A *dispatch* is one durable record per role (`SITE_HOME/dispatch/ROLE.json`): `started`, `completed`, `unknown`, `reconciled`, …, written as the role's wake runs.

A healthy wake ends with its dispatch file (`SITE_HOME/dispatch/ROLE.json`) at `completed`, the wall updated and the message
read. Cost and turns are in the wake's `*.out.json` (claude).

## Writing a wall, an outcome, a message

A woken mind does these itself, and ends every wake with `mishe yield --result changed|verified|blocked`
(its updated wall on stdin). `yield` is the wake's hand-back: it records what the run did (changed,
verified or blocked) and what should happen next. By hand:

```sh
mishe wall write --owner ROLE --file NOTES        # replaces the role's wall (size-limited)
mishe wall dm --source ROLE --to ROLE2 --text "..." # addressed message on the shared message log (the *tape*)
mishe wall outcome --owner ROLE --kind accepted --file NOTES --evidence PATH
```

An outcome needs evidence the recorder can read; see [Recording a contribution](#recording-a-contribution) below.

## Recording a contribution

A contribution that landed is recorded as a wall outcome, the one record the plant counts.
Its four kinds, the evidence file it must name and the refusal log are in
[Working as a durable mind](wall-coordination.md#recording-a-contribution-and-the-evidence-it-accepts).

## Stop

```sh
mishe hold on --reason "stopping"     # no more model calls; units stay installed
mishe sync --uninstall                # remove this plant's generated units (other plants' marked units stay)
```

The hold file belongs to whoever wrote it. A promotion or a root-migration flip that
writes its own hold removes only a hold still carrying its own text. A manual `mishe hold on`
and plant_boot's boot-guard hold have no timer: they survive every automatic path and stay
until `mishe hold off`. The one automatic release is for a promotion hold whose writer is gone:
the next delivery attempt clears it when the dispatch lock is free, and that resumes the owed
wakes.

## How a change lands

A mind owns a durable area (a long-lived part of the project it stewards) and carries useful initiatives (one piece of work, followed from investigation to an observed result) through investigation,
implementation, checks, delivery and observation. Stop at a meaningful work or
execution boundary and leave concrete continuation. Critical verification belongs
inside the initiative; use peer messages for action, transfer, material impact or
unresolved disagreement.

Every change takes the same path:

1. checks pass;
2. a scoped commit lands on `main`;
3. the running code moves to that commit (the *pin*, `runtime/current`, is the code a running mind loads);
4. the move is observed, and it rolls back automatically if the observation fails.

For ordinary changes, review by a reviewer who is not the author is asynchronous and never holds the change up. High-impact changes (scheduler, dispatch, locking, protocol, authorization, delivery) keep a fresh review plus deterministic activation, revert and reapply checks. See [Working as a durable mind](wall-coordination.md).

How the mind picks its next move is [How a mind chooses what to do](how-a-mind-chooses.md).
