# Decision models for runtime observation

[Completion judge](completions-judge.md) · [Wall coordination](wall-coordination.md)

Research checked against live primary sources on **2026-10-07**. Availability,
versions and free offers need another lookup before each trial. These are
candidate recommendations, not mishe benchmark results or an activated gate.

System 0 comes first: use deterministic checks, calculations and actions
wherever they can do the work. System 1 must never replace them. Before adding
a model question, name the residual semantic judgment that code cannot settle;
if a deterministic solution becomes available, retire that model question.

Decision models can help identify unsupported causal claims, classify session
behavior and select a bounded investigation. Code continues to own timestamps,
sequence IDs, hashes, test results, scope, admission budgets and the conditions
for claiming delivery. A model verdict cannot manufacture missing evidence or
erase a failure. The [repair requirements](wall-coordination.md) still apply.

## Candidates to evaluate

| Candidate | Access | Proposed first use | Important limit |
| --- | --- | --- | --- |
| Jev 1.13 Free | OpenCode Zen, `jev-1.13-free` | Hosted reference for narrow evidence and session classifications | Free offer is temporary; requires the appropriate Zen access. Record returned version. |
| Nimble 9B | Ollama, `nimble:9b-q4_K_M` | First local text comparator | 5.6 GB published download; effective decision prompt limit is 8,192 tokens, including schema. Measure memory and latency here. |
| Tev1 4B | Ollama, `tev1:4b` | Lower-memory text comparator | Experimental; prompts operate around 2,000 tokens. |
| Tev1 0.8B | Ollama, `tev1:0.8b` | Cheap triage baseline | Expect more missed distinctions; require measured benefit over deterministic triage. |
| Clef-flash 9B | Ollama, `clef-flash` | Screenshot classification and text/visual disagreements | Requires Ollama 0.35.1+; default published download is 11–12 GB. Fit is uncertain on this shared 12 GB GPU. |
| Clef 27B | Ollama, `clef` | Larger multimodal comparison when resources permit | About 18 GB published download; not the first local candidate on this host. |

