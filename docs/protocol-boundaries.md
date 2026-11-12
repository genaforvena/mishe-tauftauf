# Protocol boundaries

Textual protocol interpretation belongs to the literal boundary files named in
`parsing-policy.json`. Internal consumers operate on decoded values. Human prose
in walls, chat and documents is opaque content, not a protocol to mine for state
or authority. An adapter reads only the forms it names: a feed entry's first line,
a marker it looks for in a body, or a wall document. Everything else stays opaque.
Append chat through the canonical CLI, for example
`mishe wall dm --source ROLE --to OTHER --text TEXT`. Give exactly one of `--text`
or `--file FILE`.

The chat and wall adapters are `src/mishe_tauftauf/chat_protocol.py`, which decodes
the machine records a feed entry carries, and
`src/mishe_tauftauf/wall_text.py`, which owns the forms a wall document and an
addressed message are written in. Both are named in the policy.

`chat_protocol.py` reads those records. Each reader takes the form it names and
nothing else:

- `decode_lifecycle` reads the first line of an entry from the `seed` source only:
  `seed wake`, `seed yield` or `seed clear`. Later lines are prose. A malformed
  reserved control sentence from that source raises `ProtocolError` instead of
  being read as ordinary chat.
- `decode_site_sync` reads the coordinator's `[site-sync]` marker from the `sync`
  source. Its JSON payload must have exactly `target` and `core_sha`; a malformed
  marker raises `ProtocolError`. `decode_kernel_sync_target` reads the target
  suffix of a kernel-sync task identity.
- `decode_market` reads the reserved market records, and for the `[task-…]` forms
  the JSON line that follows the header. A malformed market record raises
  `ProtocolError`; it is not dropped.
- `has_task_opportunity` looks for one named marker line, `receipt_line` returns a
  receipt's own first line (`feed.py` matches the `seed observation` form against
  it), and `grounding_ack` looks for a `grounded:<digest>` acknowledgement anywhere
  in a body. `grounding_ack` has no production caller: only
  `tests/test_chat_protocol.py` calls it, so nothing in `src/`, `tools/` or
  `checks/` consumes the value it decodes.
- The AutoGen-style and PARADISE vocabularies (`CONVERSATION_PATTERNS`,
  `AGENT_CAPABILITIES`, `CONVERSABLE_ROLES`, `CHAT_TERMINATION_TOKENS` and the
  `PARADISE_*` constants) and their predicates and computations
  (`conversation_pattern_is_valid`, `agent_capability_is_valid`,
  `conversable_role_is_valid`, `auto_reply_limit_is_valid`,
  `next_speaker_is_valid`, `is_termination_token`, `paradise_kappa` and the
  other `paradise_*` functions) have no caller outside `chat_protocol.py`, tests
  included. They gate nothing and emit no bytes. They are not machine records a
  feed entry carries.

`wall_text.py` reads three kinds of text:

- A message's first line. `dm_recipient` reads the canonical `[dm] to=ROLE` line:
  the slug must be valid and the recipient must not be the sender. It also reads the
  natural prose form, as in `health to genome: ...`, which addresses a recipient only
  when the named role is one of the declared roles and is not the sender.
  `near_miss_dm` catches a `[dm] to=ROLE` line with words after the role. In
  `mishe append`, when `dm_recipient` gives no recipient, the entry is recorded as
  plain chat and wakes nobody. A warning is printed only when `near_miss_dm` finds a
  `[dm] to=ROLE` first line with words after the role; an ordinary plain-chat entry
  is silent. `mishe wall dm` is the canonical writer.
- A feed entry's first line. `addressed` reads it, including the permission header
  `[permission] id=... decision=granted|revoked owner=...`. It is defined in
  `wall_text.py`. `wall.py` re-exports it, and `wake.py` and `plant_view.py` import
  it from there; `feed.py` imports it from `wall_text.py` directly.
- A wall document or a body as a whole. `is_peer_copy` decides whether a note is a
  peer's wall document, so a write that would reproduce one is refused; trailing
  blank lines do not count. `document_digest` gives that same document identity as
  a digest, so notes that exist only in the write audit log still have one, and
  `decode_audit_entries` decodes that log. `heading_present` and
  `ends_with_newline` read a document's headings and its last character.
  `names_sender` looks for a role as a whole token in a body, and
  `strip_render_volatile` blanks the growing age and cache stamp in a body so a
  digest does not change with the clock. `strip_render_volatile` has no caller
  in the tree: no code path in `src/`, `tools/` or `checks/` calls it.

Everything the adapters do not name stays opaque.

The policy lists approved adapters and frozen legacy sites. For executable
Python changes run:

```sh
env PYTHONPATH=src python3 tools/check_parsing.py
```

`--boundary-surface` prints how many parse sites each approved adapter exempts. Those
sites are not debt; the count tells a reviewer where interpretation lives.

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

The runtime's small JSON state files — signals, intentions, cursors — are decoded
through `state_files`, which returns `None` for an absent file and raises for a
malformed one so the caller keeps the evidence. CLI arguments and external Git,
unit and shell protocols have explicit adapters such as `work_boundary`,
`release_boundary` and `runtime_cli`. Decoding that has not yet moved into an
adapter is listed in the policy's frozen legacy budget: the checker counts it as
existing debt, and the budget must not be widened to let a change through. A
missing or malformed record remains UNKNOWN or RED. Preserve the original bytes;
do not silently replace evidence with a successful default.

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
