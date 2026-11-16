# mishe-signal-freshness contract (version 0.1, responsibility: signal freshness verdict)

Status: BUILT, differential-tested (`tests/test_signal_freshness.py`) and wired into the
signal store's authoritative expiry sweep (`signal_store.expire`). The reader is
`src/mishe_tauftauf/signal_freshness.py`. `signal_store.read` keeps its Python comparison
as a display-level safety net, not the state-changing authority. Owner: rust-plan.

## Scope

The verdict for one signal observation: is `now - seen > max_age`? It does not read
`NAME.json` or `.seen`, does not build the `Signal` value or its reason text, and does
not expire or write edges. Those stay in `signal_store.py`.

## Invocation

```text
mishe-signal-freshness SEEN NOW MAX_AGE
```

Three arguments, each an ASCII float in Python `repr(float)` form: digits, `+`, `-`, `.`,
`e`/`E`, `inf` and `nan`. Anything else (underscores, spaces, empty tokens) is outside
the contract. There is no stdin and no file.

## Verdict (exit status)

| Exit | Meaning |
|---|---|
| 0 | FRESH: `now - seen > max_age` is false |
| 1 | EXPIRED: the comparison is true |
| 2 | outside the contract: wrong argument count or a malformed number (message on stderr) |

Nothing is written to stdout. IEEE double arithmetic, so `nan` is FRESH and `inf` against
a finite age is EXPIRED, matching Python.

## Errors

The reader maps any status other than 0 or 1, a missing or non-executable binary, a
spawn failure and a timeout to UNKNOWN (`FreshnessUnknown`). It never falls back to
Python, so a broken binary cannot make a stale signal look fresh.

## Evidence

Unit tests in `src/main.rs` cover the comparison and the number parser. The Python
differential test compares 9 fixed boundary triples and 2,000 seeded random triples
against the Python comparison. It also checks that the reader fails closed on a missing
binary, an exit status of 2 and a timeout.

Build: `cargo build --release --locked --offline` in this directory (no dependencies).
The delivery check builds every `rust/` crate.
