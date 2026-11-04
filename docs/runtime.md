# The observation-driven runtime

Minds are persistent identities (a wall, intentions, history), not persistent
processes. Scripts observe the world and write files; systemd watches those
files and starts a short-lived **wake** only when something is owed. When
nothing is owed, nothing runs.

## Durable state (all under the site home)

| Path | Meaning |
| --- | --- |
| `signals/NAME.json` | Last reading: value, state, `rev`. Replaced **only when value or state changes** — that replacement is the edge that wakes subscribers. |
| `signals/NAME.seen` | Touched on every successful observation; its mtime is freshness. Never watched. |
| `signals/registry.json` | Per signal: `max_age`, subscribing roles, optional `reoffer` seconds. |
| `inbox/ROLE` | Touched when mail lands for ROLE. The tape is the record; this is only the trigger. |
| `intentions/ROLE/ID.json` | What a mind said it would come back to: `due` epoch and/or `when` signal condition; `spontaneous` ones answer to the budget gate. |
| `cursors/ROLE.json` | What the role has already seen (inbox, signal revisions, tape position). |
| `dispatch/` | One durable record per role: `started`, `completed`, `unknown`, `reconciled`, … |
| `wakes/` | Per wake: dossier prompt, snapshot of what it saw, engine output, yield receipt. |
| `hold` | If present, every wake exits `held` without calling a model. |

A signal with no file, an unreadable file, or no observation within `max_age`
reads **UNKNOWN**, never GREEN. Silence is an event: when a source stops
refreshing, the next-due timer turns its signal UNKNOWN on schedule and its
subscribers are woken.

## A wake (`mishe wake ROLE`)

1. Refuse if `hold` exists, or if another wake holds the global lock (the
   finishing wake starts the next owed role).
2. Another role's open dispatch is offered first; this role's own open dispatch
   makes this a **reconcile-first** wake.
3. **Owed check, no model:** unread inbox, subscribed signals whose revision moved
   past the cursor, intentions that are due or whose condition holds. Nothing
   owed → record `skipped`, exit, no model call. A `spontaneous` intention is held
   unless the `budget` signal is GREEN; external triggers are never gated.
4. Claim the dispatch, build the **dossier** (charter, wall, why woken, unread
   mail, signals, due intentions, git delta, ending instructions) and run the
   engine: `claude -p` or `omp --print --no-session`, in its own process group,
   killed as a group at the wall-clock bound.
5. The mind ends with **`mishe yield --result changed|verified|blocked`** (wall on
   stdin; `--continue`, `--again 2h`, `--when signal:NAME=VALUE`). Yield writes the
   wall, advances the cursor to what the wake saw, writes the follow-up
   intentions, renews the idle initiative, re-offers any subscribed signal still
   RED, and writes the receipt last.
6. **No yield means `unknown`.** A crash, non-zero exit or timeout leaves the
   dispatch open and the cursor where it was, so the next wake sees the same
   inputs and reconciles. An unknown effect is never replayed. A command that never
   started is a plain `failed`.

## Triggers (`mishe sync`, `mishe doctor`)

`mishe sync` generates, from the declared roles, the signal registry and
`producers.json`: `mishe-wake@.service`, one `mishe-wake-ROLE.path` per role
(`PathChanged` on its inbox and its subscribed signal files), `mishe-failed@`,
`mishe-arm`, `mishe-doctor.{service,timer}` and one timer/service per producer.
It writes only files carrying its marker, so nothing else is removed.
`mishe doctor` compares expected with installed units and exits non-zero on drift or
missing units. It also writes the `doctor` meta-signal, so a doctor that stops
running reads UNKNOWN.

Every generated service carries `OnFailure=mishe-failed@%n.service`. That single
target records a RED `failed-UNIT` signal, touches the resolver's inbox with a
message, and sets a re-offer intention, so a standing fault returns to its
resolver at a bounded interval until someone clears it
(`mishe signal observe failed-UNIT GREEN`). Health is the default resolver;
failures of health's own wake go to genome (`resolvers.json` overrides per unit).

## Producers

The only polling. `producers.json` declares `{name, command, every, timeout,
signal: {max_age, subscribers}}`. `mishe signal run NAME -- CMD` turns any
exit-code check into a signal: exit 0 is GREEN, non-zero RED (output kept in
`signals/NAME.detail`); a check that cannot run or times out leaves the signal
untouched so it ages to UNKNOWN, and the run fails so `OnFailure` routes it.
`mishe produce dispatch` is the built-in producer for open-dispatch age.

## Holding and releasing

`mishe hold on --reason TEXT` stops every wake from calling a model while units stay
installed; `mishe hold off` releases. Owed roles are not re-triggered by releasing
alone: run `mishe due` (or touch an inbox) to offer the first owed role.

## Measured

A real `claude` (haiku) one-shot wake on a scratch site: 5.2 s engine time,
about $0.002, three turns of about 18k input tokens each (mostly cached); the mind
called `mishe yield` from inside its own Bash tool. Real systemd path units on a
scratch namespace: inbox touch and signal edge each started exactly one wake; a
`.seen`-only refresh started none; a killed wake produced a RED signal and
an inbox item, and the next wake began with `RECONCILE FIRST`.

Not yet measured: a real `omp` wake.
