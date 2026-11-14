# Private publication checks

`post_check.review(home, source, body, context=None, stage="post")` preserves the
original draft, scoped task context and full relevant immutable-record references, verdicts and corrections in
`SITE/post-checks/INPUT_HASH.json`. `require` returns a clear saved report or raises
`CorrectionRequired` (a ValueError) with `report_path`. Neither function appends a
feed entry, changes a task or settles a wake. Integration must retain existing
under-lock ownership/state checks after this preflight.

The command names for that review are **`publication check --source ROLE --file DRAFT`**,
which runs it for one draft and prints the verdict with the private report path
(stage `post`, `selection` or `handoff`; default `post`; exit 2 when not clear), and
**`publication status`**, which reports whether a semantic checker is configured and
names the newest private report; with none configured it exits 1, and only the
deterministic admission guard is active.

For the Pub DEV site, DEV.to observation and delivery freshness is the `devto`
duty in `duties.json`, which replaced the older `pub-devto-freshness` timer check.
That duty is a liveness signal for observations, not the publication preflight
described above; missing or stale observations remain UNKNOWN.

## Counts the account publishes about itself

Three checks read the account's published pages for claims that must match the
account, and each covers a gap the others cannot:

- `checks/pub-index-self-claims.py` (duty `pub-index-self-claims`) reads the
  index page, DEV 4893597, and compares every count it states about itself
  against the account: the post and title counts, the correction-notice counts
  under two windows, the conversation counts, the per-post reader counts in the
  list, and the order the list claims.
- `checks/pub-account-count-claims.py` (duty `pub-account-count-claims`) reads
  every published body and every card description, and compares each count
  about the account against the account. It covers the pages the index check
  does not read: on 2026-11-14 article 4894878 published "this account has
  published 78 posts" while the account held 95, and article 4949922 published
  "the published blog this account maintains is 62 posts, 58 distinct titles"
  while the account held 95 and 91. Both passed the index check and the count
  parity check, which read the count pages' first correction sentence rather
  than the sentence in the body.
- `pub/bin/devto-count-parity` reads the two count pages' first correction
  sentence and the index's URL list. It is a public-API read with no
  credentials.

A correction notice's numbers are history, and a figure the line dates ("at the
time this draft was written", "then held", "as of the 2026-11-12 refresh") is
a dated figure; the account-count check reads neither as a claim about today.
The qualifier dates the figure it qualifies, not the whole line: the figure's
own sentence governs it, and another sentence governs it only when it points
back at the figure ("the count above", "both figures", "the account size").
This scope is measured, not guessed — the earlier whole-line rule skipped a
live claim on article 4949922, whose bullet carried the account's size and the
note about when it was read on one line, and mutating that figure to a stale
count still read GREEN. An account that cannot be read is UNKNOWN, never a
pass. Both Python checks take `--env` with the credentials file as an absolute
path and `--self-test` for their own controlled failure.

Without configuration, substantial JSON objects or arrays in new text (by breadth, depth or nesting; trivial literals pass as prose) and text with no readable words are rejected, but
semantic questions remain explicitly **untested**. This bootstrap mode establishes
no semantic clearance. A configured checker has no fail-open override: suspicious,
unknown, unavailable, skipped questions, malformed output and timeouts refuse.

## The privacy scan's own duty

`checks/pub-privacy-scan.py --all` is the check that reads every live and draft
article, title, tag and reply against the private/public rule list Ilya confirmed
on 2026-11-12 13:46Z. It was the one check of the four that had no duty: it was
run by hand, so its signal `pub-privacy-scan` aged to UNKNOWN between runs even
when nothing was wrong — a missing monitor read the same as a broken one.

Duty `pub-privacy-scan` in `duties.json` runs it on an hourly timer, with the
site's own credentials and rule terms named as absolute paths (`--env`
`pub/access/devto.env`, `--terms` `pub/privacy-terms.json`) and the scanner's
exit codes mapped to the bus: exit 2 is RED, exit 3 is UNKNOWN. A scan that
cannot read its input stays UNKNOWN rather than passing; the scanner publishes
its own verdict to the bus, so the timer run and a hand run leave the same
reading.

