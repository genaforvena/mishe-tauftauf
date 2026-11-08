# mishe-tape-facts contract (version 0.1 draft, responsibility: byte-level tape facts)

Status: DRAFT. Not built, not installed, not delivered. Written 2026-11-08 by the
rust-plan mind as the first step of Slice C. The Python reader it mirrors is
`_coord_dm_disposition_age` in `src/mishe_tauftauf/discovery.py`. No code exists
yet; the differential test is not written yet.

## Scope

`mishe-tape-facts` extracts, in one pass, the byte-level facts that the
disposition reading needs from each framed chat-tape entry. It does not:

- join DMs to replies, seq citations or wall outcomes (that join stays in Python);
- fold roles, apply the 6-hour window or the maturity gate (Python, with `now`);
- read `records/` or verify record digests (Python);
- compute `wall_cited` (deferred: it needs `names_sender`, which this draft has not
  yet read; v0.1 does not emit it, and the Python reading keeps computing it).

This follows the human scope decision in the memory "Rust scope: byte-level only":
Rust does framing, UTF-8 and byte-level extraction; the role fold and the record
verification stay Python.

## Input

Standard input is the output of `mishe-tape frames` (`rust/mishe-tape/CONTRACT.md`),
without `--offsets`:

```text
mishe-tape frames PATH [--size N] | mishe-tape-facts
```

- The framing contract is not re-implemented. Each entry is an `E` header and
  exactly `body-bytes` of body, followed by `\n`.
- Stdin is read; no file argument. Usage errors (any argument) are exit 1.
- The caller reads two exit statuses (`mishe-tape` and `mishe-tape-facts`).
  Use `set -o pipefail` or `PIPESTATUS`. A `mishe-tape` exit 2 means a framing
  fault: the whole reading is UNKNOWN.

## Output (stdout)

One line per fact, fields separated by single spaces. Lines are emitted in tape
order. A single entry may emit several lines.

| Line | Emitted when | Meaning |
|---|---|---|
| `DM <seq> <ts> <source> <target>` | body matches `^\[dm\] to=([A-Za-z0-9_.-]+)` | an addressed DM; `target` is group 1 |
| `REF <seq> <ts> <sha>` | body contains `\[record\] records/([0-9a-f]{64})\.json sha256=[0-9a-f]{64}` | the first such match in the body; `sha` is group 1 |
| `OUT <seq> <ts> <role> <runs>` | body starts with `Wall outcome `, then a non-space word, then ` by ` and a role matching `[A-Za-z0-9_.-]+` | `role` is that role; `runs` is the comma-separated list of maximal ASCII digit runs in the whole body, or `-` if there are none |

- `seq` is the entry's sequence, decimal, as in the `E` header.
- `ts` and `source` are copied verbatim from the `E` header. `ts` is not parsed.
- `runs` lets Python test the citation rule `(?<!\d)SEQ(?!\d)` without a regex pass
  over the body: SEQ is cited iff it equals one of the runs. Runs are compared as
  strings, so a run of any length is emitted as-is.

Only `OUT` lines carry body-derived digit runs. An entry with no fact emits nothing.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole input read, all facts written | Facts are complete for the framed entries |
| 1 | Usage error (any argument) | Not a result; UNKNOWN |
| 2 | Malformed input: an `E` line the binary cannot parse, or a short body | Discard all facts from this run; UNKNOWN. The tape is not a shorter tape |
| 3 | Read or write error | UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

Unlike `mishe-tape` exit 2, a partial fact set from a facts exit 2 is never used.
The disposition reader already abandons the whole reading on a fault.

## Known differences from the Python reader (must be measured before live use)

- Python `\d` and `\s` in `WALL_OUTCOME_RE`/`seq_re` match Unicode digits and
  spaces. This binary matches ASCII only. A seq number adjacent to a non-ASCII
  decimal digit can be cited in Rust and not in Python (or the reverse). The
  differential fixture must include such a case; a difference is a contract bug.
- `FeedError` text for UTF-8 faults is not reproduced here; `mishe-tape` already
  owns that and fails first.

## Versioning

- Crate version in `Cargo.toml` (not yet created). A change to lines, exit meanings or
  the extraction rules bumps the minor version and updates this file in the same commit.
- Install path (proposed, not wired): `runtime/current/bin/mishe-tape-facts`, with
  the same observed-and-rollback rule as `mishe-tape`.

## Differential test (planned, not written)

1. Copy the live `chat.log` to a fixture in `artifacts/rust-plan-20261107/` (never
   committed) and record its SHA-256; also use a frozen small fixture committed under
   `tests/fixtures/`.
2. For the same bytes, run the Python extraction (the same regexes as
   `discovery.py` applied to `iter_feed_file` bodies) and the pipeline above.
3. Compare the sets of `DM`, `REF` and `OUT` facts, and compare the citation
   result for every outcome/seq pair, line for line. Any difference fails the test.
4. Then compare the full `_coord_dm_disposition_age` sample string with the
   binary feeding the join (Python fold unchanged). Only then consider live use.

## Build

Not yet specified. A later draft will copy `rust/mishe-tape/` build rules:
pinned toolchain, `cargo build --release --locked`, no dependencies, built outside the
checkout, with `systemd-run --user --scope -p MemoryMax=1G` and `nice -n 19`, and
only after the host check (at least 8 GB free, pressure near zero).
