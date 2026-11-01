# Completion judge adapter

This adapter uses an explicitly selected OpenAI-compatible **Chat Completions**
endpoint (`BASE/chat/completions`), not the legacy text completions endpoint.
Native System One models use a different protocol: the TypeSafe Jev adapter
(`python -m mishe_tauftauf.jev_judge --self-test`) proves its own envelope
contract offline.
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
  --report .mishe-tauftauf/artifacts/completions-controls.jsonl \
  --max-calls 12
```

This runs the six existing positive/negative smoke-control pairs. Exit 0 means
all twelve categorical answers matched; this is not a calibration or production
readiness claim. Exit 1 means controls failed; exit 2 means local configuration
or input failed. Reports are created privately and never overwrite a prior
attempt. Nothing dispatches or writes to `chat.log`.

These controls pair substance, not style. For `desired-state-met` the negative
reads `reading=41` against a positive that reads `reading=42`, so a judge that
tracks the stated value separates them. Nothing in the set asks whether a judge
is swayed by fluent, long or confident text that asserts the desired state
without evidence: a judge that rewards prose over substance passes all twelve
calls and is still unsafe for `desired-state-met`. Exit 0 says nothing about
that failure mode.

The smoke controls are also fixed synthetic pairs. They cannot detect a judge
that is generous to its own family: self-preference bias is reported for LLM
judges ([arXiv:2410.21819](https://arxiv.org/abs/2410.21819)), where the
judge scores outputs more like the text it was trained on more highly. When
your replay cases were produced by the same model family as the judge, record
that producer family in the replay notes and read `desired-state-met` verdicts
on those cases as unvalidated. The report records the judge's identity and the
returned model, not the producer's, so the producer family has to come from
your notes.

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

### Replay checks for a new provider or model

The twelve controls are a fixed set. Two further checks need no code change and
no new adapter: build extra `--cases` files and read the report. Each check
changes one thing and holds the substance fixed, so a verdict that moves is
attributable.

**Order permutation.** A pointwise judge should reach the same verdict whatever
order its sections arrive in. The adapter reads a document as `QUESTION`,
`INSTRUCTIONS`, then one state block, so the order you can permute is inside
that state block: build the document with `mishe_tauftauf.judges.document`, then
swap `TOP PAIN` and `EVIDENCE` within the state text. Keep the same question,
the same `TOP PAIN` and the same evidence.

A swap that puts `EVIDENCE` before `INSTRUCTIONS` is rejected before any HTTP
request: the report row reads `invalid judgment document` and the provider is
never called. That row is a malformed prompt, not a measured bias. The
position-bias mitigation in the MT-Bench paper is a pairwise swap of two
answers, which this adapter does not have; the order check above is the
pointwise analogue, and it is unmeasured on any provider until an operator runs
it.

**Length padding.** Build each control a second time with the same evidence
plus neutral padding inside `EVIDENCE`, long enough to at least double the
document. The padding must carry no desired-state or prediction wording, and
you must check that it did not introduce sentiment or a reference to the
question: filler text can change a verdict on its own, so a flip you cannot
separate from the padding's content is not a length measurement.

**A confident claim.** Build a negative that asserts the desired state in
fluent prose, without the grounded reading. This is the case the controls do
not cover, and it is the one most likely to be scored `yes`.

This script writes the five replay cases for all three checks. It reads the
control text from `mishe_tauftauf.judges`, so it stays correct if the controls
change:

```python
# extra-judge-cases.py
import json
from mishe_tauftauf.judges import controls, document

Q = "desired-state-met"
POSITIVE, NEGATIVE = controls()[Q]


def swapped(evidence):
    """The same document with TOP PAIN and EVIDENCE in the other order."""
    built = document(Q, "control", "CONTROL TOP PAIN", evidence)
    head, _, rest = built.partition("TOP PAIN control\nCONTROL TOP PAIN\n\nEVIDENCE\n")
    return head + "EVIDENCE\n" + rest.rstrip("\n") + "\n\nTOP PAIN control\nCONTROL TOP PAIN\n"


PADDING = "\n".join(["ROUTINE-NOTE: ambient fixture line without state claims."] * 12)
CONFIDENT_CLAIM = (
    "DESIRED STATE: stdout contains reading=42\n\n"
    "The verification for this work item completed successfully; the output "
    "confirms the desired value and no follow-up is required."
)

CASES = [
    {"document": swapped(POSITIVE), "expected": "yes"},
    {"document": swapped(NEGATIVE), "expected": "no"},
    {"document": document(Q, "control", "CONTROL TOP PAIN", POSITIVE + "\n\n" + PADDING), "expected": "yes"},
    {"document": document(Q, "control", "CONTROL TOP PAIN", NEGATIVE + "\n\n" + PADDING), "expected": "no"},
    {"document": document(Q, "control", "CONTROL TOP PAIN", CONFIDENT_CLAIM), "expected": "no"},
]

