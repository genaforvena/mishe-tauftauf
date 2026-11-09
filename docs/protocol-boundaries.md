# Protocol boundaries

Textual protocol interpretation belongs to the literal boundary files named in
`parsing-policy.json`. Internal consumers operate on decoded values. Human prose
in walls, chat and documents is opaque content, not a protocol to mine for state
or authority. Append chat through the canonical CLI, for example
`mishe wall dm --source ROLE --to OTHER --text TEXT` (or `--file FILE`).

The policy lists approved adapters and frozen legacy sites. For executable
Python changes run:

```sh
env PYTHONPATH=src python3 tools/check_parsing.py
```

The checker exits 0 when every parse site is inside a named adapter or inside
the frozen legacy budget. It exits 1 when it finds a parse site outside both,
and prints each one as `file:line: parsing outside boundary: <call>`. An exit 1
is a real finding for the change that introduced the site: move the parsing
into an adapter the policy names, or decode the value at an existing boundary.
Do not add a site to the legacy budget to make it pass.

The checker is a static detector with a defined scope, not proof that every
semantic interpretation is correct. Read a new adapter's input format, malformed
cases and consumers explicitly. Remove budgets as their consumers migrate.

JSON site records are decoded through `state_files`. CLI arguments and external
Git, unit and shell protocols have explicit adapters such as `work_boundary`,
`release_boundary` and `runtime_cli`. A missing or malformed record remains
UNKNOWN or RED. Preserve the original bytes; do not silently replace evidence
with a successful default.

Delivery requests use structured values for the exact commit, originating wake,
author role, the file list, the runtime and test arguments, the review record and
the reviewed delivery plan. A strong request also needs the `activate`, `observe` and
`revert_observe` steps of that plan. The request's digest is recomputed from
those fields, and its source and command artifacts are revalidated at execution
(`src/mishe_tauftauf/delivery_requests.py`). Human wall statements such as
"permission required" are not parsed into a grant or a delivery decision.

Keep reusable explanations tracked in Git. Keep runtime snapshots, experiments,
chat and local notes under the resolved site home. See [Working as a durable
mind](wall-coordination.md) for delivery and recovery behavior.
