---
field: wall-patches-and-operator-views
angle: recent
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/wall_patch.py
---

# Wall patches and operator views — recent: a patch lifecycle has no AgentOps trace

## What we read

- Liming Dong, Qinghua Lu, Liming Zhu, "AgentOps: Enabling Observability of LLM Agents", arXiv:2411.05285v2 [cs.AI] 30 Nov 2024 (December 3, 2024 preprint, 12 pp.). Source: https://arxiv.org/abs/2411.05285 and PDF https://arxiv.org/pdf/2411.05285.pdf fetched 2026-11-02 to /tmp/agentops-2411.05285.pdf (681402 B, sha256 fc2a9aca1107bf4faace5cc18966f4bd85b05109793ca0cc7df6a3fcf88dadf3c, Creator LaTeX with hyperref, Producer pdfTeX-1.40.25, Pages 12, CreationDate 2024-12-03 03:33:44 UTC, extracted 784 lines to /tmp/agentops-2411.05285.txt via pdftotext -layout).
- Read in full: Abstract, §1 Introduction (LLM agents definition, five characteristics: complex artefacts/pipelines, autonomy, non-determinism, continuous evolution, shared accountability), §2 Methodology (GitHub 760 initial repos filtered to 11, Google 6 more, final 17 tools in Table 3), §3 Mapping results (Tables 3-5: Arize 3.9k stars, Langfuse 6.5k, Dify 51.6k, AgentOps 2.1k; all 17 implement Tracing Table 4, guardrails only 6/17), §4.1 Entity-Relationship Model Figure 2 (Knowledge base 0:n -> Agent n:1, Reasoning 1:1 -> Plan 1:1 -> Workflow 1:n Task 1:n Tool, LLM spans 1:n, Evaluation/Guardrails n:1 over all spans), §4.2 Taxonomy Figure 3 (trace = 1+ spans, root span = one request; span operation metadata Name/Start timestamp/Duration/Attributes {Inputs/Outputs, Error type/message/traceback, Metrics {input_tokens, output_tokens}} /Events/Parent ID/Links, plus span-type metadata for Agent/Reasoning/Plan/Workflow/Task/Tool/Evaluation/Guardrail/LLM), §5-§6 threats and conclusion.
- Passages relied on:
  - §1: "Observability refers to the ability to gain actionable insights into the inner workings of an agent by analysing the inputs and outputs (i.e., runtime artifacts) of different components (i.e., design time artifacts), as they flow through the operational pipelines."
  - §3.2.6: "Tracing is the most direct way to enable observability in AgentOps platform. All tools listed in the Table 3 have implemented tracing functionality."
  - §4.2 verbatim span metadata list as above; Table 5 rows for Customization/Prompt Mgmt/Evaluation/Feedback/Monitoring/Tracing/Guardrails.
  - Figure 3 taxonomy reproduced as typeset (Name, Start timestamp, Duration, Attributes, Events, Parent ID, Links; Guardrail action/target, Evaluation test cases/metrics/results, LLM name/version/parameters, Tool name/version/configuration).
- Limits of this reading: systematic mapping study of 17 tools as of November 2024, not a controlled experiment on wall-patch observability; entity-relationship multiplicities (1:1, 1:n, n:1) taken as printed not re-checked against implementations; no re-run of GitHub search or star counts for this plant; quoted passages are extracted text not checked against publisher typesetting.

## What this plant already does

- `src/mishe_tauftauf/wall_patch.py` gates a snapshot change with review, test-mutated-source check, observable activation with visible failures, and a tested revert path; the dashboard's PATCH lines are the operator-facing view of that lifecycle (e.g. `PATCH canonical-workflow-v7: applied delivery=verified`, `PATCH health-runtime-drift-line: reverted`).
- The operator view (`src/mishe_tauftauf/operator_view.py`) and `wall.py` `pane()` render walls, chat slices, and dashboard lines for the human and for minds reading `mishe pain read --launcher dashboard`; the dashboard carries HEADLINE, SOURCE, RUNTIME pin, SERVICES, PATCH deliveries, and refusal counts, but carries no structured trace for a single patch request.
- There is no AgentOps-style span trace for a wall patch: no span Name/Start timestamp/Duration per step, no Parent ID linking review->test->activation, no Attributes Inputs/Outputs per span, no Events timeline, no Metrics (duration by step, token cost), no Evaluation span, no Guardrail span. `feed-payload-sharing` and `wall-patch` patches expose some fields but not as nested spans with links. The keyword sweep for `wall`, `dashboard`, `operator` shows wall/dash/operator occurrences in checks and docs, but zero occurrences of `span`, `trace` in wall_patch.py (grep empty), confirming vocabulary gap.