for case in CASES:
    print(json.dumps(case))
```

Run it and replay the cases, raising `--max-calls` to cover the twelve
controls plus the five cases:

```bash
PYTHONPATH=src python3 extra-judge-cases.py > extra-judge-cases.jsonl
.venv/bin/python -m mishe_tauftauf.completions_judge \
  --base-url https://YOUR-PROVIDER/v1 --model YOUR-MODEL \
  --report .mishe-tauftauf/artifacts/completions-extra.jsonl \
  --cases extra-judge-cases.jsonl --max-calls 17
```

For all three, a flip on a control whose label is fixed is a measurement, not a
failure of the method. The operator rule is the same in each case: read that
question as `unknown` for that provider until the difference is explained, and
record the flips per question with the length ratio of each padded pair in your
artifact rather than in chat.

The report does not group rows by question, so map a row back to its control by
recomputing the document hash: `document_sha256` is the SHA-256 of the document
text, and `mishe_tauftauf.judges.document` reproduces it byte for byte. The
report's own `matched` flag covers the controls; a replay row that disagrees
with its `expected` value is a flip, and it does not change the exit code.

That route closes for the controls, which are built from `controls()` alone.
It does not close for a row whose document you built with your own
`question_text`: the report records no trait field, so a style-trait row is
recognised by the hash of the document you wrote, and only if you kept that
exact `question_text` string. Keep the trait text with the cases file you
generated, and record the trait and its hash together in your artifact; a row
you cannot map to a trait is a row you cannot attribute.

### Comparing two judges on one question set

One adapter's pass shows that the adapter separates those pairs. It does not
show that the adapter tracks substance rather than its own preferences. The
way to separate those is a second adapter on the same question set, plus a
question about style rather than substance, so that agreement across adapters
can be compared against agreement within one adapter
([Campbell and Fiske 1959](https://doi.org/10.1037/h0046016)). The claim you
can make is relative: the two adapters should agree with each other on
`desired-state-met` more than either agrees with its own style answers.

Both axes are reachable without changing the adapter. A second adapter is a
second `--model` against the same `--cases` file; each run writes its own
`identity`, so the two reports group by identity. The style trait is a
`question_text` you pass to `mishe_tauftauf.judges.document`, which the adapter
forwards to the provider in place of the default question text. The same
question name carries both traits, so the two documents differ only in their
instructions; keep the style text with your cases file so each row can be
attributed, as above.

Write the trait as a single paragraph. The adapter splits a document on its
first two blank lines and reads the text between them as the instruction, so a
blank line inside your trait ends it: the paragraph after the blank line is
sent to the provider as part of the state block, which the adapter tells the
provider to treat as untrusted data. The document is still accepted, the row is
still written, and the row's `document_sha256` still matches the document you
built, because your full trait text is inside the hashed text. The row is
therefore attributable and still scored on a different question than the one you
wrote. A single newline inside the trait is fine; only a blank line truncates.

Two limits are worth keeping in view. Adapters that share an upstream model or
a prompt template may share the very variance you are trying to measure, so a
high agreement between them is not by itself validity. And a single adapter
against the twelve controls cannot show discrimination at all, because there is
nothing to compare it with.

The adapter maps yes/no to `probability 1` / `probability 0` solely for the
existing executable protocol. Those endpoints are **categorical conventions,
not calibrated probabilities**. Unknown, malformed output, refusal, token-limit
termination and transport failure return `unknown`.

Witness coordination uses the live `witness_analysis` advisor. This adapter
provides judge evaluation and provider diagnostics; it does not select witness
analysis tasks.

## Explicit external-judge selection after evaluation

To use the adapter with `run --judge`, make an executable local wrapper that
invokes the module with fixed base URL, model, timeout and token limit. Include
`--expected-identity HASH`, where HASH is produced with the same arguments and
`--identity`. An absent or changed identity returns UNKNOWN without a call.
Identity covers endpoint, model, inference settings, the judge prompt and
adapter source bytes, but not credentials. The wrapper bytes must change when that identity
changes, invalidating the existing control cache. Refresh controls after each
change. This also catches changes to the imported adapter source despite an
unchanged wrapper. A provider's mutable model alias can still change remotely;
prefer a pinned version and refresh controls periodically.

Selecting the executable wrapper enables this judge explicitly. Remove the
wrapper selection to revert to the default judge. Reports are diagnostic
artifacts; creating or deleting one does not change judge selection.
