# Changing mind models

Inspect the plant's minds, then replace an exact model:

```sh
mishe model list
mishe model replace opencode-go/minimax-m3.1-flash-free minimax-code/MiniMax-M3.1-Flash --effort compatible --fresh-start --wait 30
```

The command checks the target catalog and makes a bounded provider probe using
each affected mind's credential directory and configuration. It preserves other
launcher arguments and saves the original bytes. Provider probes can consume
inference quota. `--dry-run` checks the catalog and prints proposed changes
without changing launchers, restarting minds or making an inference request.
Catalog discovery may refresh the provider's own cache.

Names match the exact provider and model, including case. `--role ROLE` narrows
the change and can be repeated. The default effort policy preserves supported
values and rejects unsupported values. `--effort compatible` permits a lower
supported value, for example `xhigh` to `high`; `--effort highest` selects the
target's highest supported value. Each adjustment appears in output.

Without `--fresh-start`, busy minds wait for idle. With it, current wakes are
marked **abandoned**, their pane output is retained, and the next wake starts
from durable state. Abandonment does not undo prior actions or certify that they
succeeded. The next mind receives that uncertainty before repeating external
actions. The command never restarts a shared tmux server.

Every applied request returns a change ID:

```sh
mishe model status CHANGE_ID
mishe model revert CHANGE_ID
```

Status distinguishes selected configuration, queued activation, a new process,
and a successful resident provider turn. A preflight probe does not establish
resident adoption. `delivered` preserves a verified operation after a later
canonical rotation; that new process's provider health is a separate observation.
Exit status `3` means pending, partial or unknown delivery; `2` means an invalid
request or failed preflight. Repeating the same request
resumes its operation. Revert restores the exact saved configuration and drives
the reverse process change while refusing later peer edits. Restoring a retired
model can fail; the command retains that failure.

`mishe model watch` advances queued changes and records observations every 30
seconds. `--once` runs one cycle; `--check` fails for missing, broken, stale or
unhealthy monitoring. Unverified changes older than 120 seconds and failed
observations address Health. Persistent installations need a supervised watch
process with a watchdog and an independent freshness check.

The watcher shares the plant-wide seed lock with every resident supervisor,
which holds it across slow steps (a discovery scan, a pane capture, a dashboard
render, a tape read). Each change's acquisition is therefore bounded: a cycle
that cannot take the lock publishes `unknown` naming the contention and stays
alive to retry, instead of waiting past its own watchdog and leaving the
freshness check reading a snapshot nothing refreshed. A deferred observation is
not a delivery verdict; `model status CHANGE_ID` still reports the operation's
own state.

The first activation adapter supports literal shell launchers exporting
environment values and executing OMP directly, with either model flag spelling.
Arbitrary wrappers, inline credentials, resumed sessions and native runners with
their own provider packages require separate adapters; they remain explicit
UNKNOWN or fail preflight. The command does not change accounts or other plants.

## Engine account inventory (read-only)

`mishe engine accounts` lists the registered subscription pools from
`engine/accounts.json` in the site home, with credential-store health from OMP's
read-only metadata. `mishe engine status [--json]` also reads `omp usage --json`
through the site's credential store and shows each account-bound window with its
remaining fraction, reset time and provider fetch age. Exit status `3` means a
pool is unconfigured, stale, unattributed or UNKNOWN. Neither command changes a
launcher, credential or routing decision.
The installed launcher predates the `engine` subcommand. Until a reviewed snapshot
carries it, run it from the source tree:
`PYTHONPATH=src .venv/bin/python -m mishe_tauftauf --home <site home> engine status`.

Each alias binds to an OMP credential row by id within its `store`, an OMP agent
directory relative to the site home. The default is `omp-recovery/agent`; a second
subscription on the same provider needs its own store, because OMP's usage report
carries no credential identity. Accounts are read per store, and a store that holds
several configured accounts for one provider stays UNATTRIBUTED. Two accounts whose
quota windows reset at identical instants are treated as one entitlement under two
credentials and both stay UNATTRIBUTED. Keys and identity fields are never read
into output. A report older than 180 seconds is STALE even when OMP's aggregate
timestamp is fresh. A store whose auth schema differs from the observed version 8
is refused rather than guessed, and its usage is not read. Provider failures append
to `engine/usage-errors.jsonl` (mode 0600).

Last-good retention: each successful FRESH or STALE sample is kept in
`engine/last-good.json` (mode 0600). A later failed read shows the account UNKNOWN
with that sample under `last_good`, labelled with its own age. A malformed
last-good file is reported and not overwritten.

Provisioning Go B: its store `engine/go-b-store` exists but holds no credential.
Provisioning is a human step; the key is entered in the terminal and never in chat
or a file. The flow for an API-key provider in OMP is not verified here: start
`omp` with `PI_CODING_AGENT_DIR` set to that store and use `/login` for
`opencode-go`. Then set the `go-b` registry entry to `configured` with its
`credential_id`, and confirm the store's usage with `mishe engine status`.

Routing, reserves and automatic rotation are not part of this surface.

## Engine watch (supervised)

Collector cycle every 60s ±10s jitter reads `engine_usage.status(home)` and `token_usage.report(home)`, writes `engine/watch-cache.json` (0600) with `{observed_at, engine_state, stats_state, decisions: {role: policy.decide(...)}, next_reconsider}`. Next reconsider is the earliest `next_reconsider_ms` across roles. Private failures append to `engine/watch-errors.jsonl` (0600).

Decision emits but does not apply: when `policy.decide` returns `select` for a role and the selected route differs from the current route (from `engine/current/<role>.json` or `engine/current.json` when present) and hold allows, a proposal record is written to `engine/proposals/<generation>-<role>.json` (0600) with the decision and generation (observation ms). No launcher is changed. The `--apply` flag may call the milestone 3 `apply()` step with CAS comparing the generation; reapply without CAS is not automatic.

Service is a supervised restart loop (`serve()` calls `once()`); `once()` is the testable single cycle and `serve()` is the daemon. An external supervisor restarts the loop; the cache mtime is the liveness signal.

Stale guard is the independent `check()` reading `engine/watch-cache.json` mtime. If missing, stale (>180s), future-dated (<-5s skew), or malformed (unreadable or missing `observed_at`/`decisions`), it returns `UNKNOWN`/`RED` with a reason and appends to the durable Health outbox `health/engine-watch-alert.jsonl` (0600). The outbox is append-only; recovery appends a new record without erasing the original. A broken alert delivery still leaves `UNKNOWN`/`RED` (the alert error is recorded privately). Health detection interval for model-change monitoring is 120s; engine watch uses the 180s failure-detection bound, matching `substrate_watch` and `initiative-watch` staleness.

Tested revert/backout: `rm engine/watch-cache.json` then `once()` reproduces the same `FRESH` state and decisions; guard with missing or stale cache stays `UNKNOWN`/`RED` until a fresh `once()` recovers.
