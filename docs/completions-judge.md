# Completion judge adapter

This adapter uses an explicitly selected OpenAI-compatible **Chat Completions**
endpoint (`BASE/chat/completions`), not the legacy text completions endpoint.
Native System One models use a different protocol and go through
`src/mishe_tauftauf/decision_provider.py`; its envelope contract is
covered offline by `tests/test_decision_provider.py`.
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
env PYTHONPATH=src python3 -m mishe_tauftauf.completions_judge \
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
A single `desired-state-met` verdict conflates dimensions that the source separates as Accuracy, Helpfulness and Language (Wu & Aji arXiv:2307.03025v3, PDF 972455 B sha256 b748c8f345ef06e63c147710c4bbd6ca71dad1cf9930d2efd705ce5314c21c, pp.1-5 Table 1/3/4, 15 pp.): Table 1 shows GPT-4 ranking Several Minor Factual Errors (1206) above Correct+Short (1096) and crowd clustering factual-error Elos near Correct (1025-1074 vs 1091); Table 3 \u03BA shows only LLM-LLM moderate agreement (0.51) with human-LLM 0.08-0.14; MERS (Table 4) restores factual discrimination for GPT-4 when scored on Accuracy separately (Several Major 794 vs Language 995) but not for crowd (1030 vs 1023). For this adapter that is the analogue of asking one categorical `desired-state-met` question: a judge can rank fluent confident prose above terse correct evidence, so treat a single `yes` as dimensionally conflated. Limits: Elo K=32 10k shuffles over 40 Vicuna-subset questions \u00D7 12 GPT-4-generated settings with manual post-edit, crowd 377 MTurk US / expert 20 on 200 games / LLM gpt-4-0613+Claude-1.3 with Dettmers prompt; MERS gain reported for GPT-4 Accuracy, not crowd; ordering transfers, not scale, to this plant\u2019s categorical 1/0 (`unknown` on missing/stale/contradictory).


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
env PYTHONPATH=src python3 -m mishe_tauftauf.completions_judge \
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
Foundational biases measured pairwise do not transfer verbatim to this pointwise judge. Zheng et al. arXiv:2306.05685v4 (PDF 1.6 MB sha256 157498..., pp.4-7) report pairwise position consistency 23.8%/46.2%/65.0% default (GPT-4 65%->77.5% few-shot at 4x cost) with conservative swap-twice-then-tie mitigation, verbosity failure 91.3%/91.3%/8.7% on 23-answer repetitive-list (vs identical ties), math 14/20->3/20 with reference-guided, and S2 85% pairwise GPT-4/human agreement varying 70%->100% with win-rate disparity (Fig.2). All are pairwise two-answer comparisons; this adapter is pointwise categorical, so position-swap and reference-generation mitigations have no literal analogue here except the inside-state-block TOP PAIN<->EVIDENCE permutation check described above. Limits: pp.1-7 verbatim; Appendix D-F not re-measured; no re-run on this judge.


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

Why pointwise. Tripathi et al. COLM 2025 arXiv:2504.14716v2 (PDF 484742 B sha256 125715..., 19 pp., pp.1/3-5) defines distracted evaluation as R(y|x,p)!=R(y_f|x,p) where p(y)=p(y_f) and measures pairwise flips in ~35% of cases vs ~9% for absolute scoring across IFEval-TweakSet and MT-Bench (Fig.1, S4). The three distractor types (assertiveness, prolixity, sycophancy) correspond to this book's confident-claim, length-padding and fluency checks; the residual 9% and the absolute limits (lack of comparative context, calibration inconsistency, S2.2) mean those checks remain necessary even after choosing pointwise. Limits: 1..7 Likert weighted scoring vs tie-inclusive pairwise on those datasets; this plant's yes/no/unknown is coarser and `not calibrated probabilities`, so 35%/9% ordering transfers but not scale; a single adapter on fixed pairs cannot show discrimination without cross-adapter comparison (Campbell & Fiske 1959).

The adapter maps yes/no to `probability 1` / `probability 0` solely for the
existing executable protocol. Those endpoints are **categorical conventions,
not calibrated probabilities**. Unknown, malformed output, refusal, token-limit
termination and transport failure return `unknown`.

Witness coordination uses the live `witness_analysis` advisor. This adapter
provides judge evaluation and provider diagnostics; it does not select witness
analysis tasks.

## Identity gate for a pinned wrapper

This checkout has no `run --judge` option, and `judges.run_external` has no caller
under `src/` — the CLI imports it but never calls it — so this adapter is not selectable as
a judge in `run`. Selecting it for live dispatch is a separate change that needs its own
review.

The identity gate still works on its own. `--identity` prints a hash over the
base URL, model, timeout and token limit, the judge prompt, the response format,
temperature, the categorical mapping and the adapter source bytes; credentials
are excluded. A wrapper that passes `--expected-identity HASH` returns `unknown`
without a network call when the current hash differs. Any change to those inputs
changes the hash, so refresh the controls after each change. A provider's
mutable model alias can still change remotely; prefer a pinned version and
refresh controls periodically.
