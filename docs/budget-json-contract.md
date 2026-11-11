# `engine/budget.json` contract

Status: **active, partial**. `src/mishe_tauftauf/budget_producer.py` produces the `accounts`
section from the engine status and the native Claude tap (`mishe budget-produce --out …`);
`checks/budget-consistency.py` judges it. The router migration (step 4) has shipped:
`engine_router.live_status()` reads the accounts section and the router no longer reads
`engine_usage.status` or refetches stale readings. Still open: the ledger sections, the
render-only budget view (step 3) and deleting the old writers (step 5). The `budget` duty
now runs the producer (step 2): the site-local `producers/budget.sh` calls
`mishe budget-produce` before the old collector, observed on the timer 2026-11-11
(`produced_at` 05:18:05Z → 05:28:06Z, next fire 05:38:04Z). With a 600 s period inside the
900 s `MAX_AGE_S`, the file stays fresh with ~300 s of margin, so a role whose candidate
reads an account window no longer routes on no evidence. Still open for the judge: no
scheduler runs `checks/budget-consistency.py`, so a silent aging-out would go unreported
(see that duty below). Roles whose candidates are all free models or the Claude safety net
read no window and are unaffected.
Owner of the model/account mapping: `engine/routes.json`. Owner of this contract: steward.

## Problem

The budget window today has three independent writers and a viewer that carries its own
mapping:

- `operator/budget/collector.py` writes `state.json` (turns/tokens from transcripts).
- the engine status path writes `quota.json` (account windows).
- `operator/budget/claude_ratelimit_tap.py` writes `claude-native-quota.json`.
- `operator/budget/budget_view.py` carries `MAP` (account -> provider names) and computes
  used %, burn/h, ETA and verdicts itself.

Consequences observed 2026-11-10:

1. Free models (`longcat-2.5-free`, `opencode-go/beam-free`, `ollama/k2-horizon-7b`) have
   `account: null` in `routes.json`, but `budget_view.MAP` books their turns under `go-a`,
   so `go-a` reads 100% while the free models keep running.
2. The routing ledger counts a wake only at completion. Timeouts and no-yield wakes are
   never counted, so the places where tokens burn fastest vanish from the table.
3. The router (`engine_router.assess`), the budget window and the duties each read a
   different combination of the three writers, and disagree without any check.

## Design

One producer writes one file, `engine/budget.json`, atomically. Every other consumer reads
only that file and computes nothing itself.

- **Producer**: `src/mishe_tauftauf/budget_producer.py`. Writes `engine/budget.json` atomically
  with `produced_at` and its own id. The shipped slice builds only the `accounts` section, from
  `quota.json` and `claude-native-quota.json`; the ledger sections will also read `routes.json`
  (mapping owner), `engine/routing-ledger.jsonl`, `engine/routing-state.json`, `state.json` and
  `engine_router.live_status()` for the live account windows.
- **Consumers**: `engine_router.assess()`/`live_status()` (account windows and routing
  assessment), `budget_view.py` (render only), the steward, and the `budget-consistency`
  duty. None of them reads `state.json`, `quota.json` or `claude-native-quota.json`
  directly after migration, and none carries a model/account MAP.
  `live_status()` reads the `accounts` section and maps each window (`remaining`,
  `reset_at`, `state`) into the row shape :mod:`engine_policy` admits on; the router adds
  `UNREADABLE` to its non-admitting states. A missing, malformed, wrong-schema or
  older-than-`MAX_AGE_S` file reads as no status (UNKNOWN), never as an empty reading.
- **Migration**: the producer and `budget_view.py` render-only ship as one independent
  delivery. The old writers and the viewer's MAP/computations are deleted in the same
  delivery, or the delivery is not activated. No two sources at any activated state.

## Schema (version 1)

```json
{
  "schema": 1,
  "produced_at": 1794321600.0,
  "producer": "src/mishe_tauftauf/budget_producer.py",
  "routes_version": 1,
  "accounts": {
    "claude": {
      "provider": "claude-code",
      "state": "FRESH",
      "observed_at": 1794321447.2,
      "windows": {
        "5h": {"used": 0.28, "remaining": 0.72, "reset_at": 1794327600.0,
               "source": "claude-native-usage", "observed_at": 1794321447.2, "state": "FRESH"},
        "7d": {"used": 0.95, "remaining": 0.05, "reset_at": 1794366000.0,
               "source": "claude-native-usage", "observed_at": 1794321447.2, "state": "FRESH"}
      }
    }
  },
  "free_models": {
    "omp:longcat-2.5-free": {
      "free": true, "account": null,
      "turns_1h": 12, "out_1h": 123, "in_1h": 456,
      "turns_24h": 120, "out_24h": 1234, "in_24h": 4567,
      "last_outcome": {"role": "docs", "outcome": "yielded", "at": "2026-11-10T14:35:18Z"},
      "pending": 1
    }
  },
  "paid_models": {
    "omp:minimax-code/MiniMax-M3.1-Flash": {
      "free": false, "account": "minimax-a",
      "turns_1h": 3, "out_1h": 40, "in_1h": 50,
      "turns_24h": 20, "out_24h": 200, "in_24h": 300,
      "last_outcome": {"role": "genome", "outcome": "timeout", "at": "2026-11-10T13:28:00Z"},
      "pending": 0
    }
  },
  "per_mind": {
    "genome": {
      "omp:longcat-2.5-free": {"turns_1h": 2, "out_1h": 20, "in_1h": 30,
                               "turns_24h": 10, "out_24h": 100, "in_24h": 150,
                               "last_outcome": {"outcome": "yielded", "at": "..."}}
    }
  },
  "cooling": {"genome|omp:longcat-2.5-free:-": {"until": 1794255604449.7, "reason": "timeout"}},
  "routing": {
    "state": "GREEN", "reason": "every role has an admitted candidate on fresh windows",
    "roles": {"genome": {"selected": "omp:longcat-2.5-free:-", "decision": "select"}},
    "cooled": []
  },
  "unknowns": ["claude-native-quota.json unreadable: ..."]
}
```

