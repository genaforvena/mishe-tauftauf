# When Mishe owns the next step

An external agent CLI owns its model/tool loop. Mishe can observe its work and
wake it again, but cannot shape every intervening model request. The experimental
internal loop moves that boundary: Mishe's caller can choose the tools offered
on the next turn and supply a new observation before the model chooses an action.
The model still supplies proposals; the effect executor checks current authority.

The opportunity is **better experiments inside useful work**, not another manager
that approves every thought. Start with Docs: a reader question has a small,
inspectable corpus, and an editorial result can be reviewed without guessing
whether a healthy process did anything useful.

## Implemented building blocks

`inference_loop.drive_native` accepts an optional `before_turn(state)` callback.
The state is detached from the running context and includes the zero-based turn,
calls used, remaining turn/call budgets and current context. Return `None` to
leave the current offer unchanged, or a decision such as:

```python
def before_turn(state):
    return {
        'tools': ['search_docs', 'read_file'],
        'notice': 'The build result is still UNKNOWN; this is an observation, not permission.',
    }
```

Every offered name must refer to an originally granted tool schema. Later turns
may re-offer original tools; the callback cannot invent tools, alter schemas,
rewrite history or replace the system prompt. A notice is ordinary user-message
text, not elevated authority. The exact decision is journaled before provider
input. Failure to persist stops before the request. Reconstructed history retains
the offer and notice; an interrupted policy boundary does not authorize a retry.
This hook does not set a model's provider-level `tool_choice`, change the model,
execute effects or turn an advisor into an authority oracle.

`docs_tools.search_docs(root, query, limit=20)` is a literal, case-insensitive
search of `README.md` and immediate `docs/*.md`, using the same reader corpus as
the command-coverage audit. It returns exact repository paths, line numbers and
line text, plus an explicit truncation flag. It does not search private site
notes, follow symlink pages, build an index or interpret prose as commands.
Read failures raise; a completed no-hit result means only that the literal query
was absent from the eligible corpus. Read the surrounding passage before editing.

These are library mechanisms. The ordinary plant still uses its installed
launchers; availability in source does not migrate every resident or establish
live acceptance. Session budgets, effect journals, current authority and failure
reconciliation still apply. See [the internal-loop boundary](how-it-works.md).

## Experiments worth doing

| Experiment | Concrete intervention | Evidence for keep/revise/revert |
| --- | --- | --- |
| **Reader-first Docs** | Start from a real reader question; search the book, read the passage, edit only the gap. | Reviewer can answer the question from the resulting page; path/line citations support the claim. A shorter trace alone is not success. |
| **Progressive tool offers** | Offer observation tools before the first dashboard read; restore normal owned coding tools afterwards; offer none after settlement. | Journal shows the actual schemas at each turn. An unoffered call dispatches nothing. Check that legitimate work is not stranded. |
| **Fresh evidence between steps** | Supply changed build/check state or an addressed request at the next safe turn boundary, labelled with provenance and freshness. | The next decision acknowledges the changed evidence without erasing the earlier failure. Missing/stale input remains UNKNOWN. |
| **Selective System 1 critique** | After deterministic checks, ask a read-only critic one residual question: “Does this passage answer the reader, and what is unsupported?” | Keep only critiques that identify a grounded defect or useful competing interpretation. Record uncertainty; do not turn agreement into test evidence. |
| **Docs integration at the point of action** | Retrieve the maintained passage for the current task alongside command results, rather than loading the whole manual at wake time. | A reviewer can trace the action to the right passage and observed state. Retrieved text never authorizes an action by itself. |
| **Context handoff by evidence** | At a completed-result boundary, retain decisions, artifacts and unresolved effects instead of a giant narrative recap. | A successor finds the open question and exact evidence; no repeated side effect or fabricated recovery. Context shrinking must not discard uncertainty. |
| **Measured model/reasoning selection** | Compare low/high reasoning or different authenticated models on the same retained, read-only editorial question. | Compare grounded defects found, reader usefulness, latency and observed usage. Do not equate tokens saved or self-scored quality with better work. |

The first two are the initial Docs integration experiment. The remaining rows are
proposals, not enabled services. In particular there is no automatic per-turn
critic, model router, background evaluator or new permission gate.

## System 0 first; System 1 where it earns its place

Use deterministic code for file lookup, schema validation, budgets, command exit
status, evidence identity, scope and settlement. Use System 1 for the residual
judgment: what a reader will misunderstand, whether two statements conflict, or
which experiment is worth trying. A critic should receive a bounded question and
the evidence needed to disagree, not the actor's desired conclusion alone.

Do not ask a model to choose the next shell command when a known deterministic
check already answers the question. Do not insert a critic on every read. Do not
let model agreement grant authority, suppress a warning, or convert UNKNOWN to
GREEN. A live experiment should name its prediction, observed result and
keep/revise/revert decision, including adverse observations.

For Docs, useful outcomes include deleting a stale paragraph, making a reader
journey executable, or an evidenced decision to keep the current text. Repeating
an unchanged audit, acknowledging a message, or successfully settling a wake is
not by itself editorial progress.
