# mishe-cgroup-counters contract (version 0.1, responsibility: cgroup v2 counter decoding)

Responsibility: decode the counter text in the kernel's cgroup v2 `cpu.stat`
and `cpu.max` files. It owns only that decoding. It reads no file, runs no
command, holds no state and writes nothing but stdout. Python keeps the file
read, the baseline, the verdict and all UNKNOWN routing.

It is the byte-exact counterpart of `cpu_stat_text.parse_cpu_stat`,
`parse_cpu_stat_local` and `parse_cpu_max_period` for the ASCII text the kernel
writes. The cgroup v2 file grammar is ABI-frozen (`name value` lines;
`quota period` for `cpu.max`), so this seam is stable. The justification is
single authority and testability, not CPU: the cgroup readers are not a
profiled hot spot and no speedup is claimed.

## Invocation

```text
mishe-cgroup-counters stat NAME [NAME ...]   (stdin: cpu.stat body)
mishe-cgroup-counters max-period             (stdin: cpu.max body)
```

- `stat` takes one or more counter names; every named counter is required.
  Duplicate names answer once, at the first requested position.
- `max-period` takes no further arguments.
- Any other argument shape is a usage error (exit 1).
- Input bound: 1 MiB. A larger body is a data fault (exit 2), not a truncated
  result. Input must be valid UTF-8.

## Decoding rules

- `stat`: one `name value` pair per line, in first-seen order. Whitespace is
  split on Python's `str.split()` semantics. A named counter whose value is not
  a decimal integer is a data fault; a missing named counter is a data fault.
  Lines for other counters are ignored, even if their values are malformed.
  A repeated named counter takes its last value, matching Python's dict.
- Values are Python-`int` shaped: optional sign, ASCII digits, single
  underscores between digits. Values outside `i64` are a data fault (the
  kernel never writes such a counter).
- `max-period`: the second whitespace token is the period; it must be a
  positive decimal integer. The `max` quota form still has a period and is
  decoded; a non-integer or nonpositive period is a data fault. `cpu.max.burst`
  is a different file and is not part of this contract.

## Output (stdout)

- `stat`: `NAME<TAB>VALUE\n` for each requested counter, in request order.
- `max-period`: `period<TAB>VALUE\n`.
- On any fault, stdout is empty; diagnostics are not written to stdout.

## Exit statuses

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Whole input read and decoded; stdout is the result | Use it |
| 1 | Usage, or stdin read error | UNKNOWN |
| 2 | Data fault: invalid UTF-8, over bound, absent or non-decimal counter | UNKNOWN; never zero |
| 3 | stdout write error | UNKNOWN |
| other | Not part of the contract | UNKNOWN, never GREEN |

## Caller

`mishe_tauftauf.cgroup_counters` (Python adapter). It resolves
`MISHE_CGROUP_COUNTERS_BIN` or `$SITE/bin/mishe-cgroup-counters` and checks the
sha256 in `mishe-cgroup-counters.install.json`. A configured binary that is
missing, mismatched, failing or outside the contract raises
`CgroupCountersUnknown`; `cpu_stat_text` maps that to its existing `None`
answer, so the sense stays UNKNOWN. With no binary configured at all (a test or
an uninstalled checkout) the adapter uses the retained reference decoder in
`cpu_stat_text` explicitly; that path is not used in a delivered plant.

`src/mishe_tauftauf/host_containment.py` keeps an independent `cpu.stat` parser
because those bytes are deployed as the host guard outside this delivery path;
cutting it over needs the guard's separate activation discipline and is out of
scope here.

## Build

Locked: `Cargo.lock`, `rust-toolchain.toml` (1.97.0), no dependencies. Built with
`cargo build --release --locked` outside the checkout, under a memory cap, with
low parallelism (`-j2`). Unit tests: `cargo test --release --locked`.

## Version

0.1: first delivered candidate (rust-plan, 2026-11-16). Replaces the decoding
in `cpu_stat_text.parse_cpu_stat`, `parse_cpu_stat_local` and
`parse_cpu_max_period` for the live `slice_throttle` and `unit_cpu` callers.
