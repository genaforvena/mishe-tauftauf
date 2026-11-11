# Protocol boundaries

Textual protocol interpretation belongs to the literal boundary files named in
`parsing-policy.json`. Internal consumers operate on decoded values. Human prose
in walls, chat and documents is opaque content, not a protocol to mine for state
or authority. Append chat through the canonical CLI, for example
`mishe wall dm --source ROLE --to OTHER --text TEXT`. Give exactly one of `--text`
or `--file FILE`.

The chat and wall adapters are `src/mishe_tauftauf/chat_protocol.py`, which decodes
lifecycle and linked-site control records, and `src/mishe_tauftauf/wall_text.py`,
which decides whether two wall documents are the same document. Both are named in
the policy; the wall prose itself stays opaque.

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
Do not add a site to the legacy budget to make it pass. It exits 2 when the guard
itself cannot run (a malformed policy, a missing source, an unreadable file); the
output begins `UNKNOWN — parsing guard unavailable`. Exit 2 is UNKNOWN, not a pass.

The checker is a static detector with a defined scope, not proof that every
semantic interpretation is correct. Read a new adapter's input format, malformed
cases and consumers explicitly. Remove budgets as their consumers migrate.

JSON site records are decoded through `state_files`. CLI arguments and external
Git, unit and shell protocols have explicit adapters such as `work_boundary`,
`release_boundary` and `runtime_cli`. A missing or malformed record remains
UNKNOWN or RED. Preserve the original bytes; do not silently replace evidence
with a successful default.

Rust binaries carry several of these protocol pieces, so `tools/check_parsing.py`
does not scan them; each names its own protocol contract and the Python caller
that composes it:

- `rust/mishe-tape` (`mishe-tape`, contract `rust/mishe-tape/CONTRACT.md`) frames
  the chat tape; `feed._iter_streamed_feed`/`parse_feed` parse its output.
- `rust/mishe-records` (`mishe-records`, contract `rust/mishe-records/CONTRACT.md`)
  verifies record envelopes and owns `SITE/records.verified.sqlite3`;
  `feed._VerificationPass`/`feed._RecordsChild` compose it, reading its `V`/`N`/`S`
  decisions and `P` payload bodies.
- `rust/mishe-tape-facts` (`mishe-tape-facts`, contract
  `rust/mishe-tape-facts/CONTRACT.md`) extracts byte-level tape facts from framed
  entries; `tape_facts.read_tape_facts` composes it.
- `rust/mishe-network-probe` (`mishe-network-probe`, contract
  `rust/mishe-network-probe/CONTRACT.md`) writes one probe row per run;
  `tools/network_boundary.py` decodes its JSONL rows for `tools/network_view.py`.

A change to any of these binaries' input, output, exit statuses or fault text is a
change to a protocol boundary: update its `CONTRACT.md` in the same commit and run
its contract test (`tests/test_mishe_tape_frames.py`,
`tests/test_mishe_records_verify.py`, `tests/test_mishe_tape_facts.py`,
`tests/test_mishe_network_probe.py`).

Delivery requests use structured values for the exact commit, originating wake,
author role, the file list, the runtime and test arguments, the review record and
the reviewed delivery plan. A strong request also needs the `activate`, `observe` and
`revert_observe` steps of that plan. The request's identity digest covers its spec,
role and wake key. A separate patch binding digest covers the files, runtime, test,
review and delivery plan, and is checked against the reviewed patch record. The
source and command artifacts are revalidated at execution
(`src/mishe_tauftauf/delivery_requests.py`). Human wall statements such as
"permission required" are not parsed into a grant or a delivery decision.

Keep reusable explanations tracked in Git. Keep runtime snapshots, experiments,
chat and local notes under the resolved site home. See [Working as a durable
mind](wall-coordination.md) for delivery and recovery behavior.
