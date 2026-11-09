# mishe-tape contract (version 0.1, responsibility: tape framing)

Template for every Rust slice: the contract lives next to the code, and it is
the thing a differential test checks. A caller depends on this file, not on
the Rust source.

## Scope

`mishe-tape` owns one responsibility: turning chat-tape bytes into framed
entries, or into the first framing fault. It does not verify record envelopes,
hold the verified store, fold roles, lock, or write the tape. Those stay in
Python until their own slices (see `docs/rust-runtime-migration-plan.md`, section 9).

## Invocation

```text
mishe-tape frames PATH [--start N] [--offset N] [--size N] [--offsets]
```

- `PATH`: a tape file, or `/proc/self/fd/N` when the caller holds an open handle
  and must read the inode it captured.
- `--start N`: sequence number the first entry must carry (default `1`).
- `--offset N`: byte offset to seek to before reading. Offsets reported on
  output stay absolute.
- `--size N`: absolute end offset in bytes. Reading stops there.
- `--offsets`: print frame offsets (`O <sequence> <offset>` per line) instead of
  entry bodies. Used to rebuild the checkpoint frame index.
- stdin is not read. Nothing is written to a file.

Unknown flags or missing values are a usage error (exit 1).

## Output (stdout)

For each entry in tape order, unless `--offsets`:

```text
E <sequence> <timestamp> <source> <body-bytes>\n
<body bytes, exactly body-bytes long>\n
```

- `sequence` is decimal, 20 ASCII digits in the input, printed without padding.
- `timestamp` and `source` are the header fields, verbatim.
- `body-bytes` is the body length in bytes. The body is the text the Python
  reader yields, with the `    | ` prefixes removed.
- With `--offsets`: `O <sequence> <offset>\n`, offset is the absolute byte
  offset of the header line.

Every entry before a fault is written before the fault is reported.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole requested range framed | Output is complete |
| 2 | Framing fault; entries before it are on stdout, the fault text is on stderr | Use the entries before the fault; treat the tape as malformed at that point, do not guess |
| 1 | Usage, open, seek, read or write error | Not a framing result; UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

## Faults (stderr text, identical to the Python `FeedError` text)

- `malformed header at line N`
- `invalid timestamp at sequence N`
- `malformed body framing at line N`
- `unterminated entry N`
- `malformed no-newline entry N`
- `empty body at sequence N`
- `feed is not UTF-8: <detail>`: the Python text may differ in the detail
  after the colon. This is a known, documented difference (`tests/test_mishe_tape_frames.py`,
  `test_utf8_error_fails_closed_with_documented_text_difference`). The exit
  status (2) matches. The entries before the fault do NOT always match: the Python
  reader decodes in 64 KiB or stream chunks, so a UTF-8 fault in the same chunk as
  earlier entries yields none of them, while the binary yields every entry before
  the bad bytes. Callers must treat a UTF-8 fault as "no entries from this read"
  and never fold the entries that preceded it. Streaming callers (`iter_feed_file`:
  `outcome_events.py`, `discovery.py`) may have received those entries before the
  `FeedError`, so they discard their partial result on a fault; checked 2026-11-08,
  both return UNKNOWN or an empty classification. Observed in a scratch differential
  on 2026-11-08 (not committed): a tape with one valid entry followed by a
  non-UTF-8 entry gave Python zero entries and the binary one entry, both exit 2.

## Framing rules (mirror `feed.py`)

- Header: optional `v1 `, 20 ASCII digits (`[0-9]`, not Unicode digits),
  timestamp `YYYY-MM-DDTHH:MM:SS[.frac]Z`, source `[A-Za-z0-9._/-]+`, ` ::\n`.
- Line splitting follows `str.splitlines` (LF, CR, CRLF, VT, FF, FS, GS, RS,
  NEL, LS, PS), keeping terminators.
- Body lines start with `    | `. The terminator line is `    .\n` or `    .-\n`.
- Sequence must equal the expected next number (starting from `--start`).

## Versioning

- The version is the crate version in `Cargo.toml`. A change to the output
  format, exit meanings, or fault texts bumps the minor version and updates this file in the same commit.
- A caller pins to a version through a hash-pinned install. The live install is
  site-local at `$SITE/bin/mishe-tape`, checked on every resolution against
  `$SITE/bin/mishe-tape.install.json` (sha256, source commit, contract revision);
  a mismatch is `TapeUnknown`, never a Python fallback. A swap is an atomic
  rename of that one binary with its own observation and rollback (the previous
  binary is kept until observed).

## Golden differential test

`tests/test_mishe_tape_frames.py` checks the binary against
`tests/fixtures/tape_framing_golden.json`: the answers the retired Python
whole-tape reader gave for each framing case, recorded before its deletion.
Fault cases compare the exit status and the exact fault text; a UTF-8 case
compares the exit status and the `feed is not UTF-8` prefix only. It runs when a
binary resolves (`MISHE_TAPE_BIN`, then the site install) and skips with the reason
otherwise. The Python reader no longer exists in `src/`; `feed.parse_feed` and
`feed.iter_feed_file` frame through this binary (memfd or shared-lock fd).

Known gap: the UTF-8 fault detail is relative to the body, not the absolute tape
offset the Python reader reported.

## Build

Reproducible from a clean `git archive` of `rust/mishe-tape` with the pinned
toolchain (`rust-toolchain.toml`, 1.97.0) and `cargo build --release --locked`.
No dependencies. Build outside the repository checkout, with
`systemd-run --user --scope -p MemoryMax=1G` and `nice -n 19`.
Verified 2026-11-08: a fresh build from HEAD has the same SHA-256 as the
retained artifact (`12dde4d2…ba710`).
