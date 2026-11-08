# mishe-tape-facts contract (version 0.2, responsibility: byte-level tape facts)

Status: BUILT and differential-tested (tests/test_mishe_tape_facts.py). Not
installed, not wired into any live consumer, not delivered. Owner: rust-plan.
The Python reader it mirrors is `_coord_dm_disposition_age` in
`src/mishe_tauftauf/discovery.py`, which keeps the role fold, the window, the
maturity gate and record verification.

## Scope

`mishe-tape-facts` extracts, in one pass, the byte-level facts that the
disposition reading needs from each framed chat-tape entry. It does not:

- join DMs to replies, seq citations or wall outcomes (that join stays in Python);
- fold roles, apply the 6-hour window or the maturity gate (Python, with `now`);
- read `records/` or verify record digests (Python);
- compute `wall_cited` (deferred: it needs `names_sender`, which v0.2 does not read).

Scope follows the human decision in the memory "Rust scope: byte-level only":
Rust does framing, UTF-8 and byte-level extraction; the role fold and the record
verification stay Python.

## Input

Standard input is the output of `mishe-tape frames` (`rust/mishe-tape/CONTRACT.md`),
without `--offsets`:

```text
mishe-tape frames PATH [--size N] | mishe-tape-facts
```

- The framing contract is not re-implemented. Each entry is an `E` header and
  exactly `body-bytes` of body, followed by `\n`. A header is
  `E <seq> <ts> <source> <len>` with single spaces.
- Stdin is read; no file argument. Any argument is a usage error (exit 1).

## Output (stdout)

Lines are emitted in tape order. A single entry may emit several lines; an
entry with no fact emits nothing. Fields are separated by single spaces.

| Line | Emitted when | Meaning |
|---|---|---|
| `DM <seq> <ts> <source> <target>` | body starts with `[dm] to=` followed by one or more of `[A-Za-z0-9_.-]` | an addressed DM; `target` is the maximal such run |
| `REF <seq> <ts> <sha>` | body contains `[record] records/<64 lowercase hex>.json sha256=<64 lowercase hex>` | the first such match; `sha` is the first 64-hex group |
| `OUT <seq> <ts> <role> <runs>` | body starts with `Wall outcome `, a non-empty token with no whitespace in the ASCII set Python `str` `\s` matches (0x09-0x0D and 0x1C-0x20; a non-ASCII byte in the token defers), a single space, `by `, then one or more `[A-Za-z0-9_.-]` | `role` is that run; `runs` is described below |
| `DEFER <seq> <ts>` | body starts with `Wall outcome ` and its header token contains a non-ASCII byte | the caller must apply the Python rule (`WALL_OUTCOME_RE`) to this entry's body itself |

- `seq` is the entry's sequence, decimal, as in the `E` header. `ts` and `source`
  are copied verbatim from the `E` header; `ts` is not parsed.
- `runs` is `-` when the body has no ASCII digit run; otherwise the comma-separated
  maximal ASCII digit runs of the whole body, in order. Python tests the citation
  rule `(?<!\d)SEQ(?!\d)` for an entry by checking whether SEQ equals a run.
- `runs` is `!` when some ASCII digit run touches a non-ASCII byte (the character
  before or after it). Python's `\d` matches Unicode decimal digits, so the
  citation for that entry cannot be decided from ASCII runs. The caller applies the
  Python citation rule to that entry's body.

## Why the deferrals exist (differential evidence)

Python `\d` and `\s` are Unicode-aware; the binary is ASCII-only. Two places
could differ, so both are routed back to Python rather than guessed:

1. A header token containing a non-ASCII byte (e.g. U+00A0): Python's `\S+` stops
   at that character, Rust's ASCII view would not. → `DEFER`.
2. A digit run next to a non-ASCII character (e.g. `1٣2`): Python may treat an
   adjacent Unicode digit as part of the run. → `runs = !`.

`tests/test_mishe_tape_facts.py` runs fixture bodies covering both and compares the
DM target, REF hash, OUT role and per-probe citation against the Python regexes.
Any mismatch outside a deferral fails the test.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole input read, all facts written | Facts are complete for the framed entries |
| 1 | Usage error (any argument) | Not a result; UNKNOWN |
| 2 | Malformed input: an `E` line the binary cannot parse, a short body, or a missing body terminator | Discard all facts from this run; UNKNOWN. The tape is not a shorter tape |
| 3 | Read or write error | UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

## Build

- Crate `rust/mishe-tape-facts/`, no dependencies, `Cargo.lock` tracked, toolchain
  pinned by `rust-toolchain.toml` (1.97.0, as `mishe-tape`).
- Built outside the checkout with the target dir under the gitignored site:
  `CARGO_TARGET_DIR=<site>/artifacts/rust-plan-20261107/target-facts
  systemd-run --user --scope -q -p MemoryMax=1G nice -n 19 cargo build --release -j1`.
  Build only after the host check (at least 8 GB free, memory pressure near zero).

## Versioning

- Version 0.2 (2026-11-08): DEFER line and `!` runs marker added; differential test
  written. A change to lines, exit meanings or extraction rules bumps the minor
  version and updates this file in the same commit.
- Install path (not wired): a `components/mishe-tape-facts/current` pointer under the
  per-component layout that genome is defining; do not place it on the global pin.
