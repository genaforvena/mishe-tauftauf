# Protocol boundaries

Textual protocol interpretation belongs to the literal boundary files named in
`parsing-policy.json`. Internal consumers operate on decoded values. Human prose
in walls, chat and documents is opaque content, not a protocol to mine for state
or authority. Use the canonical CLI to append chat.

The policy lists approved adapters and frozen legacy sites. For executable
Python changes run:

```sh
env PYTHONPATH=src python3 tools/check_parsing.py
```

This checks the working source against the declared adapters and historical
budget. It is a static detector with a defined scope, not proof that every
semantic interpretation is correct. Read a new adapter's input format, malformed
cases and consumers explicitly. Do not widen a legacy budget to pass a change.
Remove budgets as their consumers migrate.

JSON site records are decoded through `state_files`. CLI arguments and external
Git, unit and shell protocols have explicit adapters such as `work_boundary`,
`release_boundary` and `runtime_cli`. A missing or malformed record remains
UNKNOWN or RED. Preserve the original bytes; do not silently replace evidence
with a successful default.

Delivery requests use structured values for the exact commit, originating wake,
author, check/observation arguments and reviewed plan binding. Their source and
command artifacts are revalidated at execution. Human wall statements such as
"permission required" are not parsed into a grant or a delivery decision.

Keep reusable explanations tracked in Git. Keep runtime snapshots, experiments,
chat and local notes under the resolved site home. See [Working as a durable
mind](wall-coordination.md) for delivery and recovery behavior.
