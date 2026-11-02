---
field: inference-effects-and-native-loop
angle: recent
date: 2026-11-02
status: applied
owner: inference-research
---

## What we read

- Anthropic, "Building effective agents", research/engineering (published 2024-12-19, modified 2026-10-09): <https://www.anthropic.com/research/building-effective-agents> (served as <https://www.anthropic.com/engineering/building-effective-agents>). Fetched 2026-11-02 to /tmp/anthropic-building-effective-agents.html 220420 bytes sha256 776a08c1a92780ec0c1d4ae96586dfa12e4105be7d6818bdf79de0eefe40e983, text extracted via tag strip to /tmp/anthropic-agents.txt 155545 bytes. Read the primary page itself, not a summary. Passages quoted verbatim: "Workflows are systems where LLMs and tools are orchestrated through predefined code paths. Agents, on the other hand, are systems where LLMs dynamically direct their own processes and tool usage, maintaining control over how they accomplish tasks." "During execution, it's crucial for the agents to gain \"ground truth\" from the environment at each step (such as tool call results or code execution) to assess its progress." "We suggest that developers start by using LLM APIs directly: many patterns can be implemented in a few lines of code." "Agents are typically just LLMs using tools based on environmental feedback in a loop."
- The plant's own text, read from the working tree: `src/mishe_tauftauf/inference_loop.py` `drive_native` docstring (lines 83-101: capability selection is model-owned, typed validation and execution remain inside dispatch, no automatic resubmission after unknown effects, before_turn re-offer contract) and `src/mishe_tauftauf/inference_effects.py` module docstring (lines 1-20: allocate stable operation ID before dispatch, persist intent before acting, crash between intent and outcome reads back as unknown never silently retried) plus `src/mishe_tauftauf/native_gate.py` `detect` docstring (lines 207-215: RED false negative as delegation branch with live mind read not-ready).

## What this plant already does

`drive_native` is already an agent in the Anthropic sense, not a workflow. Each turn the model selects among the offered tools; `dispatch` owns durable operation identity and re-checks authority after persistence; `_validate_tool_offer` and `_apply_turn_policy` enforce that a turn may only re-offer exact original schemas (name equality, full dict equality against `original_tools`), and a `notice` appends as verbatim user observation, never authority. Every state change is journaled before the next provider input: `checkpoint`, `turn_policy`, `model_input` before `session.turn`, `model_output` after, `proposal`/`phase`/`authorized` per call, `tool_result` with `isError: outcome.status != completed`, and per-turn cost evidence `wire_usages`/`fetch_ledgers` (carried across `resume_from`). Any `status != completed` stops the loop (`stop('unknown')`); `recover_native` requires identical `{turns,calls,context,obligation}` and refuses a `stopped` caller. The native pane boundary is explicit (`native_pane.read_prompt` with bracketing, limit, `on_ready` after framing) and the delegation readiness probe (`native_gate.detect`) classifies VERDICT correctly rather than synthesizing readiness. This matches "ground truth at each step" and "just LLMs using tools based on environmental feedback in a loop."

## What is new here

The Anthropic paper makes the agent/workflow naming explicit and ties four actionable corollaries to it, each dated 2024-12-19 and current as of 2026-10-09:

1. The distinction itself matters for docs: workflows have predefined code paths; agents have the model directing tool use. The plant's loop has no predefined path beyond budgets and `before_turn`; the transfer is naming that correctly so a reader does not misread `drive_native` as a workflow.
2. "Ground truth at each step" is a journaling obligation, not a slogan: tool results and code execution must be observable to the next model turn. The plant already emits `model_input`/`model_output`/`tool_result` but buries the Anthropic lineage in comments; an explicit citation closes the loop for a docs reader asking why those emissions exist.
3. "Start by using LLM APIs directly" and "understand the underlying code; incorrect assumptions about what's under the hood are a common source of customer error" — the plant already does this (`inference_worker` wraps the provider, no framework), but a future contributor could propose abstracting `session.turn` behind a framework without noting the journaling contract the abstraction must preserve.
4. "Agents can then pause for human feedback at checkpoints or when encountering blockers" plus "It is therefore crucial to design toolsets and their documentation clearly." The plant's checkpoints are `before_turn` (per-turn grant narrowing with journaled decision), `phase` and `authorized` (per-call synchronous observer), and `cancelled` (multiple boundaries). Tool clarity is the docstring/schema the model sees; `_tool_names` only checks names, so description drift has no check today — noted as limit, not imported.

None of this contradicts the MCP untrusted-annotation discard (2026-10-31, this cell) which refused retry on `unknown`; Anthropic's "ground truth" strengthens never-retry-unknown (an `unknown` tool result is ground truth that the effect is unresolved).

## Application

target: src/mishe_tauftauf/inference_loop.py

Additive, docs-only clarification to the `drive_native` module/docstring header, keeping all behaviour byte-identical. No new parsing site, no new parameter, no new journal kind:

- Name the Anthropic agent/workflow distinction verbatim and state that `drive_native` implements the agent form (model directs tool usage in a loop, feedback via `tool_result`/`wire_usage`), citing Anthropic 2024-12-19 (modified 2026-10-09) https://www.anthropic.com/research/building-effective-agents.
- Cite the "ground truth at each step" quote as the rationale for the existing emission order (`model_input` before provider, `model_output`/`tool_result`/`wire_usage`/`fetch_ledger` after, `turn_policy` before input) and for `stop('unknown')` on any non-completed dispatch (an `unknown` observation is ground truth that reconciliation, not retry, owns the effect per `inference_effects.py`).
- Note the checkpoint lineage: `before_turn` is the local form of Anthropic's checkpoint/pause (journaled decision before provider), `phase`/`authorized` are per-call checkpoints, `cancelled` is the cooperative withdrawal boundary — all already present, named here so a reader asking "where are the checkpoints?" finds them.
- Preserve the existing "start by using LLM APIs directly" stance: note that `session.turn` is a direct provider call wrapped by `inference_worker`, not a framework, and that any wrapper must preserve the journal-before-next-input order.

Limits carried: description clarity (Anthropic Appendix 2) is not checked here; `_tool_names` validates names, not prose, so a weak tool description remains a docs bug outside this change. Multi-window/multi-pattern orchestration (orchestrator-workers, evaluator-optimizer, routing) stays caller-owned via `before_turn`; the loop does not choose thresholds. Source fidelity: HTML retained at /tmp/anthropic-building-effective-agents.html sha 776a08c1 and extracted text at /tmp/anthropic-agents.txt; quotes above are verbatim from that extraction and not from a summary.

