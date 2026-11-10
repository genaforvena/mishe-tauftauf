# mishe-records contract (version 0.1, responsibility: record verification and the verified store)

Template: `rust/mishe-tape/CONTRACT.md`. The contract lives next to the code and
is the thing a differential test checks. A caller depends on this file, not on
the Rust source.

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
  Unknown lines are a protocol fault: exit 1, no guess.
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
| `V <sequence>\n` | A store row matched the file's current identity and was younger than `VERIFIED_MAX_AGE`; verified without re-reading the record | verified |
| `N <sequence>\n` | No usable row: the envelope was verified now and its payload will be stored | verified |
| `F <sequence>\n` | The entry is not a well-formed single agreeing reference (no reference, several, digests unequal, or a legacy `{` control line) | run the Python `records.payload(entry)` path unchanged, including its error text |

With `--payloads`, `V` and `N` are followed immediately by the payload body:

```text
P <sequence> <payload-bytes>\n
<the envelope's "payload" object, JSON, exactly `payload-bytes` long>\n
```

The bytes are the object as `records.prepare` canonicalised it (`sort_keys=True`,
`separators=(",", ":")`), so the caller's `json.loads` sees the same dict it
would have computed. `F` never carries a body.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole stream processed | Complete |
| 2 | An entry that is a well-formed reference failed verification (missing, unreadable, checksum mismatch, invalid envelope); the fault text is on stderr, identical to the Python `ValueError` text, and the results before it are on stdout | Fail the pass as the Python verifier does today; store nothing from the replaced or unreadable directory |
| 1 | Usage, protocol, open, read, write or sqlite error | Not a verification result; UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

## The store it writes (shared with Python today)

- `SITE/records.verified.sqlite3`, `PRAGMA journal_mode=WAL`,
  `CREATE TABLE verified (digest TEXT PRIMARY KEY, ino INTEGER, size INTEGER,
  ctime_ns INTEGER, mtime_ns INTEGER, verified_at REAL, payload BLOB NOT NULL)`.
- `payload` is `zlib.compress(json.dumps(payload, sort_keys=True,
  separators=(",", ":")).encode(), 6)` — level 6, exactly as `records.py` writes
  it, so a Python reader decompresses and parses it unchanged.
- A row is a hit only while all four identity fields equal the record file's
  current `(st_ino, st_size, st_ctime_ns, st_mtime_ns)` taken by
  `lstat`/`fstatat` with no symlink follow through the records-directory fd, and
  `0 <= now - verified_at < VERIFIED_MAX_AGE` (6 h).
- A row that is missing, has a non-integer identity field, a non-numeric
  `verified_at`, a non-`BLOB` or undecodable payload is a miss, never served.
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
  never substitutes a stale payload. No caller-visible result changes.

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
  round; that interop is a test, not an assumption (checked 2026-11-10 for
  `digest`, integer, REAL and BLOB columns in both directions).
- The crate has a fetched dependency graph (`rusqlite` + bundled
  `libsqlite3-sys`, 6 crates, 5.4 MB, 39.5 s, 378 MB peak; plan 10.13) and is
  **not** buildable offline from a bare checkout. The live install is therefore
  a prebuilt artifact at `$SITE/bin/mishe-records` recorded by sha256, source
  commit and contract revision in `$SITE/bin/mishe-records.install.json`; a
  mismatch is `RecordsUnknown`, never a Python fallback. Swapping is one atomic
  rename with the previous binary kept until observed.

## Differential test

`tests/test_mishe_records_verify.py` runs the binary when it resolves
(`MISHE_RECORDS_BIN`, then the site install) and skips with the reason
otherwise. It checks, against the live Python path on a fixture site:

- byte-identical payloads for the whole fixture tape, hit and miss paths;
- identical decision line per entry (`V`/`N`/`F`) and identical fault text and
  exit status for each malformed case;
- a store written by Python is read by the binary and a store written by the
  binary is read by Python (`records.VerifiedStore`);
- a record rewritten mid-pass is verified again, not served;
- a records-directory swap before a flush stores nothing from the old inode;
- a kill between flushes leaves a readable store with only older rows.
