# mishe-systemd-show contract (version 0.1, responsibility: systemctl show decoding)

Responsibility: turn `systemctl show` text (`KEY=VALUE` lines) into the dict the
unit-PSI reader consumes. It owns only that decoding. It runs no systemctl, reads
no file, holds no state, and writes nothing but stdout.

It is the byte-exact counterpart of the former Python `parse_systemd_show`. The
Python reference is kept only in `tests/test_rust_systemd_show.py` as the
differential oracle; the Python decoder itself is retired.

## Invocation

```text
mishe-systemd-show            (stdin: systemctl show output)
```

- No arguments. Any argument is a usage error (exit 1).
- Input bound: 1 MiB. Larger input is a data fault (exit 2), not a truncated result.

## Decoding rules

- Lines are split at the Python `str.splitlines()` boundaries: `\n`, `\r`, `\r\n`,
  `\v`, `\f`, `\x1c`, `\x1d`, `\x1e`, `\x85`, ` `, ` `.
- A line without `=` is ignored. The key is the text before the first `=`; the value
  is everything after it (it may contain `=`). An empty key is ignored.
- A repeated key keeps its first position and takes its last value.
- Blank lines are ignored.

## Output (stdout)

One line per distinct key, in first-seen order: `KEY=VALUE\n`. Keys and values
cannot contain a line-break character by construction, so the stream is lossless
and a caller splits each line once, at the first `=`.

## Exit statuses

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Decoded; stdout is the result (empty output is a valid empty dict) | Use the dict |
| 1 | Usage, read or write error | UNKNOWN |
| 2 | Data fault: input is not valid UTF-8, or over the bound | UNKNOWN; never guess |
| other | Not part of the contract | UNKNOWN |

Invalid UTF-8 is not repaired; the byte offset of the first bad byte goes to stderr.

## Caller

`unit_psi_text.parse_systemd_show` (Python adapter). It finds the binary through
`MISHE_SYSTEMD_SHOW_BIN` (override) or `$SITE/bin/mishe-systemd-show`, checked
against `mishe-systemd-show.install.json`. A missing, mismatched or failing binary
raises `SystemdShowUnknown`. The unit reader catches that and reports the sampler
as unreadable (UNKNOWN). It never falls back to Python.

## Build

Locked: `Cargo.lock`, `rust-toolchain.toml` (1.97.0), no dependencies. Built with
`cargo build --release --locked` outside the checkout, under a memory cap, with
`-j1`. Unit tests: `cargo test --release --locked`.

## Version

0.1: first live candidate (rust-plan, 2026-11-14). Replaces the Python decoder in
`src/mishe_tauftauf/unit_psi_text.py`.
