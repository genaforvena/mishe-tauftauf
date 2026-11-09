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
