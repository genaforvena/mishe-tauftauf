# Unattended Claude quota readings

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
cannot admit work. The producer should run at least every ten minutes; the
producer and the budget pane expire readings after fifteen minutes (`MAX_AGE_S`
900 s). A missing or stale producer therefore cannot establish budget readiness. Senses owns acquisition
failures; the `budget-claude` signal carries the verdict and, for a RED, the cause that
held work. The tracked publisher registers
that signal with no subscribers, so a site that wants the verdict routed registers its own
resolver.

The native report has display precision: whole percentage points and minute
reset times. The result records that precision explicitly. It is not an exact
token allowance or a replacement forecast. The existing LOW and burn-rate rules
remain in effect. A fresh report can still hold spontaneous work RED.

The previous statusLine tap remains historical evidence. Its callback timestamp
establishes a render, while `rate_limits` only appears after an API response in
the session. Do not renew quota freshness merely by redrawing an idle terminal.
The budget pane shows the producer's file, which reads the independent native
report after its reviewed activation.

References: [native commands](https://code.claude.com/docs/en/commands),
[status line data](https://code.claude.com/docs/en/statusline). Observed native
contract: Claude Code 2.1.319 on 2026-11-06; unsupported future formats fail closed.

## Spending policy (decided; pace is reported, not gated by a percentage test)

Status: no code applies a 50% gate, and the router does not read the policy.

- The alert rule lives in the site-local `operator/budget_snapshot.py`, run by the
  `budget` duty's `producers/budget.sh` and not tracked in Git. It alerts only on
  UNKNOWN or stale readings, so a spent (`LOW`) window with a known reset and a
  burn projection are informational; above 50% weekly use shows as pace in
  `operator/budget.md`. The pane `operator/budget/budget_view.py` has been
  render-only since 2026-11-12: it draws the producer's burn and ETA and decides
  nothing.
- `src/mishe_tauftauf/provider_budget.py` builds the Claude rows from
  `engine/budget.json` itself (`rows_from_engine_budget`) and turns a non-`ok`
  window into the `budget-claude` RED: `LOW` when `used > 0.9`, `RUNS OUT BEFORE
  RESET` when the producer's `eta_s` lands before the reset. The tracked module
  has no weekly-percentage rule.
- The router does not read it. `engine_policy._classify_account` judges a window
  by its remaining fraction against a reserve threshold of 15%, so a weekly
  window at 60% or 80% used still classifies as healthy and admission depends
  on the remaining fraction alone.

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
