# mishe-unit-paths contract (version 0.1, responsibility: unit declaration paths)

Responsibility: decode the absolute paths a unit's `systemctl show` text declares
— the paths in `ExecStart`, `WorkingDirectory` and `Environment`. It owns only that
decoding. It runs no systemctl, reads no file, holds no state, and writes nothing
but stdout.

It is the byte-exact counterpart of `unit_properties.declared_paths` for ASCII
input. Non-ASCII input is deferred to the Python decoder (see below) because
Python's `\w` and `\s` are Unicode-aware where this binary is ASCII-only.

## Invocation

```text
mishe-unit-paths            (stdin: systemctl show output)
```

- No arguments. Any argument is a usage error (exit 1).
- Input bound: 1 MiB. Larger input is a data fault (exit 2), not a truncated result.

## Decoding rules

- A path begins at `/` whose preceding byte (if any) is not a word byte
  (`[A-Za-z0-9_]`), `.`, `/` or `-`: the Python lookbehind `(?<![\w./-])`.
- The path continues over every byte that is not Python ASCII `\s` and not
  systemd's list punctuation `;:,"'()`. `/` is allowed inside a path.
- Trailing `.` and `,` are stripped (`str.rstrip(".,")`).
- Paths are emitted in first-seen order; repeats are dropped. A search resumes
  after the full match, not after the stripped tail.
- If the input contains any non-ASCII byte, no paths are emitted: the whole
  answer is the single `DEFER\n` line and the caller asks the Python decoder.

## Why the deferral exists (differential evidence)

Measured with `.mishe-tauftauf/artifacts/rust-plan-20261107/unit_paths_probe.py`
(2026-11-16): Python `re` is Unicode-aware, this binary is not.

1. `Environment=X=\u00e9/home/u/site/tool`: Python's `\w` matches `é`, so the
   lookbehind rejects the following `/` and the path is absent. An ASCII `\w`
   would accept it. -> `DEFER`.
2. `Environment=X=/home/u/site/\u2028tool`: Python's `\s` matches U+2028, ending
   the path at `/home/u/site/`. An ASCII `\s` would include the separator. -> `DEFER`.

`tests/test_rust_unit_paths.py` records the byte-exact answers for the ASCII
cases and asserts the DEFER line for the non-ASCII cases. Any mismatch fails the
test; the binary never guesses a Unicode answer.

## Output (stdout)

- ASCII input: one path per line, `\n`-terminated, in first-seen order. Empty
  stdout is a valid empty result.
- Non-ASCII input: exactly `DEFER\n`. This is a routing decision, not a result.

## Exit statuses

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole input read; stdout is the result or the DEFER line | Use it |
| 1 | Usage, or read error | UNKNOWN |
| 2 | Data fault: input is not valid UTF-8, or over the bound | UNKNOWN; never guess |
| 3 | Write error | UNKNOWN |
| other | Not part of the contract | UNKNOWN, never GREEN |

## Caller

`mishe_tauftauf.unit_paths.declared_paths` (Python adapter). It finds the binary
through `MISHE_UNIT_PATHS_BIN` (override) or `$SITE/bin/mishe-unit-paths`, checked
against `mishe-unit-paths.install.json`. A missing, mismatched or failing binary
raises `UnitPathsUnknown`. On the DEFER line the adapter calls the retained Python
`unit_properties.declared_paths` for that input; this declared arm is the only
Python path and is not a silent fallback.

## Build

Locked: `Cargo.lock`, `rust-toolchain.toml` (1.97.0), no dependencies. Built with
`cargo build --release --locked` outside the checkout, under a memory cap, with
low parallelism (`-j2`). Unit tests: `cargo test --release --locked`.

## Version

0.1: first live candidate (rust-plan, 2026-11-16). Replaces the ASCII decode in
`src/mishe_tauftauf/unit_properties.declared_paths` for the four live callers.