Zen documents the System One route and the free Jev selector. Its live model
catalog also returned `jev-1.13-free` during this investigation. General free
models use other protocols: for example, MiMo-V2.6-Flash Free uses Chat
Completions, while Muse Spark Contributor uses Responses. Those are separate
generative comparators, not interchangeable System One backends. Check each
model's current terms and endpoint rather than assuming a common free tier.
[OpenCode Zen](https://opencode.ai/docs/zen/)

Nimble is a Qwen3.5-9B fine-tune that scores supplied answer choices. Its latest
upstream checkpoint and probability settings have changed since the original
release, so old benchmark calibration does not transfer automatically. The
Ollama catalog's generic context badge is not its decision prompt contract.
[Nimble model](https://ollama.com/library/nimble),
[explicit quantizations](https://ollama.com/library/nimble/tags),
[upstream release notes](https://github.com/bespokelabsai/nimble#updates)

Tev1 has smaller memory requirements, but its vendor calls out incomplete
calibration, adversarial and out-of-distribution evaluation. The 0.8B model is
a resource baseline rather than the preferred judge for closing incidents.
[Tev1 model and limitations](https://ollama.com/library/tev1)

Cloudflare introduced Clef and Clef-flash on October 1. They support images and
a 64K decision context. Its reported agent-trace workflow accuracy was Jev
71.6%, Clef-flash 69.8%, Clef 68.5%. That vendor-run comparison is relevant to
this use case but does not establish a winner on mishe incidents. Published
latency elsewhere is not a prediction for this GPU.
[Cloudflare announcement and evaluations](https://blog.cloudflare.com/clef-decision-models/),
[Clef-flash in Ollama](https://ollama.com/library/clef-flash)

## Current access methods

Use **native System One HTTP**, with `state` and named `questions`:

| Service | Endpoint | Model selector | Authentication |
| --- | --- | --- | --- |
| Local Ollama | `http://127.0.0.1:11434/v1/systemone` | Explicit local decision-model tag | None |
| OpenCode Zen | `https://opencode.ai/zen/v1/systemone` | `jev-1.13-free` | Zen bearer key |
| Direct TypeSafe | `https://api.typesafe.ai/v1/systemone` | Request `jev-1.13.0` only after evaluation; availability and immutable weights are unverified | TypeSafe bearer key; separately priced |

Ollama requires 0.35+ for Nimble/Tev1 and 0.35.1+ for Clef. It currently
documents local decision serving; ordinary Ollama cloud chat availability does
not imply cloud System One support. Native requests have no streaming, tools
or generation controls. Text-only request bodies are limited to 64 KiB; ask at
most 64 questions, and check the actual model prompt limit. Questions do not
receive previous answers. Local usage may count shared input repeatedly when
questions are scored separately. Prefer direct HTTP or the TypeSafe SDK while
Ollama's examples and SDK-support notes remain inconsistent.
[Decision guide](https://docs.ollama.com/capabilities/decision),
[System One API contract](https://docs.ollama.com/api/systemone)

Jev supports `noul`, `choice` and `score`; its direct-service model contract has
32K for state plus the longest question and 64K for the complete request. These
direct-service limits are not proof of the free Zen account's quota. Model
names are requested selectors, not evidence of account availability or
immutable weights; record the returned identity and re-evaluate on version
change.
[TypeSafe model contract](https://docs.typesafe.ai/models)

The existing `jev_judge.py` uses this HTTP shape for a single Noul. Its base URL
would be `https://opencode.ai/zen` for Zen or the loopback Ollama origin for
local serving, since it appends `/v1/systemone`. Its environment names are
`TYPESAFE_BASE_URL`, `TYPESAFE_MODEL` and `TYPESAFE_API_KEY`; supply the key for
the selected provider, not an unrelated subscription. It is a reference adapter,
not a ready multi-question production integration: review its total deadline,
response bounds, identity checks, usage and failure reporting first. The
Chat Completions adapter cannot invoke this endpoint unchanged.

At inspection, this host had Ollama **0.33.2**, no decision models installed and
a 12 GB RTX 3060 with roughly 9 GB free. Its local System One route returned
404. Upstream lists [Ollama 0.40.0](https://github.com/ollama/ollama/releases/tag/v0.40.0)
as latest. No Zen/TypeSafe key was found in the checked environment or usual
OpenCode credential store; OpenCode Go credentials are a separate service.
Public API access initially returned 403 with Python's default user agent;
the Zen model-list GET succeeded with an explicit research user agent. None of
these checks proves authenticated inference access. No shared daemon was upgraded.

## Bounded runtime use

Give the model a small evidence packet and ask atomic questions:

- Does the supplied trace support this specific proposed causal explanation?
- Does the cited check exercise the named recurrence condition?
- Does the changed predicate exclude a failure class without replacement evidence?
- Does this session excerpt describe new work, repeated reconciliation, or an open uncertainty?
- Which supplied evidence-producing action should be considered next?

These are review signals. Extended causal investigation remains a reasoning
task. Compute ages, counts, thresholds, quota arithmetic and wake transitions
in code. Jev's October 2 limitations explicitly cover numerical precision,
date comparisons, distracting context, option-order sensitivity and adversarial
state. Typed output establishes shape, not factual correctness.
[Jev 1.13 limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

Every decision should bind the incident, evidence hashes, question version,
endpoint, model/weight identity, response, latency, usage and disposition.
UNKNOWN, timeout, malformed output, budget exhaustion and model disagreement
must retain the incident and reach a bounded resolver action. Keep the observer
independent of the repair and monitor its freshness and failed alert delivery.
Never let a confident semantic verdict override failed deterministic checks.

## First evaluation

Start with shadow decisions over saved, owned evidence; the decisions have no
effect on settlement, activation, suppression or incident closure. Compare Jev
with Nimble Q4 and Tev1 4B on identical small packets. Add Clef-flash only for a
separate visual task. Record requested and returned model identity and the
exact question contract for each run. Evaluate the complete model, question, answer variants and
context combination; a published ranking does not select that combination.

First run the deterministic baseline and remove questions it already answers.
Keep expected labels independent of candidate prompts and model outputs. On
the remaining semantic cases, vary question wording, answer wording and order,
and context separately before testing their interactions. Use equivalent
paraphrases, evidence at different positions, irrelevant distractors, missing
evidence and contradictions. Equivalent framing should preserve the answer;
missing or contradictory evidence may require uncertainty. Measure whether
framing changes matter more than model changes here rather than assuming
either dominates.

Use contrastive pairs that change one decisive fact: a recurrence check passes
or fails; the monitor is fresh or stale; a peer keeps chatting while the subject
is silent; an idle session did or did not yield; suppression does or does not
retain replacement coverage. Include incomplete evidence, injected instructions
and shuffled choice order. Report false acceptance, missed failure, abstention,
pair consistency, framing sensitivity, worst observed false acceptance, latency
and resource use per failure class and configuration. Keep prompt, option,
context and threshold selection separate from held-out evaluation; freeze that
whole contract before testing it. A small smoke suite proves only
that suite, not calibration or deployment readiness.

Promotion needs an independent reading, deterministic failure-path and routing
checks, observable activation, tested revert and the reviewed recovery exercise.
Continue checking decisions after promotion; re-open on recurrence, broken
monitoring, changed model identity or changed question policy. Persisting raw
failure evidence lets mishe learn from mistakes instead of becoming quiet when
a judge or retry hides them.

## Budget telemetry found during this research

Ollama now documents authenticated `GET /api/balance` and `GET /api/usage` on
ollama.com. Balance exposes included/purchased credits and monthly periods;
legacy plans expose session/weekly remaining percentages and reset times.
Usage can be delayed, and legacy token/cost fields may be omitted. Both APIs
allow 10 requests per minute per user and return `Retry-After` with 429.
Their availability changes telemetry feasibility, not current account evidence:
no authenticated balance was read here. Keep operator allocations separate,
reserve concurrent spend in code, and retain UNKNOWN for unavailable readings.
[Balance](https://docs.ollama.com/api/balance),
[Usage](https://docs.ollama.com/api/cloud-usage)
