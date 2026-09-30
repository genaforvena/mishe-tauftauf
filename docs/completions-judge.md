# Optional completion judge trial

This adapter uses an explicitly selected OpenAI-compatible **Chat Completions**
endpoint (`BASE/chat/completions`), not the legacy text completions endpoint.
It has no third-party dependencies and does not change the default judge.
The provider must support system/user messages, `response_format: json_object`,
`temperature: 0`, and `max_tokens`. Unsupported settings fail visibly; there is
no automatic provider or model fallback. The request envelope follows the
[Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Supply the provider's base URL including its API prefix (usually `/v1`) and a
model explicitly. Authentication uses `COMPLETIONS_API_KEY` or
`COMPLETIONS_KEY_FILE`. Do not put a secret in command arguments, reports or feed
entries. HTTPS is required except for loopback HTTP; redirects are refused.

## Judge smoke controls and replay

From the installed checkout, substitute your actual base URL and model:

```bash
.venv/bin/python -m mishe_tauftauf.completions_judge \
  --base-url https://YOUR-PROVIDER/v1 --model YOUR-MODEL \
  --shadow .mishe-tauftauf/artifacts/completions-controls.jsonl \
  --max-calls 12
```

This runs the six existing positive/negative smoke-control pairs. Exit 0 means
all twelve categorical answers matched; this is not a calibration or production
readiness claim. Exit 1 means controls failed; exit 2 means local configuration
or input failed. Reports are created privately and never overwrite a prior
attempt. Nothing dispatches or writes to `chat.log`.

Add `--cases PATH --max-calls N` to replay a JSONL file whose lines contain
`{"document":"QUESTION ...", "expected":"yes"}`. Construct documents with
`mishe_tauftauf.judges.document`; expected can be yes/no/unknown or omitted.
N must cover the twelve controls plus all replay cases, and cannot exceed 100.
Failed controls skip replay cases. Every evaluation makes at most one HTTP
request with a default 20-second total network deadline, a bounded input and
output, and no retry. An isolated transport process covers connection setup,
headers, body and chunk framing within the total deadline; it is killed and
reaped on expiry. Credentials travel over private stdin, never command arguments.
A server that trickles bytes cannot extend the budget; an exceeded deadline yields
UNKNOWN. These bounds limit calls, not dollar spend: use provider billing limits
for a spending cap. Multiple invocations each have their own call allowance.

Reports contain input hashes, categorical answers, expected outcomes, latency,
token usage when supplied and the returned model. They omit source documents
and raw completion/error bodies. Compare labeled failures per question,
especially false `desired-state-met`, rather than just aggregate accuracy.

The adapter maps yes/no to `probability 1` / `probability 0` solely for the
existing executable protocol. Those endpoints are **categorical conventions,
not calibrated probabilities**. Unknown, malformed output, refusal, token-limit
termination and transport failure return `unknown`.

## Witness analysis selection from chat.log

The witness experiment reads a canonical feed window and asks one question:

> Which witness analysis, if any, is warranted by the new chat entries?

“Warranted” means a specific coordination issue deserves investigation, remains
unresolved, and new evidence adds something beyond the last completed analysis.
An accepted task already being pursued normally does not warrant an unresolved
request analysis; routine repeated checks alone do not establish a loop.
The question is fixed; definitions are instructions, and the log delta plus
previous analysis are separate state. It chooses one analysis:

| Analysis | Reason to select it |
| --- | --- |
| `unresolved-requests` | An operator wish appears to lack follow-through |
| `evidence-audit` | Consequential claims conflict or lack referenced checks |
| `progress-loop` | Repeated activity appears to produce no progress |
| `ownership-review` | Ownership appears missing or conflicting |
| `none` | Routine changes, or an issue already analyzed without new evidence |
| `unknown` | A decision could not be established |

```bash
.venv/bin/python -m mishe_tauftauf.completions_judge \
  --base-url https://YOUR-PROVIDER/v1 --model YOUR-MODEL \
  --witness-home .mishe-tauftauf --since CHECKED-SEQUENCE \
  --last-analysis .mishe-tauftauf/handoffs/witness.md \
  --shadow .mishe-tauftauf/artifacts/witness-completion-trial.jsonl
```

`--since` is the sequence through which the previous witness analysis checked
the feed. The snapshot includes up to 20 preceding entries as context and all
new entries (at most 100), plus optional previous-analysis text. It refuses an
oversized window rather than truncating or advancing a cursor. The selected
window and previous analysis are sent to your chosen provider; use suitable
local or hosted endpoints for that data. Reports retain hashes and sequence
references rather than raw log entries. No new entries deterministically yield
none without a provider call. Each nonempty witness trial makes at most one call.

A named analysis must cite at least one new entry and can only cite supplied
sequence numbers. This guards the routing envelope, not the truth of its
interpretation. The trial never launches an analysis, mutates the feed, records
an analysis as completed, changes witness's charter or advances a persistent
cursor. Compare its choices with actual witness outcomes before wiring it.
Feed-rate limits, stale-pane checks, task deadlines and active-wake reservations
remain deterministic obligations; this semantic gate must not suppress them.

For a future live gate, record the last **completed** analysis type, source
range and result, and suppress a repeat only when no material new evidence has
arrived. Treat API UNKNOWN as a visible need for the existing witness path;
do not treat it as a skip. Reserve one analysis attempt through the existing
task/wake lifecycle so two polls cannot dispatch the same work. Before live
use, add labeled routing controls and test missed triggers and repeat rate.

## Explicit external-judge selection after evaluation

To use the adapter with `run --judge`, make an executable local wrapper that
invokes the module with fixed base URL, model, timeout and token limit. Include
`--expected-identity HASH`, where HASH is produced with the same arguments and
`--identity`. An absent or changed identity returns UNKNOWN without a call.
Identity covers endpoint, model, inference settings, prompts and adapter source
bytes, but not credentials. The wrapper bytes must change when that identity
changes, invalidating the existing control cache. Refresh controls after each
change. This also catches changes to the imported adapter source despite an
unchanged wrapper. A provider's mutable model alias can still change remotely;
prefer a pinned version and refresh controls periodically.

Do not activate a live judge merely because its smoke controls pass. The first
trial leaves all live selection unchanged. Rollback is removing the explicit
wrapper selection; deleting the report alone changes no behavior.
