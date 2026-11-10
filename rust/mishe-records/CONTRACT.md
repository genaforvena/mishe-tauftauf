# mishe-records contract (version 0.2, responsibility: record verification and the verified store)

Template: `rust/mishe-tape/CONTRACT.md`. The contract lives next to the code and
is the thing a differential test checks. A caller depends on this file, not on
the Rust source.

Version 0.2 (2026-11-10, first implementation): the `F` decision became `S` plus
an explicit fault, because reading `records.cached_payload` closely showed that
every structural case the old `F` covered *raises* (`records.payload` reaches
`records.envelope`, which raises the same `ValueError` the pass propagates); only
"the entry carries no `[record]` line at all" is a case the Python pass ignores.
The `V` row now also covers a digest already verified in the same pass, which is
what `cached_payload`'s `pending` map serves without re-reading the record.

## Scope

`mishe-records` owns two things and nothing else:

- verifying the record envelope an entry references (the `records.envelope`
  rules: exactly one agreeing `[record]` reference, sha256 of the file bytes
  equals the digest, the envelope is `{"version":1, "kind":<non-empty str>,
  "payload":<object>}`);
- the per-site verified-payload store (`records.verified.sqlite3`) that records
  a payload once it has passed verification.

It does not frame the tape (`mishe-tape`), fold roles, lock, write the tape,
keep the identity index (`feed.py:527-576`) or the record-kind index
(`records.KindIndex`), or publish records (`records.prepare`). Framing stays in
`mishe-tape`; the caller composes the two through a pipe.

## Invocation

```text
mishe-records verify --home SITE [--payloads] [--records-fd N]
```

- stdin: a framed entry stream in `mishe-tape frames` output form
  (`E <sequence> <timestamp> <source> <body-bytes>\n<body>\n`), in tape order.
  Unknown lines are a protocol fault: exit 1, no guess. Every entry the caller
  wants a decision for must appear in the stream.
- `--home SITE`: the site root; the store is `SITE/records.verified.sqlite3`
  and the records directory is `SITE/records`.
- `--records-fd N`: a descriptor the caller already holds on `SITE/records`,
  used for every identity `lstat` and `openat` and left open. Without it the
  program opens the directory itself once.
- `--payloads`: emit payload bodies (see Output). Without it the caller only
  needs "verified or not" (the role read, `view_entries`, `access`).
- Nothing else is read; the tape file is never opened; nothing is written
  outside the store.

## Output (stdout)

One line per input entry, in input order:

| Line | Meaning | Caller reading |
|---|---|---|
| `V <sequence>\n` | A store row, or a payload this pass verified earlier, matched the file's current identity (and, for a store row, was younger than `VERIFIED_MAX_AGE`); verified without re-reading the record | verified |
| `N <sequence>\n` | Nothing usable was held: the envelope was verified now and its payload will be stored | verified |
| `S <sequence>\n` | The entry carries no `[record]` line; `feed._VerificationPass.verify` ignores it and so does the caller | not verified, no fault |

With `--payloads`, `V` and `N` are followed immediately by the payload body:

```text
P <sequence> <payload-bytes>\n
<the envelope's "payload" object, JSON, exactly `payload-bytes` long>\n
```

The bytes are the canonical JSON of the payload (`sort_keys=True`,
`separators=(",", ":")`), so the caller's `json.loads` sees the same dict it
would have computed; `V` and `N` emit the same bytes for the same payload. `S`
never carries a body.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole stream processed | Complete |
| 2 | The entry's `[record]` reference is not one well-formed agreeing reference, or verification failed (missing, unreadable, checksum mismatch, invalid envelope); the fault text is on stderr, identical to the Python `ValueError` text, and the results before it are on stdout | The Python pass raises the same `ValueError`; fail the pass, store nothing from the replaced or unreadable directory |
| 1 | Usage, protocol, open, read, write or sqlite error | Not a verification result; UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

The structural faults and their texts (`feed.py`/`records.py`):

| Entry | Text |
|---|---|
| a legacy `{` control line *and* a `[record]` line | `mixed legacy JSON and immutable record reference` |
| several `[record]` lines, or a line that does not fullmatch the reference pattern, or none (reached only with a reference line present) | `missing or invalid immutable record reference` |
| the two digests in the reference disagree | `record digest mismatch or missing site context` |

## The store it writes (shared with Python today)

- `SITE/records.verified.sqlite3`, `PRAGMA journal_mode=WAL`,
  `CREATE TABLE verified (digest TEXT PRIMARY KEY, ino INTEGER, size INTEGER,
  ctime_ns INTEGER, mtime_ns INTEGER, verified_at REAL, payload BLOB NOT NULL)`.
