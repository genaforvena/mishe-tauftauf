# Unattended Claude quota readings

Words used below: a **window** is one usage allowance with its own reset time
(the 5-hour and the 7-day window). **burn** is how fast a window's used share
rises, per hour. **LOW** means a window is spent (more than 90% used). **RED** is
an account verdict: a window is LOW, or its burn projects it to run out before
the reset. **UNKNOWN** means the reading is missing, stale or unparseable, so
nothing is known from it. **reserve** is the share of a window a route must leave
unspent. A **producer** is the job that measures a reading, and a **signal** is
the published reading with its verdict; a **duty** checks a producer's signal
against its maximum age ([network-gaps.md](network-gaps.md) defines these).
A **candidate** is one entry in a role's roster in `engine/routes.json`, the
choices a wake can be routed to. **senses** is the role that owns acquiring these
readings. The full field contract is in [budget-json-contract.md](budget-json-contract.md).

Claude Code's native local command can report subscription limits without a
model request:

```sh
claude -p /usage --model claude-haiku-5-5 --tools '' --max-turns 1 \
  --output-format json --no-session-persistence \
  --setting-sources project,local --settings '{"disableAllHooks":true}'
```

The producer in `src/mishe_tauftauf/claude_quota.py` runs this command with
`TZ=UTC`, `LC_ALL=C.UTF-8`, `DISABLE_AUTOUPDATER=1` and a 45 second timeout.
`python3 -m mishe_tauftauf.claude_quota_text --home "$MISHE_SEED_HOME"` parses
the output in UTC. It accepts only a successful `local_command: usage` result
with zero API time, turns, cost and token counts, and both subscription windows.
Other output, malformed windows, invalid resets and timeouts become UNKNOWN.
Hooks and automatic updates are disabled for this read. It reads no credentials.

The result is `operator/budget/claude-native-quota.json`. Every attempt retains
its original stdout/stderr and previous observation in `operator/budget/native-evidence/`.
Failure publishes no current windows; last-good readings remain evidence and
cannot establish budget readiness. The producer should run at least every ten minutes. The
producer and the budget pane expire readings after fifteen minutes (`MAX_AGE_S`
900 s). Senses owns acquisition failures. The `budget-claude` signal carries the
verdict and names the window and rule that triggered a RED. The tracked publisher registers
that signal with no subscribers, so a site that wants the verdict routed registers its own
resolver. The codex-a verdict appears in the publisher's result and the household budget
view. The `budget-codex` duty (`checks/budget-codex.py`, resolver `steward`) judges
the codex-a 5h and 7d windows: RED when a FRESH window is used past 0.9, UNKNOWN when a
window is not FRESH or malformed. Burn is not judged. The `signal-coverage` duty probes
it beside `budget-claude` and reports a fresh RED codex-a verdict as a provider finding
when `budget-codex` has no signal registration.

The native report has display precision: whole percentage points and minute
reset times. The result records that precision explicitly. The tolerance is one
display unit at each bound: a reset stamped up to a minute before the read still
parses (the display rounds down to the minute), and a reset up to a minute
beyond the window's own length is still inside it. A reset further in the past
is expired, and the attempt becomes UNKNOWN. It is not an exact
token allowance or a replacement forecast. The existing LOW and burn-rate rules
remain in effect.

The budget producer calculates Claude burn from `operator/budget/claude-native-rate-history.jsonl`.
The native collector appends only complete `/usage` observations. The earlier
`claude-rate-history.jsonl` contains both native samples and statusLine callbacks, whose
timestamps can reflect a redraw of earlier API data. It remains available for historical
investigation. When the native samples for a reset span less than 25 minutes, the producer
can estimate burn from use divided by elapsed window time. It starts after 15 minutes of
elapsed window time and labels the estimate `window-elapsed`. The estimate can fall even
when usage stays flat. For example, at a steady 8% used, the five-hour forecast can
turn RED at minute 15 and clear at minute 24, when 8% of the window has elapsed.
Read `burn_source` in `engine/budget.json` to see how the current forecast was
calculated. Once the native history spans 25 minutes for that reset, the producer
labels the measured rate `history`.