### Field rules

- **Keys** are the router candidate key `engine:model:account` (`account` is `-` when
  null), exactly as in `routing-ledger.jsonl` and `routes.json`.
- **`accounts`** come from the engine status windows (`quota.json` shape) plus the native
  Claude tap. `used` is `1 - remaining`; `state` is `FRESH` only when
  `now - observed_at <= max_age` and `reset_at > now`, otherwise `STALE`. A missing or
  unreadable account is `UNREADABLE`, never OK.
- **`free_models`** are the `routes.json` candidates with `free: true` / `account: null`.
  They get their own rows and MUST NOT be summed into any paid account window. Their
  verdict is `FREE` with `no limit known`, plus turns/h and last outcome.
- **`paid_models`** are candidates with an account. Their spend is the ledger's, not a
  share of the account window.
- **Ledger counts** include every `outcome` event (`yielded`, `timeout`, `lost`, any
  other) and every `select` with no later `outcome` for that role is `pending` (UNKNOWN,
  never 0). A wake that times out or dies is counted, because that is where burn is
  highest.
- **`per_mind`** is actual spend per role per model from the ledger, not a historic share
  estimate. It replaces `budget_view.window_models`' "historic 6h share" column.
- **`routing`** is `engine_router.assess(routes, live_status, cooling, now)` computed by
  the producer at write time, so the view and the router cannot disagree about the next
  selection.
- **Age**: every window and every ledger-derived number carries its own `observed_at` /
  `at`. A number older than its max age is shown `STALE`/`UNKNOWN`, never fresh.

## `budget-consistency` duty

Resolver: **steward**. Command: `{python} {repo}/checks/budget-consistency.py --home {home}`.
Exit codes: 0 GREEN, 2 RED, 3 cannot decide (ages to UNKNOWN). `every`: 300, `timeout`: 60,
`max_age`: 900.

Not yet registered: `checks/budget-consistency.py` is in no systemd timer, CI job or
observer-check inventory. Measured 2026-11-11 with `checks/check-scheduler-coverage.py`, the
check is `unobserved`, so its verdict is manual only and a file that silently ages past
`MAX_AGE_S` produces no reading. Registering this duty is a scheduler change and needs fresh
review.

RED when any of:

1. A model with a ledger `select` in the last hour has no row in `engine/budget.json`.
2. A candidate that `yielded` in the last 30 min sits under an account whose window says
   exhausted (`used >= 0.99` or `remaining <= 0`), or a free model is booked under a paid
   account.
3. The router's `routing.roles[role].selected` is a candidate whose row is exhausted, or
   the router skipped a candidate the file marks ok for an exhaustion reason (either
   direction).
4. Any number in the file is older than its max age but shown `FRESH`.

## Migration checklist

1. Ship `src/mishe_tauftauf/budget_producer.py` + `checks/budget-consistency.py` + this
   contract as one independent delivery, with its own install record. **Done.**
2. Run the producer; verify `budget-consistency` GREEN against the live sources. **Done
   periodically.** The `budget` duty (`every` 600 s, `max_age` 1800 s, resolver senses) runs
   the producer from its site-local `producers/budget.sh`; observed on the timer 2026-11-11
   05:28:04Z, `produced_at` 05:28:06Z, GREEN, next fire 05:38:04Z. That script is not
   git-tracked, so re-installing it loses the line; the durable home is a tracked duty entry
   (scheduler change, fresh review).
3. Switch `budget_view.py` to render-only from `engine/budget.json`; delete its `MAP`,
   `rows()`, `models()`, `window_models()` computations. **Open.**
4. Switch `engine_router.assess()`/`live_status()` to read account windows from
   `engine/budget.json`; delete the direct `engine_usage.status` read from the router.
   **Done** (`live_status()` maps the accounts windows into the policy row shape; the
   `omp usage invalidate` refetch is deleted, the producer's duty owns refetching; `assess()`
   no longer reports UNKNOWN when no candidate reads a window, so a missing file does not
   stop free-model and safety-net routes).
5. Delete `operator/budget/collector.py`, `quota.json`, `state.json`,
   `claude-native-quota.json`, `claude_ratelimit_tap.py` and the old budget signal
   producer once the new path is observed live and GREEN.
6. Keep the budget window readable in the outer window plant `plants:budget`.