- `payload` is the payload's canonical JSON, zlib-compressed at level 6, exactly
  the bytes `records.py` writes. Byte-identity with C zlib's output is *not*
  required and not claimed: the reader only decompresses, so interop is checked
  as "Python's `json.loads` sees the same dict" in both directions.
- A row is a hit only while all four identity fields are SQLite integers,
  `verified_at` is a SQLite number, `payload` is a BLOB, the four equal the
  record file's current `(st_ino, st_size, st_ctime_ns, st_mtime_ns)` taken by
  `lstat`/`fstatat` with no symlink follow through the records-directory fd, and
  `0 <= now - verified_at < VERIFIED_MAX_AGE` (6 h). Anything else is a miss,
  never served.
- The lookup applies those rules *before* decompressing, and a verify-only pass
  never decompresses at all: a typed but corrupt blob is still a `V` there,
  exactly as `VerifiedStore.lookup(..., verify_only=True)` returns `True`.
- Writes are batched: new rows are inserted in one transaction per batch
  (`VERIFIED_FLUSH_ROWS = 500` rows) and one at the pass end. Before each
  flush, the descriptor for `SITE/records` must still be the directory the pass
  read (`records.check_pass_directory`); if it is not, exit 2 and store nothing.
  A kill between flushes leaves the store consistent and loses only unflushed
  rows, which the next pass verifies again.
- A digest referenced again inside one pass is checked against the file's
  current identity before its payload is served, so a record rewritten mid-pass
  is verified again, not served (`records.cached_payload`, the S0b rule).
- A missing, unreadable or corrupt store disables the cache for the pass; it
  never substitutes a stale payload. The directory swap check still runs.

## Known differences from the Python path

Both are unreachable through `records.prepare` (`allow_nan=False`, and its
records are valid JSON), and both keep the exit status:

- A record whose bytes hash to its digest but are not valid JSON: exit 2, with
  `record is not JSON: <detail>` instead of CPython's `JSONDecodeError` text.
- A record whose payload holds `NaN`/`Infinity` (Python's `json.loads` accepts
  them by default; the Rust parser refuses) or an integer above `u64::MAX`
  (the Rust parser reads it as a float). Everything up to `2^64-1` round-trips
  exactly.

## Measured target (2026-11-10, live 544 MB store)

The store lookup this program owns costs **2.53 CPU-s per cold read** in Python
today (196,301 lookups; measured 2.214 CPU-s over the store's 171,976 digests,
`artifacts/rust-plan-20261107/s0b-lookup-target.py`), of which 0.48 is the
identity `lstat`, 1.73 the one-`SELECT`-per-digest sqlite read, and 0.28 the
Python `lookup()` wrapper. Slice B's acceptance is that the whole cold
`wall show --owner genome` verification drops by at least the 2.0 CPU-s this
boundary can remove, with byte-identical output. The predecessor retired in the
same release: `records.VerifiedStore`, `lookup`, `store`, `cached_payload`,
`verified_store` and the verification half of `feed._VerificationPass`
(`feed.py:465-551`).

## Versioning and provisioning

- The version is the crate version in `Cargo.toml`. A change to the store
  schema, the output format, exit meanings or fault texts bumps the minor
  version and updates this file in the same commit.
- The store format is shared, so a format change needs both sides in one
  release. The Python reader accepts what this program writes and the other way
  round; that interop is a test, not an assumption.
- The crate has a fetched dependency graph (`rusqlite` + bundled
  `libsqlite3-sys`, plus `libc`, `serde_json` and `flate2`; plan 10.13) and is
  **not** buildable offline from a bare checkout. The live install is therefore
  a prebuilt artifact at `$SITE/bin/mishe-records` recorded by sha256, source
  commit and contract revision in `$SITE/bin/mishe-records.install.json`; a
  mismatch is `RecordsUnknown`, never a Python fallback. Swapping is one atomic
  rename with the previous binary kept until observed.

## Differential test

`tests/test_mishe_records_verify.py` runs the binary when it resolves
(`MISHE_RECORDS_BIN`, then the site install, sha256-checked against its install
record) and skips with the reason otherwise. Checked against the live Python
path on fixture sites (2026-11-10, 4 cases green):

- identical decision lines (`V`/`N`/`S`) and byte-identical payloads for the
  same entries, cold and warm, including a digest repeated inside one pass;
- a store written by the binary is served by `records.VerifiedStore` and by a
  full `feed._VerificationPass`, with the same payloads;
- identical fault text and exit status for a missing record;
- a record rewritten after it was stored is verified again, not served.

Not yet checked, and required before the cutover: a `[record]` line that does
not fullmatch (and the `mixed legacy`/`several references`/`unequal digests`
texts), the records-directory swap before a flush, and a kill between flushes.
