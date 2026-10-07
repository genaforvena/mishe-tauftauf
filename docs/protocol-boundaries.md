# Protocol boundaries

[Mesh](mesh.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

Text is an external representation. The machinery should act on decoded values,
without reconstructing control from sentences it or another component rendered.
Human prose is content. A local sensor, chat dialect or CLI format belongs to a
replaceable adapter, so moving the plant does not require teaching its internal
control logic the new site's language. This does not require strict typing or a
single predetermined schema: ordinary Python values are sufficient.

The literal approved adapter files are in `parsing-policy.json`. They cover chat
lifecycle decoding, feed framing, immutable record envelopes, external
renderer/filter input, CLI arguments, the human operator view and the durable
effect ledger. The ledger reads its append-only UTF-8 JSONL journals as a boundary:
invalid encoding or malformed records fail closed and remain intact as evidence.
The checker is also a boundary: it reads Python source and policy files. New
adapters require an explicit, reviewed addition of their exact path; a
directory-wide exemption would make the constraint ineffective.

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
the internal consumer on decoded values. The next concrete migration is outcome
publication interpretation shared by `outcome_events.py` and
`witness_analysis.py`. Retain damaged publication evidence and coverage gaps.

The checker conservatively treats some formatting operations as parsing. It
cannot prove that arbitrary Python implements no parser: indexing, loops,
dynamic aliases or another executable can implement one without calling a
listed method. Review raw-text access and data flow alongside the check. This
first check covers Python under `src`, `coordination` and `tools`; tests,
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
mishe-tauftauf --home SITE_HOME pain read operator --launcher dashboard
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
