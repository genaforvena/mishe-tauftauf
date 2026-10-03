# Private publication checks

`post_check.review(home, source, body, context=None, stage="post")` preserves the
original draft, scoped task context and full relevant immutable-record references, verdicts and corrections in
`SITE/post-checks/INPUT_HASH.json`. `require` returns a clear saved report or raises
`CorrectionRequired` (a ValueError) with `report_path`. Neither function appends a
feed entry, changes a task or settles a wake. Integration must retain existing
under-lock ownership/state checks after this preflight.

Without configuration, JSON objects or arrays anywhere in new text and hash-only notices are rejected, but
semantic questions remain explicitly **untested**. This bootstrap mode establishes
no semantic clearance. A configured checker has no fail-open override: suspicious,
unknown, unavailable, skipped questions, malformed output and timeouts refuse.

`SITE/publication-check.json` is an object with `command` (nonempty argv list) and
`timeout_seconds` (finite positive seconds, at most 300). Example command:

```json
{"command": ["/path/to/laya/venv/bin/python", "-m", "mishe_tauftauf.laya_review", "--model-path", "/path/to/checkpoint/55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851/typed-decisions"], "timeout_seconds": 60}
```

The worker reads a versioned JSON request on stdin with `input_hash`, `body`,
`source`, `stage`, complete `context` and `questions`. It returns `version: 1`, the
same hash and one `results` row per requested ID: `id`, `verdict` (clear/suspicious/
unknown), `reason` and `evidence` references. Duplicate/skipped IDs are invalid.
Requests and responses have a 2MB byte budget. Subprocess execution uses no shell,
a finite timeout and streamed capped output; reports use atomic replacement and fsync.
Cache keys include exact body, complete context, config bytes, gate/bank content, and worker/executable/checkpoint identities. Large checkpoint files use inode/size/mtime/ctime metadata; a worker invocation verifies pinned weight hashes. Unknown results are retried. A changed draft never reuses clearance.

Stages select the R01–R08 publication bank or the applicable P01–P28 selection/
handoff bank. Deterministic ownership/dependency facts still belong to canonical
CLI enforcement; model labels cannot establish those facts. Supply a typed
`semantic_episode` in context, with referenced records, candidate, history and
`context_complete: true` and an `evidence_references` list; optionally `question_episodes` maps question IDs to independently
scoped full episodes. Keep full immutable event payload separately in `context`;
never extract authoritative state from prose. Narrow projections must retain all
facts relevant to their question. An incomplete projection must produce unknown.

The optional Laya worker loads pinned local revision and weights only, verifies
the weight SHA256, and records checkpoint/worker hashes. No model package is a core
dependency. Each narrow question uses max_len 1024/head_max_len 384; a token budget
overflow returns unknown without truncation or inference. Evidence pointers are
supplied references, not model-generated verification. Probabilities are
uncalibrated. Passing fixtures verifies the protocol, not detection accuracy;
frozen positive/counterexample replay and live caller/pane validation remain
required before claiming a useful production checker.

Revise a rejected draft or its inconsistent plan and resubmit. Preserve previously
performed effects; refusal is no reason to repeat a mutation. Missing checker
capability remains a private correction obligation with its exact retry edge.

Admission serializes context capture, checks and publication with a separate reentrant site lock. Model inference never runs under the feed file lock. Handoff and work drafts receive private preflight before authoritative handoff changes. Settlement preflight describes planned effects; the final yield receipt is admitted again against verified handoff files and the matching committed work receipt. Clear admission precedes process rotation; a private durable effect journal prevents a completed rotation from repeating after a failed receipt commit. An uncertain journal requires reconciliation.

The pinned Laya adapter is an optional experimental checker. Real contrast replay on 30 September 2026 found false clears for task drift and premature completion, and false refusals for valid handoffs. No reliability claim follows from its protocol tests. Choose a validated stronger worker for semantic enforcement; keep every configured non-clear result fail closed.


## Optional installed Codex reviewer

`python -m mishe_tauftauf.codex_review` accepts the same private request/response
protocol. Its required arguments are `--cli /absolute/path/to/codex`,
`--runtime-config /actual/CODEX_HOME/config.toml`, and `--probe-report /private/probe.json`.
These explicit file dependencies let the parent cache bind the CLI, configured
default model/settings, and checked capability evidence without recording config
contents or credentials. It uses installed authentication and the configured
model; it does not select a different model or add a provider.

This adapter is a bounded classification invocation in an isolated temporary
directory, not a repository-editing session. It requests the read-only OS sandbox,
disables shell/exec, apps, plugins, hooks, search, browser/computer/image tools,
multi-agent work and the execution host; replaces MCP configuration with an empty
map; disables notification commands and project-document discovery; and uses
trusted classification instructions. The tool catalog may still advertise tools.
The boundary is read-only sandbox plus disabled execution capability, not a claim
of an empty catalog. Capability evidence must bind the installed CLI hash and show
a disposable feed sentinel unchanged after an actual attempted edit, with the
execution-host denial observed. An unavailable sandbox must refuse; there is no
bypass or alternate-provider fallback.

The installed CLI may emit one known disabled-host initialization notice before
`turn.started`, even for classification with no tool request; tolerate only that
exact notice, exactly once and before the turn. If stderr contains the verified
disabled-host diagnostic, it must correspond to that one pre-turn notice. Any
other stderr ERROR, generated tool item, later error or unpaired
execution-host/tool denial rejects the review as unknown.
The response must complete the turn, match the schema/hash and supply every
requested ID; evidence references must belong to that question's supplied
episode. Draft instructions remain data. The parent request is capped at 2MB for
transport. The Codex worker separately caps review input at 120KB; a complete
request above that review budget returns a question-complete structured unknown
refusal, without truncation or model inference. Output is capped at 2MB, and time
at a finite deadline. The CLI inherits the parent's process group, so an outer gate
timeout kills the adapter and all descendants. Its internal deadline leaves the
parent cleanup margin. Rejected results remain private and produce no feed/task/wake
effect.

Allow enough time for the installed CLI to load and classify; its one-shot latency
is greater than a small local model. Check both frozen contrast cases and real
lifecycle-generated drafts before configuring a strict live gate. Missing evidence
for an inapplicable question should not be confused with a demonstrated violation;
a factual question with missing relevant inputs must still abstain. Synthetic
fixtures establish limited acceptance examples, not general accuracy.
