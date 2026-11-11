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
existing budget view expires readings after fifteen minutes. A missing or stale
producer therefore cannot establish budget readiness. Senses owns acquisition
failures; the `budget-claude` signal routes changed verdicts to its subscribers.

The native report has display precision: whole percentage points and minute
reset times. The result records that precision explicitly. It is not an exact
token allowance or a replacement forecast. The existing LOW and burn-rate rules
remain in effect. A fresh report can still hold spontaneous work RED.

The previous statusLine tap remains historical evidence. Its callback timestamp
establishes a render, while `rate_limits` only appears after an API response in
the session. Do not renew quota freshness merely by redrawing an idle terminal.
The budget view uses the independent native report after its reviewed activation.

References: [native commands](https://code.claude.com/docs/en/commands),
[status line data](https://code.claude.com/docs/en/statusline). Observed native
contract: Claude Code 2.1.319 on 2026-11-06; unsupported future formats fail closed.

## Spending policy (decided; enforced in the budget view, not in the router)

Status: the 50% threshold is enforced in the site's budget view, and the router
does not read it.

- The rule lives in the site-local `operator/budget/budget_view.py`, which is not
  tracked in Git. Its window verdict is `RUNS OUT BEFORE RESET` only when the
  window is at or above 50% used and the burn projection empties it before the
  reset; below 50% the projection is information, not a verdict.
- `src/mishe_tauftauf/provider_budget.py` consumes those verdicts and turns a
  non-`ok` Claude window into RED, so the site's Claude readiness does read the
  weekly figure — through the view's verdict, not through a percentage test of
  its own. The tracked module has no weekly-percentage rule.
- The router does not read it. `engine_policy._classify_account` judges a window
  by its remaining fraction against a reserve threshold of 15%, so a weekly
  window at 60% or 80% used still classifies as healthy and admission depends
  on the remaining fraction alone.

RouterAI is the backup in practice: `engine/routes.json` lists its candidates
after the subscription providers for every role, so it is reached when the
earlier candidates are not admitted rather than chosen first.

The operator decision of 2026-11-11, recorded in the operator notes:

- Subscription windows may be spent freely up to 50% of their weekly limit.
- Above 50% weekly use, Mishe counts and cares about pace. Mishe still decides; the
  decision does not route to the human owner.
- RouterAI is the backup when the subscription providers fail, not a primary route.
- Alerts about budget do not ask the human for anything.
