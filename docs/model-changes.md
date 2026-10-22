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

The first activation adapter supports literal shell launchers exporting
environment values and executing OMP directly, with either model flag spelling.
Arbitrary wrappers, inline credentials, resumed sessions and native runners with
their own provider packages require separate adapters; they remain explicit
UNKNOWN or fail preflight. The command does not change accounts or other plants.
