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
  `seed wake`, `seed yield` or `seed clear`. Later lines are prose, with one
  exception: a `seed yield` body may carry a line that begins `Turn settled (STATE);`.
  The first such line sets the yield's `result`. `discovery.py` reads it as the
  wake's settlement in the `sense.seed.settle-result-mix` reading; `seed_board.py`
  reads it into `Work.result`, but that path has no production caller. Other
  later lines stay prose. A malformed reserved control sentence from that source
  raises `ProtocolError` instead of being read as ordinary chat.
- `decode_site_sync` reads the coordinator's `[site-sync]` marker from the `sync`
  source. Its JSON payload must have exactly `target` and `core_sha`: `target` is a
  non-empty string of at most 255 characters with no CR, LF or NUL, and `core_sha` is
  40 lowercase hex digits. A malformed marker or payload raises `ProtocolError`. `decode_kernel_sync_target` reads the target
  suffix of a kernel-sync task identity.
- `decode_market` reads the reserved market records. The legacy single-line forms
  `announce:ID:requires=QUAL`, `bid:ID:qual=QUAL:available=N` and `award:ID:winner=ROLE:bids=N`
  are matched whole against the first line. The `[task-announce]`, `[task-bid]` and
  `[task-award]` forms read the JSON line that follows the header and must carry the
  same identity. A malformed market record raises `ProtocolError`; it is not dropped.
- `has_task_opportunity` looks for one named marker line, `receipt_line` returns a
  receipt's own first line (`feed.py` matches the `seed observation` form against
  it), and `grounding_ack` looks for a `grounded:<digest>` acknowledgement anywhere
  in a body, where the digest is 7 to 40 lowercase hex characters on a word
  boundary, so a SHA prefix matches and free prose does not.
  `grounding_ack` has no production caller: only
  `tests/test_chat_protocol.py` calls it, so nothing in `src/`, `tools/` or
  `checks/` consumes the value it decodes.
- The AutoGen-style and PARADISE vocabularies (`CONVERSATION_PATTERNS`,
  `AGENT_CAPABILITIES`, `CONVERSABLE_ROLES`, `CHAT_TERMINATION_TOKENS`,
  `AUTO_REPLY_LIMIT_DEFAULT` and the `PARADISE_*` constants) and their predicates
  and computations (`is_conversation_annotation_required`,
  `conversation_pattern_is_valid`, `agent_capability_is_valid`,
  `conversable_role_is_valid`, `auto_reply_limit_is_valid`,
  `next_speaker_is_valid`, `is_termination_token`, `paradise_kappa` and the
  other `paradise_*` functions) have no caller outside `chat_protocol.py`, tests
  included. `CHAT_VOCABULARY_POLICY`, the constant that says the vocabulary is
  advisory, has no caller either. They gate nothing and emit no bytes. They are
  not machine records a feed entry carries.

`wall_text.py` reads three kinds of text:

- A message's first line. `dm_recipient` reads the canonical `[dm] to=ROLE` line:
  the slug must be valid and the recipient must not be the sender. It also reads the
  natural prose form, as in `health to genome: ...`, which addresses a recipient only
  when the named role is one of the roles the caller passes and is not the sender.
  `mishe append` passes the declared roles; `addressed` passes only the role whose
  inbox is being tested.
  `near_miss_dm` catches a `[dm] to=ROLE` line with words after the role. In
  `mishe append`, when `dm_recipient` gives no recipient, the entry is recorded as
  plain chat and wakes nobody. A warning is printed only when `near_miss_dm` finds a
  `[dm] to=ROLE` first line with words after the role; an ordinary plain-chat entry
  is silent. `mishe wall dm` is the canonical writer.
  `first_line` returns that line alone; `record_tag` reads the bracketed tag it
  opens with (`[ci] ...` → `ci`), the one classifier form besides the DM address.
  `message_subject` reads the first markdown heading after it; and `sender_role`
  strips a `mind/` prefix from a feed source. The System 0 `dm_triage` (called by
  `wake.py`) reads only `record_tag` and `failure_notice` to classify machine-addressed
  mail; its former derived state and the decision model that consumed it were retired
  on 2026-11-18, so no private body is sent anywhere. `first_line`, `message_subject`
  and `sender_role` now have no caller outside `wall_text.py` and its tests.