References: [native commands](https://code.claude.com/docs/en/commands),
[status line data](https://code.claude.com/docs/en/statusline). Observed native
contract: Claude Code 2.1.319 on 2026-11-06; unsupported future formats fail closed.

## Spending policy (decided; pace is reported, not gated by a percentage test)

Status: no code applies a 50% gate, and the router does not read the policy.

- The alert rule lives in the site-local `SITE_HOME/operator/budget_snapshot.py`, run by the
  `budget` duty's `SITE_HOME/producers/budget.sh` and not tracked in Git. It alerts only on
  UNKNOWN or stale readings, so a spent (`LOW`) window with a known reset and a
  burn projection are informational; above 50% weekly use shows as pace in
  `SITE_HOME/operator/budget.md`. The pane `SITE_HOME/operator/budget/budget_view.py` has been
  render-only since 2026-11-12: it draws the producer's burn and ETA and decides
  nothing.
- `src/mishe_tauftauf/provider_budget.py` builds the Claude and codex-a rows from
  `engine/budget.json` itself (`rows_from_engine_budget`). It reports `LOW` when
  `used > 0.9` and `RUNS OUT BEFORE RESET` when the producer's `eta_s` lands before
  the reset and the window is more than 5% used (at or below 5% the short-span
  guard keeps the window ok, after rounding away float noise). Either result turns the account
  verdict RED. The exception is the `claude` 7d window (listed in the
  `PROJECTION_ACCEPTED` set in `src/mishe_tauftauf/provider_budget.py`, since 2026-11-21): its burn projection is informational, so only a
  spent 7d window (`LOW`) turns it RED. The 5h window keeps the projection alarm.
- The router compares each fresh account window's remaining fraction with the
  candidate's reserve. The default is 15%. A candidate in `engine/routes.json`
  can override that default with `reserve_pct`, a fraction in `[0, 1)`.
  For example, at 93% weekly use, the provider verdict is RED for `LOW`.
  A candidate with a 5% reserve can still be admitted because 7% remains,
  provided its other window and route checks pass. With 4% remaining, that
  candidate is held in reserve. The routing ledger's “all windows healthy” reason
  describes the candidate's reserve check.
- A role can use `budget-claude` to admit spontaneous intentions. The owed check
  (`owed.py`: is there an unread message, a moved signal or a due intention for
  this role?) also accepts fresh free-route evidence from the plant's budget snapshot, but only
  from that role's own roster in `engine/routes.json` (`free: true` candidates whose
  key has a fresh FREE row), and only when the router can choose one with a free
  slot (`engine_router.has_free_route`, `require_slot=True`). While the paid
  budget check fails, a wake admitted solely for spontaneous work can only try
  free candidates. Addressed and scheduled follow-up work uses the role's
  regular candidate roster.

RouterAI is not a mind route any more. It was removed from every role in
`engine/routes.json` on 2026-11-12 (operator DM 300461) and now sits in that
file's `retired` list, so no role may select it and no wake falls back to it:
a wake whose subscription candidates are not admitted falls through to that
role's other candidates. `engine/budget.json` keeps the two retired RouterAI
candidates in a `retired_models` section; the budget pane does not draw that
section yet, so a retired candidate is absent from the pane rather than
counted in it. RouterAI is the pay-as-you-go provider for decision questions
(System One), which is a different path from a wake.

The operator decision of 2026-11-11, recorded in the operator notes:

- Subscription windows may be spent freely up to 50% of their weekly limit.
- Above 50% weekly use, Mishe counts and cares about pace. Mishe still decides; the
  decision does not route to the human owner.
- RouterAI is the backup when the subscription providers fail, not a primary route.
- Alerts about budget do not ask the human for anything.

Corrected on 2026-11-14: the third bullet described the mind routes as they
stood on 2026-11-11. On 2026-11-12 RouterAI left every role in
`engine/routes.json` (operator DM 300461) and became the pay-as-you-go provider
for decision questions instead, so today no wake falls back to it.
