# Observable analysis advice

Witness can ask a local Laya model which investigation to try. Advice changes
focus, never wake admission, work ownership, completion checks or recovery rules.
`none` means no particular analysis stands out; it does not cancel the wake.
The mind can override any suggestion after reading the full evidence.

The basic question is **Which witness analysis would be most useful now?**
Definitions distinguish unresolved requests, evidence audits, progress loops and
ownership reviews. Unknown context remains explicit. The optional site file
`analysis-advisor.json` can change the question and these definitions without
changing the worker; every decision records the configuration hash.

## Optional setup

Core remains dependency-free. Use a separate environment with the tested
`laya==0.3.20`, `transformers==4.57.1` and CPU `torch==2.8.0`; this does not claim
compatibility with the existing older `laya` project extra. Download
`convaiinnovations/laya`, revision
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, subfolder `typed-decisions`.
The worker runs offline and checks its weights against the pinned SHA256.

Configure the site with absolute executable and worker paths:

```json
{
  "command": ["/path/to/laya-venv/bin/python", "/path/to/laya_analysis.py", "--model-path", "/path/to/typed-decisions"],
  "timeout_seconds": 60
}
```

Use `python -m mishe_tauftauf.witness_analysis --home SITE --wake NUMBER` on an
actual canonical witness wake. Repeated calls return that wake's original advice;
configuration changes take effect on the next wake. This deliberately keeps
model loading off the supervisor and refreshing top pane. Optional site wrappers
`SITE/bin/witness-analysis` and `SITE/bin/handoff-check` can call the corresponding
modules or source-bound script copies with the site's installed runtime imports.
Record hashes and versions when installing such copies.

The compact input contains the six most recent external entries since the last
completed witness receipt, with 160-character excerpts, and 240 characters of the
previous completed handoff. This is a deliberately basic starting point, not a
validated semantic summary. Full body hashes, source sequence numbers, omission
counts and the original log path remain in the report. The worker checks its
actual token budget and never silently truncates it. The mind must read omitted
sources; initial diagnostic results have not established real-log accuracy.

## Feedback and visibility

Advice files under `SITE/analysis-advice/NUMBER.json` record source/config/input
hashes, model and checkpoint identity, probabilities, token coverage and elapsed
time. A failed worker produces an observable unknown result. No confidence
threshold blocks work. Advice and feedback never append ordinary chat entries.

After the investigation, record the actual choice and evidence:

```sh
python -m mishe_tauftauf.witness_analysis --home SITE --wake NUMBER \
  --used evidence-audit --outcome useful --note 'Artifact/path: what the check established; why the suggestion was accepted or overridden.'
```

Outcomes are `useful`, `routine`, or `inconclusive`; these are mind-reported claims,
not independently verified quality labels. Feedback preserves the original advice
hash and cannot overwrite a previous conflicting outcome. `--status` renders the
latest advice on the existing top pane without model work; keep the base renderer's
exit/check result. The `ANALYSIS ADVISOR:` line is informational, but the witness
observation digest has no special case for it: a status line on the pane counts as
a change and can create feedback wakes.

## Handoff repeat check

Before yielding, run:

```sh
python -m mishe_tauftauf.handoff_check --home SITE --handoff FILE
```

It checks the `Next:` paragraph or a Markdown `Next step` section against canonical
chat text. Case, punctuation and whitespace are normalized; matches require at
least eight words. Exit 2 reports a repeat or missing next step, with matching
source sequences and hashes. This is exact text matching, not semantic duplicate
detection or proof that the work is done. Reconcile earlier effects and the blocker disposition, then write a changed next step or a concrete waiting/retry condition.
A repeat can be legitimate; do not rephrase unchanged work merely to pass.
The command stores its last result in `SITE/handoff-check.json`; `--status`
shows it on the pane and returns UNKNOWN when the handoff bytes change.
The repeat result explicitly asks the mind to revise Next before yielding.

## Improvement on wakes

The general pattern is **small solution, visible decisions, explicit feedback,
then bounded evidence-based repair**. On a real wake, inspect recent advice and
outcomes for systematic wrong focus, missing context, repeats or excess overhead.
Do not tune confidence to make a metric green. Compare a proposed change with the
current version on frozen cases and its live effect; preserve old observations.
Own the paths, obtain independent review, and carry reusable source through
the shared main workflow. Improve one cause when evidence warrants it; do not
rewrite the advisor on every wake or treat agreement as success.

A digest improvement should preserve relevant ownership, retries, contradictions
and completed-analysis context. A menu improvement can change definitions or add
an analysis through reviewed source. Measure real useful findings and repeated
work, with a deterministic deduplication baseline, before claiming benefit.

Disable by removing `analysis-advisor.json` and the optional status/charter
integration. Preserve the decision history for replay. This feature neither uses
nor establishes access to native OpenAI Decisions API.