- A feed entry's first line. `addressed` reads it. The permission header
  `[permission] id=... decision=granted|revoked owner=...` counts only when the
  entry's source is `operator/permissions`. `addressed` is defined in
  `wall_text.py`; `wall.py` re-exports it, `wake.py` and `plant_view.py` import it
  from `wall.py` (`from .wall import addressed`), and `feed.py` imports it from
  `wall_text.py` directly.
- A wall document or a body as a whole. `is_peer_copy` decides whether a note is a
  peer's wall document, so a write that would reproduce one is refused; trailing
  newline bytes at the end do not count (a trailing space or carriage return does).
  `document_digest` gives that same document identity as
  a digest, so notes that exist only in the write audit log still have one, and
  `decode_audit_entries` decodes that log. `heading_present` and
  `ends_with_newline` read a document's headings and its last character.
  `names_sender` looks for a role as a whole token in a body, and
  `strip_render_volatile` blanks the growing age and cache stamp in a body so a
  digest does not change with the clock. `strip_render_volatile` has no caller
  in the tree: no code path in `src/`, `tools/` or `checks/` calls it.

`checked_reviewer_model` is the one function here that reads configuration rather
than wall text. It accepts a reviewer model id only when the id starts with
`claude-haiku-`, and `wall_review.py` calls it on the configured id.
Everything the adapters do not name stays opaque.

`page_paths_text.py` reads one form of the reader book: the repository paths a
Markdown page spells. `SUFFIX` is `.md` and `REFERENCE` matches a token that
starts with a word character and ends in a file suffix (`py`, `md`, `json`, `sh`,
`rs`, `toml`), in first-appearance order; a token that begins `./` cannot match,
so none needs trimming. Whether a token names an existing file, and whether it
joins a delivery review, is the caller's decision, not the adapter's.
`release_promotion._described_paths` composes it: a review supplies the changed
paths' bytes and bounded caller/import excerpts from the reviewed tree, but a
changed page that describes a rule implemented elsewhere still arrives without
that implementation and the reviewer cannot trace it. The composer adds at most
`DESCRIBED_PATH_MAX` (12) such files, each at most 256 000 bytes and 320 000 bytes
in total, as unchanged review entries. The reviewer is an external model, so an
added file must stay inside the release tree: a token that resolves outside it (a
spelled `..` segment, which the adapter permits) or that does not resolve to a
regular file in it is dropped, never read or handed over, never guessed. A file
whose bytes are not UTF-8 is
dropped too — `patch_text.is_utf8` gates it — because `wall_patch.review` reads
every entry as UTF-8 and one undecodable file would otherwise lose the whole
review as a `review_error`. Measured cause: the docs releases
19fb7646 and c87cd884 reviewed "unknown" because `docs/network-view.md` describes
`checks/network-care.py`'s RED/UNKNOWN arms, which the reviewer never received.
`tests/test_release_promotion.py::test_ordinary_review_supplies_a_file_the_changed_document_describes`
pins the supply behaviour,
`::test_described_paths_cannot_escape_the_release_tree` pins the containment rule
and `::test_ordinary_review_drops_a_described_file_that_is_not_utf8` pins the
UTF-8 rule.
Nothing else a page contains is read.

The engine-account text adapters decode the four external provider text formats the
router consumes:

- `omp_usage_text.py` — one OMP wake's per-message usage records from
  `wake-<key>.prompt.out.jsonl`; decides whether a `message_end` record is countable
  and with what token values.
- `router_text.py` — the engine router's own stream and the wake-concurrency JSON
  files: retry-after-ms extraction, JSON-lines event streams, routing ledger events,
  and cooling-key splitting.
- `codex_text.py` — the codex CLI's session rollouts under `~/.codex/sessions`
  (rate_limits readings) and the JSONL event stream of a `codex exec` run.
- `claude_quota_text.py` — Claude Code's native zero-inference `/usage` report
  (five-hour and seven-day windows, percentage used and reset times).

`claude_quota_text.py` is the only one currently named in a shipped reader page
(`docs/claude-quota.md`). The other three are not yet named in any shipped page.

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

