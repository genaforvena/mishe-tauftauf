# Protocol boundaries

[Mesh](mesh.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

Text is an external representation. The machinery should act on decoded values,
without reconstructing control from sentences it or another component rendered.
Human prose is content. A local sensor, chat dialect or CLI format belongs to a
replaceable adapter, so moving the plant does not require teaching its internal
control logic the new site's language. This does not require strict typing or a
single predetermined schema: ordinary Python values are sufficient.

The literal approved adapter files are in `parsing-policy.json`. They cover chat
lifecycle and linked-site sync decoding, feed framing, immutable record
envelopes, task-coordination findings, wall-outcome publication text, external
renderer/filter input, CLI arguments, the human operator view and the durable
effect ledger. The site window manifest and the wall-clear journals described
below are approved adapters too.

The ledger reads its append-only UTF-8 JSONL journals as a boundary:
invalid encoding or malformed records fail closed and remain intact as evidence.
The checker reads Python source and policy files. The reader-book command-name
check reads the served release's CLI parser and command-shaped spans in
`README.md` and `docs/*.md`; it reports unknown subcommands rather than
silently treating them as commands. New adapters require an explicit, reviewed
addition of their exact path; a directory-wide exemption would make the
constraint ineffective.

`docs_tools.py` is the reader-text search boundary: it validates a literal query,
enumerates `README.md` and immediate `docs/*.md`, and returns exact path/line/text
matches. Casefold matching and line-ending removal do not interpret Markdown
prose as protocol, commands or authority. Symlink pages are excluded; read/decode
failures propagate instead of becoming no-hit evidence. The caller consumes the
returned values without parsing the document again.

The checker writes a JSON report to `--out` or, by default,
`<home>/artifacts/check-docs-command-names.json`, creating parent directories.

`proc_table.py` decodes external process and tmux tables. It parses the `ps`
parent/child columns, `tmux list-panes` pid list, and single tmux pane fields;
`pane_start_command` is decoded as shell argv with quoting errors rejected. A
caller acts only on decoded values; malformed or unreadable output is not guessed.

`host_radio.py` decodes this host's BLE/Wi-Fi adapter state for the
space-perception sense: the kernel sysfs ABI for `/sys/class/bluetooth`,
`/sys/class/rfkill` and `/sys/class/net` (one newline-terminated value per file),
and the human-readable `bluetoothctl show` report. It returns ordinary Python
values and never mutates a radio. A missing adapter or a `bluetoothctl` read that
fails or does not return inside its bound is an explicit problem value, so the
consumer keeps the reading UNKNOWN rather than reading absence or a hang as calm.
An unreadable sysfs value is named `unread` rather than dropped, and an unreadable
rfkill switch is omitted from the decoded list: `rfkill_unreadable()` names the
skipped switches, so a caller can tell an omission from a radio that is absent.
It also decodes one repository-facing dialect, `decode_git_log_lines`: the
commit lines of `git log --oneline -L` ancestry output, so
the served-staleness reading can ask whether a sense's producing unit changed
since its pin, skipping git's diff bodies and blank lines. The consumer is the
discover scan (discovery.py `_served_staleness`), which marks senses stale.

`observations.py` also decodes one volatile token in the rendered discover and
senses panes: the displayed age of an unverified reading (`age=19m17s`) grows
on every refresh while the reading's meaning is unchanged, so
`redact_display_age` replaces a numeric age with `age=<age>` before the text
reaches the change digest. The redaction stops an observation event per
refresh while a stream is merely aging out or held frozen; the gap class
wording, the `last-phone` sample stamp, a non-numeric `age=unknown` and the
STATE line still separate digests. The redaction is scoped to those two
panes: `_observation_text` returns any other role's frame untouched, so a
growing numeric age on another role's pane still reads as a new event. One
such token is reachable — the health view's
`PANE LEASE: RED stale=window(age=Ns>limit=Ms)` line grows on every refresh
while a top-pane renderer stays stale. Measured 2026-10-11T02:29Z on the
published pane frames, no resident pane outside discover and senses carries a
live age that grows per refresh: the numeric ages on the genome, witness,
body-research and research-methods panes sit inside quoted tape records, which
rotate as the tape does rather than growing with a reading, so the scope limit
is recorded without an observed churn.

`native_gate.py` decodes the readiness probe's external inputs for a
native-loop mind: the tmux pane report (`pane_dead pane_current_command`), the
probe script's exit status, and this site's own verdict JSON. It returns
ordinary Python values and never pastes into a pane. The decoded engine name is
matched against every python name this site runs (`python`, `python3`,
`python3.12`) and the three TUI engines, so the gate literal cannot silently
narrow to the one interpreter the probe was written beside. A pane report that
tmux cannot answer, a probe that fails or times out, or an unreadable verdict
file are explicit problem values: the consumer keeps the reading UNKNOWN or
classifies a correct not-ready, and only an alive non-TUI pane the probe rejects
while the role's lifecycle shows a live mind reads RED. The verdict is recorded
per role at `checks/native-gate-<role>.json`, so a repaired gate and a slipping
one leave different durable evidence.

`inference_optiona.py` decodes the `omp --mode json --print` stdout of a one-shot
provider-only turn: a JSONL event stream in which the last assistant `message_end`
wins and any earlier partial content is not mistaken for a completed message.
The subprocess return code is checked before the stream is parsed, so a provider
failure's partial stdout can never become a success. A line that is not valid
JSON, or valid JSON that is not an event object, fails closed with the offending
line preserved in the record. Absent `usage`, `stopReason` and `responseId`
fields stay `None` (UNKNOWN) rather than zero. Callers consume the returned
record; a failure is a record with `ok=False`, never an exception.

`wall_text.py` decides wall-note ownership for the wall guard. The restore
document renders every peer wall undelimited into one text, so a mind's notes
can reproduce a peer's wall byte for byte and the write would otherwise be
accepted. Comparing two wall documents is an interpretation of external wall
text and lives in the adapter: `is_peer_copy` reports whether the notes are a
peer's document ignoring trailing blank lines — a paste that ends on a
different blank-line count is still the peer's document — while any other
difference is the writer's own text. `wall.py` acts on the answer alone.

`outcome_publication.py` decodes the wall-outcome publication: a reserved
header, the mind's prose and one immutable record reference. Recognition and
validation are separate answers on purpose. A publication a mind quoted inside
its own evidence still starts with the reserved prefix, so the coverage
classifier in `outcome_events.py` recognizes a candidate that way and then
validates it — a damaged publication is reported as a coverage gap instead of
being dropped as an event. The advisor's previous-context reader in
`witness_analysis.py` needs the whole publication or nothing, so
`decode_publication_body` returns one decoded publication or `None`, and the
comparison against the immutable record stays in the consumer. Both read the
same header grammar, the same reference line and the same prose span, so a
displaced header or a second reference cannot mean one thing to each. The
remaining `witness_analysis.py` budget reads its own local advice files and
command line, not the publication dialect.

`chat_protocol.py` interprets historical wake, yield and clear receipts. A
seed-sourced receipt's first line supplies the lifecycle kind and header fields;
for yields, the decoder also extracts a reported result from a subsequent line
beginning `Turn settled (<lowercase-result>);`. The canonical writer places this
settlement record immediately after the yield header. The decoder currently
accepts the first matching settlement line anywhere after that header; it does
not enforce placement or uniqueness. All remaining text is retained as prose,
but a prose line matching that marker is therefore not opaque to the decoder.
Malformed reserved lifecycle headers raise a `ProtocolError` naming the original
tape sequence. Scheduling, task projections, settlement reconciliation and
lifecycle diagnostics consume those decoded fields. The existing external tape
format remains readable.

Protocol decoding establishes syntax and values. The internal consumer still
checks whether a settlement refers to its actual pending wake. Successful
transport, human-reported settlement and semantic success remain distinct facts.

`window_manifest.py` decodes the site's declared tmux window names from
`health/windows.json`: a JSON list of strings. A missing manifest means no
declared utilities; malformed JSON or a non-list fails closed as a `ValueError`,
never a guessed list, so each caller falls back to its own safe default.

`clear_journal.py` decodes the wall-clear journals a supervisor writes under
`checks/`: one JSON object per `wall-clear-*.json` rotation. A journal that
cannot be read, or whose text is not a JSON object, reads as unreadable
monitoring damage (`None`), so the stall detector reports UNKNOWN rather than
silently passing; a healthy rotation is simply absent from the concern set.

Run the deterministic check from the repository:

```sh
env PYTHONPATH=src .venv/bin/python tools/check_parsing.py
```

CI and `tests/test_parsing_guard.py` run the same check. The AST checker detects
the listed regex and decoding APIs and string operations, including direct import
aliases. Outside approved files, existing calls have a frozen budget of exact
expression fingerprints. Additional calls or changed expressions fail even if
the total count stays constant. Missing/corrupt policy, missing source and Python
syntax errors fail visibly. Tests inject a new parser, alter and duplicate a
legacy call, and break the policy/source to exercise failure detection.

This is a migration constraint, not certification that the whole repository has
no internal parsing. Legacy exceptions remain in the policy; remove their budget
when migrating the corresponding consumer. Do not regenerate the legacy budget
to make a failing change pass. Each migration must identify the external dialect,
move its interpretation into an adapter, test malformed/unknown input and keep
the internal consumer on decoded values. The outcome publication dialect —
the reserved header, the prose and the immutable record reference —
completed that migration into `outcome_publication.py`: the coverage
classifier in `outcome_events.py` recognizes a candidate by prefix and validates
it, while the advisor's previous-context reader in `witness_analysis.py` decodes
the whole publication or treats it as absent. Retain damaged publication
evidence and coverage gaps.

The checker conservatively treats some formatting operations as parsing. It
cannot prove that arbitrary Python implements no parser: indexing, loops,
dynamic aliases or another executable can implement one without calling a
listed method. Review raw-text access and data flow alongside the check. A
measured instance of the limit (2026-10-13): rewriting a prefix test from
`unit.startswith(PREFIX)` to `unit[:len(PREFIX)] == PREFIX` kept the same
interpretation outside an adapter and changed the verdict from a reported
violation to a pass, because the check recognizes a fixed set of call names.
A frozen-budget pass is therefore not evidence that an interpretation moved
into an adapter.

This first check covers Python under `src`, `coordination` and `tools`; tests,
gitignored deployment scripts and third-party code are outside its scan. A
separate executable used to decode external text is still an adapter, and its
location and contract must be recorded rather than hidden as an internal helper.

The human operator window has a complete dashboard above the existing shell.
`operator/brief.md` holds current discussion and decisions;
`operator/commands.md` holds exact installed commands and local capability
provenance. `walls/operator.md` retains current human obligations. These are
site-local notes, not committed material or machine control. A replant preserves
the edited notes and original shell; it refreshes only its marked dashboard pane.

Read the full view with the site CLI:

```sh
mishe --home SITE_HOME pain read operator --launcher dashboard
```

The ordinary watcher publishes every five seconds. Missing or invalid inputs
produce UNKNOWN; missing, corrupt or older-than-30-second dashboard records fail
closed. The operator renderer also participates in the existing pane-lease
observation because it is installed in `top-pains/`; a stopped renderer becomes
UNKNOWN or RED through that observation and the health dashboard, rather than
being counted as a healthy shell. The operator remains a human, not a supervised
mind or a decision gate.

Telegram is optional local deployment material. This host's source scripts live
in the local `lte-workstation/scripts` checkout and its bot token/chat ID live in
`~/.config/remote-access/env`. An explicitly authorized install can copy the
needed fields privately and copy/adapt the sending scripts into the owned site;
record provenance, commands and verification in the operator view. Keep
credentials out of Git and console output, and keep sender state local. Do not
assume another plant has the same account, sensor or textual dialect. Installing
a capability does not request an automatic sender or authorize unsolicited
messages.
