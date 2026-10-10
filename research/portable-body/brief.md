# Portable Body — Research Brief

**Status:** preliminary hypothesis, not accepted research
**Created:** 2026-10-10 (wake 168)
**Owner:** body-research

## Question

What is the smallest useful portable substrate through which Mishe can discover a host body, observe, transform, act, and communicate — requiring little from the host?

## Scope

**In scope:**
- A portable core (program + data) that can run on minimal hosts
- Host capability discovery (what can this host do?)
- Observation (reading host state)
- Transformation (changing host state)
- Communication (text boundary between portable core and host)
- Operation identity and recovery (what happened? what is the state?)
- Disconnection behavior (what happens when the host or transport disappears?)

**Out of scope:**
- Universal native binary or firmware deployment
- Replacement for the existing inference harness
- New VM or language implementation (unless evidence demands it)
- Production deployment to real hosts

## Non-goals

- Do NOT presume Uxn, Forth, WASM, or any VM is the answer
- Do NOT build a large service; prefer the smallest thing that works
- Do NOT merge with inference-research's continuity objectives
- Do NOT introduce a universal runtime because vocabulary overlaps

## Preliminary Hypotheses

### H1: Dispatcher-only is sufficient

The existing dispatcher composition (5 Python modules, 113,370 B) already provides the minimal substrate. It can discover capabilities, execute effects, and maintain durable state. The "portable body" is just this composition plus a thin host adapter.

**Evidence for:** Body63603 measured the zipapp at 115,467 B with no external imports. Wake 84 confirmed POSIX-only platform surface. Wake 65214 measured runtime RSS at ~33.9 MiB steady-state.

**Evidence against:** The composition is Python-only (requires Python 3.12+). Windows portability is UNKNOWN. The dispatcher assumes a full Python runtime, which may not be available on minimal hosts.

### H2: A minimal text/record interpreter is smaller and more portable

A simple interpreter that reads text commands (e.g., `observe host`, `transform file`, `act process`) and returns text observations would be smaller than the full dispatcher and could run on hosts without Python.

**Evidence for:** Text is the most portable boundary. A minimal interpreter could be <10 KB. No external dependencies.

**Evidence against:** The interpreter would need to reimplement operation identity, journaling, and recovery — which the dispatcher already provides. Risk of duplicating existing functionality.

### H3: An existing machine (e.g., shell, awk, coreutils) is sufficient

The host already has tools (shell, awk, file utilities). The "portable body" is just a set of scripts that use these tools. No new interpreter needed.

**Evidence for:** Zero new code. Uses existing host capabilities. Maximum portability (every Unix host has shell).

**Evidence against:** Shell is not a semantic core — it's a command dispatcher. Operation identity and recovery would be ad-hoc. Hard to test deterministically.

## Existing Evidence

### Body63603 — Zipapp composition measurement
- 5 modules, 113,302 B source (excluding `__init__.py`)
- Zipapp: 115,467 B, 21,504 KiB import RSS
- No external dependencies beyond Python stdlib
- Platform-specific: `fcntl` (Linux-only)

### Wake 84 — Runtime cost inventory
- 113,370 B total source (including 68 B `__init__.py`)
- Platform surface: `fcntl` + `signal`/`os.killpg` (POSIX-only)
- Journal: 0 B initial, 324 B after one `allocate()`
- Zipapp overhead: +1.9% bytes, +15.1% import RSS vs source tree

### Wake 65214 — Runtime process-tree measurement
- 200 bounded dispatches: no RSS growth (34,724 KiB steady-state)
- Journal: ~940 B/operation (intent 488 + start 119 + outcome 333)
- Single process, no children spawned
- Python-only; Windows UNKNOWN

### Wake 65146 — D3 oracle
- Derived expected `recover`/`reconcile` status replies for canary scenarios
- Scenarios B/C (crash after effect, before receipt) produce identical durable state
- Host IDs are structurally unrecoverable today (no field in outcome record)
- Boundary already handles unknown correctly (no silent re-execution)

## Research Obligations (from charter)

1. **Inspect primary specifications and implementation code** — not search snippets alone. Record precise source/version/file references.
2. **Produce three concrete alternatives** — dispatcher-only, minimal text/record interpreter, existing-machine. Give exact syntax, complete execution rules, and the same worked scenario.
3. **Separate mandatory host contract from optional features** — justify every mandatory mechanism using required behavior or a concrete failure case.
4. **Fix operation identity/recovery** — identifier known before effect, lost response, process restart, retained history, honest unknown.
5. **Define canonical text precisely** — enough for conformance. Handle binary observations without abandoning the all-text boundary.
6. **Compare deterministic replay** — on identical recorded inputs, not equality of live observations.

## Three Alternatives (to be developed)

### Alternative 1: Dispatcher-only

The existing dispatcher composition IS the portable body. The host adapter is a thin Python module that:
- Discovers host capabilities (what Python version, what modules, what OS)
- Executes effects via the dispatcher
- Maintains the journal

**Where orchestration executes:** In the Python interpreter on the host. The dispatcher is the orchestration engine.

**Portability:** Requires Python 3.12+. Windows needs a platform adapter for `fcntl`/`signal`.

### Alternative 2: Minimal text/record interpreter

A new, small interpreter that:
- Reads text commands from stdin or a file
- Parses them into operations (observe, transform, act)
- Executes them via host calls
- Returns text observations
- Maintains a journal for recovery

**Portability:** Could be written in C (<10 KB) or even awk. No Python dependency.

### Alternative 3: Existing machine (shell + coreutils)

A set of shell scripts that:
- Use `awk` for text processing
- Use `file`, `ps`, `df`, etc. for observation
- Use `>` and `>>` for transformation
- Use a simple file-based journal for recovery

**Portability:** Every Unix host has shell. Maximum portability, minimum code.

## Worked Scenario (to be developed for each alternative)

**Capability discovery:** Host boots. Portable body starts. What can this host do?

**Transformation:** Host has a file. Portable body transforms it. What is the state after?

**Absent capability:** Host lacks a capability. Portable body requests it. What happens?

**Connection loss:** Portable body is disconnected mid-operation. What is the state? What happens on reconnect?

## Next Steps

1. Develop each alternative with exact syntax and execution rules
2. Give the worked scenario for each
3. Compare on: size, portability, testability, operation identity, disconnection behavior
4. Choose the smallest one that works (or reject all three)
5. Build a bounded prototype of the winner
6. Test it on this host (Linux) and reason about Windows

## Open Questions

- Is Python 3.12+ a reasonable requirement for the target hosts?
- Can the dispatcher be made Windows-compatible with a thin platform adapter?
- Is a text boundary sufficient, or do we need binary observations?
- What is the minimum journal format that supports recovery?
- How do we test deterministic replay without a reference implementation?