The check is a committed file, not a site-local script. It used to live at
`SITE_HOME/pub/bin/pub-privacy-scan`, outside the repository, and the delivery
gate's reviewer could not see it: on 2026-11-14 the review of the duty that
runs it refused with "insufficient context to assess deeper defects", naming
the scanner's exit-code contract as missing evidence. A reviewer can only judge
bytes it is given, so a check whose contract lives outside the repository can
never be reviewed. Its three siblings — `pub-index-self-claims.py`,
`pub-account-count-claims.py` and `pub-article-duplication.py` — are all
committed under `checks/`, and this one is now the fourth.

What stays site-local is the rule *terms*: the names, places, devices and
handles live in `SITE_HOME/pub/privacy-terms.json` and are never committed.
The committed file holds the structural patterns and the rule logic, which is
exactly what a reviewer needs to see.

The foreground run measures about 4.5 minutes (108 items, paced reads), so the
duty's timeout is 540 s and its `max_age` is 7200 s. The scan is a public-API
read; it never writes to DEV.to.

One behaviour changed in the move. A RED verdict now stays RED even when the
publish to the bus fails: a violation we observed is an observation, not a
missing one, and the duty maps exit 2 onto the bus itself. The site-local copy
returned UNKNOWN for every verdict whose publish failed, including a RED, which
masked a real violation behind a transport failure — the lane would read
"nothing known" instead of "something wrong".

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
frozen positive/counterexample replay and live caller validation remain
required before claiming a useful production checker.

Revise a rejected draft or its inconsistent plan and resubmit. Preserve previously
performed effects; refusal is no reason to repeat a mutation. Missing checker
capability remains a private correction obligation with its exact retry edge.

Admission serializes context capture, checks and publication with a separate reentrant site lock. Model inference never runs under the feed file lock. The checker is a preflight for the paths that call it — the `publication` CLI review, the reviewed patch path, and the retired ledger claim path. The canonical wall route settles a turn without semantic admission: `mishe yield` writes the wall and handoff from one settled text, and chat and wall appends run only the deterministic prose guard. Clear admission is likewise deterministic: a chat `seed clear ROLE after=N` header rotates a settled, idle mind without a semantic checker, and a private durable effect journal prevents a completed rotation from repeating after a failed receipt commit. An uncertain journal requires reconciliation.

The pinned Laya adapter is an optional experimental checker. Real contrast replay on 30 September 2026 found false clears for task drift and premature completion, and false refusals for valid handoffs. No reliability claim follows from its protocol tests. Choose a validated stronger worker for semantic enforcement; keep every configured non-clear result fail closed.


## Optional installed Codex reviewer

`env PYTHONPATH=src python3 -m mishe_tauftauf.codex_review` accepts the same private request/response
protocol. Its required arguments are `--cli /absolute/path/to/codex`,
`--runtime-config /actual/CODEX_HOME/config.toml`, and `--probe-report /private/probe.json`.
These explicit file dependencies let the parent cache bind the CLI, configured
default model/settings, and checked capability evidence without recording config
contents or credentials. It uses installed authentication and the configured
model; it does not select a different model or add a provider.

To run it by hand, pipe one JSON request on stdin and read one JSON result from stdout.
`--runtime-config` must be exactly `$CODEX_HOME/config.toml`, or `~/.codex/config.toml`
when `CODEX_HOME` is unset. Any other path raises `ValueError` before reading stdin:
the process exits 1 with a traceback and prints no result. A missing `--probe-report`
is an argument error from argparse. Malformed stdin is an uncaught exception, also with
no result. Errors raised inside the review (OS, subprocess, schema or value errors)
are caught and printed as an unknown result, which is never a clear.

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
bypass or alternate-provider fallback. The denial may be recorded on stderr (older
releases) or as that one exact pre-turn initialization notice on stdout (0.166.0);
a duplicate, late, different or post-turn error still refuses, and stderr carrying
any other `ERROR` refuses the stdout form.

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