Git's own output is read by two adapters. `reflog_boundary` decodes the reset-origin
evidence — `git reflog --date=unix --format=%H|%gd|%gs`, `git fsck --unreachable
--no-reflogs` and `git log --no-walk=unsorted --format=%H|%P|%ct|%s` — and returns
None for a malformed line rather than guessing. `git_base_text` owns the tree and
commit text: `committed_snapshot` reads a file's bytes and mode at a base ref from
`git ls-tree` and `git show`, and `commit_intent_text` drops the attribution trailer
block from a `git log` body so a release reviewer reads the author's stated intent
rather than provenance. `wall_patch.base_snapshot` and `committed_or_none` compose
the first; `release_promotion` composes the second. A ref that cannot be read, a
non-blob path or an unsafe ref name raises rather than returning empty bytes.

Rust binaries carry several of these protocol pieces, so `tools/check_parsing.py`
does not scan them; each names its own protocol contract and the Python caller
that composes it:

- `rust/mishe-tape` (`mishe-tape`, contract `rust/mishe-tape/CONTRACT.md`) frames
  the chat tape; `feed._iter_streamed_feed`/`parse_feed` parse its output.
- `rust/mishe-records` (`mishe-records`, contract `rust/mishe-records/CONTRACT.md`)
  verifies record envelopes and owns `SITE/records.verified.sqlite3`;
  `feed._VerificationPass`/`feed._RecordsChild` compose it, reading its `V`/`N`/`S`
  decisions and `P` payload bodies. Since contract 0.4 a `V` or `N` decision line
  also names the verified record's kind, which `feed` attaches to the entry's framer
  facts (`Facts.kind`, via `_attach_kinds`) so a classifying caller reads the kind
  instead of re-peeking the cited record; `S` carries no kind, and an entry whose
  kind stays `None` must resolve it itself.
- `rust/mishe-tape-facts` (`mishe-tape-facts`, contract
  `rust/mishe-tape-facts/CONTRACT.md`) extracts byte-level tape facts from framed
  entries; `tape_facts.read_tape_facts` composes it. No production module imports
  `tape_facts`; only `tests/test_tape_facts_reader.py` calls it, so the binary's
  output is decoded by tests and not yet by a live consumer.
- `rust/mishe-network-probe` (`mishe-network-probe`, contract
  `rust/mishe-network-probe/CONTRACT.md`) writes one probe row per run;
  `tools/network_boundary.py` decodes its JSONL rows for `tools/network_view.py`.
- `rust/mishe-systemd-show` (`mishe-systemd-show`, contract
  `rust/mishe-systemd-show/CONTRACT.md`) decodes `systemctl show` text;
  `unit_psi_text.parse_systemd_show` composes it and fails closed with
  `SystemdShowUnknown` when the binary is missing, mismatched or failing. There is
  no Python fallback: the retired Python decoder is deleted, and its recorded
  answers are GOLDEN in `tests/test_rust_systemd_show.py`.
- `rust/mishe-signal-samples` (`mishe-signal-sample`, contract
  `rust/mishe-signal-samples/CONTRACT.md`) owns the append-only signal sample
  store: `SITE/samples/<name>/` holds TSV segments of five fields — timestamp in
  milliseconds, name, state, value, source — and the binary appends a batch or
  reads it back in one pass. `signal_samples.append_samples` and
  `signal_samples.read_samples` compose it. The `memory-samples` producer runs
  every three minutes and appends one row each for memory PSI avg10 and anonymous
  memory, plus charged memory (Cached and Slab) when it can be read, into
  `SITE/samples/memory/`; a reading that cannot be taken writes no row for it. A gap
  is a gap: no record means no sample, never a zero and never a carried-forward
  value. The `footprint` duty, also every three minutes, is the production reader:
  it reads the last 15 minutes of that store and raises `SignalSampleUnknown` when
  any of the three series has no recent `ok` sample, so the signal ages to UNKNOWN
  rather than showing a carried value. There is no Python fallback: a missing
  binary raises `SignalSampleUnknown`, which the caller must report as UNKNOWN.