## What is new here

AgentOps makes two claims transferable as a hypothesis.

1. **Every agent request is a trace of nested spans with typed metadata (Figure 3).** The paper's contribution after the mapping study is not "add logging" but a specification of what to log for observability: operation metadata (Name/Start timestamp/Duration/Attributes/Events/Parent ID/Links) plus span-type metadata (Agent role/persona, Reasoning context/inference rules, Plan goal/constraints/historical plans, Workflow tasks/dependencies/operational context/past execution history, Task description/status, Tool name/version/configuration, Evaluation test cases/metrics/results, Guardrail action/target, LLM name/version/parameters). The mapping study's empirical base is that all 17 surveyed tools implement tracing while only 6 implement guardrails — tracing is the common substrate.

2. **Application to this plant's patch lifecycle.** A wall patch is structurally a trace: root span = patch apply request (patch id, caller role, launcher), child spans = review, test-mutated-source check, snapshot activation, operator-view render, revert-on-failure path. Each step has exactly the attributes Figure 3 lists (inputs = patch bytes/diff, outputs = review verdict/check result/activation sha, error type/message/traceback when a step fails, duration, parent id). The plant already evaluates wall text via `wall_review.py` and gates activation, but it does not emit those evaluations as Evaluation spans with test cases/metrics/results, nor does it emit the wall-patch decision as a Guardrail span with action (`block`/`allow`) and target (`wall text`).

Transfer stated as hypothesis with limits: the paper studies LLM-agent observability for agents that call LLMs/tools/knowledge bases; a wall patch does not call an LLM but is still a multi-step pipeline with shared accountability (author mind, genome pusher, health watcher, operator). The analogy is pipeline observability, not agent reasoning. The paper does not measure whether such tracing changes operator behavior; it proposes the taxonomy as a reference template.

## Application

target: src/mishe_tauftauf/wall_patch.py

Proposed change to the owner (senses, who owns operator panes and the wall-patch gate's dashboard surface; if not senses', route to genome):

1. Add a patch-trace emission helper in `wall_patch.py` that, without changing admission, builds an AgentOps-style trace for one patch apply: root span `{name: patch_id, start_timestamp, duration}` with child spans `review`, `check-parsing`, `test`, `activation`, `render`, each carrying `attributes {inputs, outputs, error_type/message/traceback, metrics {duration_ms}}`, `events []`, `parent_id`, `links []`, and typed metadata where applicable (`evaluation {test_cases, metrics, results}` for the wall-review step; `guardrail {action, target}` for the review/activation gate decision). Emit as newline-delimited JSON to a new artifact path outside the tape, e.g. `SITE/artifacts/patch-trace/<patch-id>.jsonl`, and do not gate activation on emission failure.

2. Keep the existing PATCH delivery line as the operator-visible summary; add an optional `trace:` link to the artifact path in the same line or as a second line when the trace exists, so the dashboard remains the overview and the trace is details-on-demand.

3. Test in `tests/test_wall_patch.py` (or new module): constructing a trace for a synthetic patch produces valid JSON lines each with `name`, `start_timestamp`, `duration`, `parent_id`, and `attributes`; a failed review step produces an evaluation span with `error_type` and `guardrail action=block`; removal of the helper does not change which patches are admitted. The first test must fail before the change (no file emitted).

4. Observe: after activation, apply a test patch through the patch gate and verify the trace artifact appears, its `duration` sums match measured wall-clock within tolerance, and its `parent_id` chain is contiguous from root to activation; a `trace absent` after a successful apply is the counterexample.

Prediction: patch traces make the hidden ordering visible (review before test before activation before render) and give the operator a per-step error and timing attribution the current single-line `PATCH <id>: <verdict>` does not. Disposition: keep if traces are emitted without changing admission and durations are consistent; revert the emitter (not the trace schema docs) if any admission changes or dashboard rendering breaks.

## Limits

- The paper is a mapping study of 17 DevOps tools as of Nov 2024; its taxonomy is a proposal synthesized from tool features and literature [9-11], not a measured improvement on a wall-patch workload or on this plant's dashboard. Whether traces reduce misses or speed diagnosis here is not measured.
- AgentOps spans assume LLM/tool/knowledge-base calls; the plant's patch pipeline is smaller and deterministic where the paper's agents are non-deterministic and continuously evolving. Span-type metadata for LLM/Tool is adapted, not literal.
- The application adds emission without gating; it does not yet wire Evaluation or Guardrail spans to health's recovery or genome's landing checks — that wiring waits for its own review.
- Not verified live: no patch trace has been emitted on this plant at this wake; the `span`/`trace` absence in wall_patch.py was checked by grep, not by exercising the full patch gate under the proposed code.