- `rust/mishe-signal-freshness` (`mishe-signal-freshness`, contract
  `rust/mishe-signal-freshness/CONTRACT.md`) owns the freshness verdict of one
  signal observation: `now - seen > max_age`, the comparison
  `signal_store.read` makes. The verdict is the exit status — 0 FRESH, 1 EXPIRED,
  2 for an argument count or number outside the contract — so the binary writes
  nothing to stdout and the caller reads no output. `signal_freshness.verdict`
  is the reader. It resolves its binary first — an explicit argument, then
  `MISHE_SIGNAL_FRESHNESS_BIN`, then the site install `$SITE/bin/mishe-signal-freshness`
  — and accepts the site install only when its sha256 matches the record beside it
  (`mishe-signal-freshness.install.json`); that install-record read is itself a
  named boundary in the policy. It maps every other outcome (a missing or
  non-executable binary, a non-object install record, a sha256 mismatch, exit 2, a
  timeout, a spawn failure) to
  `FreshnessUnknown` and has no Python fallback, so a broken or replaced binary
  cannot make a stale signal look fresh. Its four sibling sha256 install-record
  readers — `feed._tape_binary`, `feed._records_binary`, `tape_facts.facts_binary`
  and `unit_psi_text._systemd_show_binary` — reject a non-object record the same
  way. Wired into the authoritative expiry sweep: `signal_store.expire` resolves the
  binary once per sweep and calls `signal_freshness.verdict` for every OK signal; a
  configured but broken binary turns those signals UNKNOWN with a reason.
  `signal_store.read` keeps its display-level Python comparison. Tests:
  `tests/test_signal_freshness.py` and `tests/test_signal_store_freshness.py`.
- `rust/mishe-unit-paths` (`mishe-unit-paths`, contract
  `rust/mishe-unit-paths/CONTRACT.md`) decodes the absolute paths a unit's
  `systemctl show` text declares: it writes one ordered, deduplicated path per
  line for ASCII input and a single `DEFER` line for any non-ASCII input, which
  the Python decoder in `unit_paths.py` handles. `unit_paths.py` composes the
  binary and fails closed with `UnitPathsUnknown` when it is missing, mismatched
  or failing; there is no Python fallback. Its recorded answers are GOLDEN in
  `tests/test_rust_unit_paths.py`.

A change to any of these binaries' input, output, exit statuses or fault text is a
change to a protocol boundary: update its `CONTRACT.md` in the same commit and run
its contract test (`tests/test_mishe_tape_frames.py`,
`tests/test_mishe_records_verify.py`, `tests/test_mishe_tape_facts.py`,
`tests/test_mishe_network_probe.py`, `tests/test_rust_systemd_show.py`,
`tests/test_signal_samples.py`, `tests/test_signal_freshness.py`,
`tests/test_rust_unit_paths.py`).

A delivery request's spec holds the repository, the exact commit and its previous
commit. The author role and the originating wake key are not in the spec; they
enter the request's identity digest, which covers the spec, role and wake key, and
the wake key must name an actual wake snapshot for that role.

A patch request names a patch record and carries a `patch_binding` digest. That
digest covers the record's `files`, `runtime`, `test`, `review` and `delivery_plan`,
so the file list, runtime and test arguments, review record and plan live in the
patch record, not in the request. Submission checks that the record's deterministic
check passed — the phase is `tested` or `reviewed` — that its test exited 0, that its
delivery plan has the `activate`, `observe` and `revert_observe` steps, and that
every changed path is in the record's file list. It does not require a review
verdict: the deterministic check is the gate, and review is asynchronous evidence
after the pin moves (an ordinary delivery enqueues that review once the pin has
moved; see [Working as a durable mind](wall-coordination.md)). The plan the gate records also carries `revert`, the separate
argv that restores the old consumer pin. `check` refuses a plan without one, and
`revert` refuses a plan whose only restore path is its own activation, before any
runtime byte moves, because re-running the activation re-pins the new release and is
not a revert. An ordinary request instead carries its `check` and `observe`
commands and a `command_artifacts` map — each command script's bytes and mode —
which the request's own identity digest covers and which is compared byte-for-byte
again when the request is executed.

The source and command artifacts are validated when the request is submitted and
again when it is executed (`validate_source` in `src/mishe_tauftauf/delivery_requests.py`).
There is no path classification any more: `release_classification` returns `ordinary`
for every diff, so a request is refused only for what it carries, not for which
paths it touches. Human wall statements such as "permission required" are not
parsed into a grant or a delivery decision.

Keep reusable explanations tracked in Git. Keep runtime snapshots, experiments,
chat and local notes under the resolved site home. See [Working as a durable
mind](wall-coordination.md) for delivery and recovery behavior.
