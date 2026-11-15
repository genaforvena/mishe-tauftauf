# mishe-signal-sample contract (version 0.1.0, responsibility: signal sample store)

Responsibility: keep bounded, append-only time-series samples of Mishe signals
and read them back in one pass. It owns the segment format, the append rule and
the read rule. It runs no producer, reads no config, holds no state between runs,
and writes only inside the sample directory it was given. It never fills a gap:
no record means no sample, and no value is ever invented for a missing time.

The directory is `SITE/signals/samples/` by convention; the caller passes it.

## Record format

One record per line, five tab-separated fields, terminated by `\n`:

```text
ts_ms \t name \t state \t value \t source \n
```

| Field | Rule |
|---|---|
| `ts_ms` | Unix time in milliseconds. Digits only, 1 to 15 digits, not zero. |
| `name` | 1 to 96 bytes of `[A-Za-z0-9._:-]`. |
| `state` | One of `ok`, `warn`, `red`, `unknown`. The producer's verdict; `unknown` means the sample could not be taken. |
| `value` | `-` for no numeric value, else a finite decimal number (digits, `+ - . e E`, at most 32 bytes). `NaN` and `inf` are rejected. |
| `source` | 1 to 128 bytes of `[A-Za-z0-9._:/-]`: the producer or file the number came from. |

A line is at most 512 bytes. Fields are validated, never repaired. A line with
any other field count, a carriage return, or any other character is malformed.

## Segments

- Files `DIR/<15 digits>.tsv`. The digits are the first `ts_ms` of the segment,
  zero-padded, so lexical order is time order of creation.
- A segment grows by appends until the next batch would exceed `MAX_SEGMENT_BYTES`;
  then a new segment is created with `create_new` (never merged into an old name).
- Retention: after a new segment is created, the oldest segments beyond
  `KEEP_SEGMENTS` are removed. Retention is bounded by count and by segment size.
- Any file in `DIR` that is not a segment name is a data fault for both subcommands.

## Subcommands

```text
mishe-signal-sample append DIR MAX_SEGMENT_BYTES KEEP_SEGMENTS   (stdin: TSV batch)
mishe-signal-sample read   DIR [--name NAME] [--since MS] [--until MS]   (stdout: TSV)
```

- `MAX_SEGMENT_BYTES` is 4096 to 67108864. `KEEP_SEGMENTS` is at least 1.
- `append` input is at most 1 MiB and must be complete: every line validated and
  the last byte a newline. The batch is written with one append, so it is
  all-or-nothing as far as validation goes: a bad batch writes nothing.
- `read` emits each valid, matching record exactly as written (same bytes, one
  line per record), in segment order then file order. It does not sort. Records
  outside `--since`/`--until` (inclusive, ms) or not named `NAME` are skipped.
  Callers that need time order sort the stream themselves.

## Exit statuses

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | Done. For `read`, stdout is the complete result. An empty directory is a valid empty result (no samples, not zeros). | Use the output |
| 1 | Usage, directory or file open/read/write error | UNKNOWN; stdout is not a result |
| 2 | Data fault: malformed or torn line, bad UTF-8, stray file, over-bound batch | `append`: nothing was written, input rejected. `read`: the valid records were still emitted, but the caller marks the window UNKNOWN for the faulted segments |
| other | Not part of the contract | UNKNOWN |

Diagnostics go to stderr as `label:line: reason`, where `label` is the segment
file name. Malformed input is kept on disk as evidence; the reader never deletes
or rewrites it.

## Torn writes

A crash mid-append can leave a last line without `\n`. `read` reports it as a
`torn tail` fault and still emits the earlier valid records. Nothing repairs it
automatically; a later append starts after the torn bytes, so a torn segment
stays on disk and keeps reporting as a fault until retention removes it.

## Version and build

Version 0.1.0. `Cargo.lock` and `rust-toolchain.toml` are committed. Std only, no
dependencies. Build outside the checkout with a memory cap, for example
`cargo build --release --target-dir $ARTIFACTS/target-signal -j 1`.

## Callers

Python producers (planned, next slice): a writer helper that runs the binary
once per producer batch at a bounded interval, with the binary found through
`MISHE_SIGNAL_SAMPLE_BIN` (tests) or the installed site copy. If neither exists
the caller raises a named `SignalSampleUnknown`; there is no Python fallback.
Analysis (health's lagged cross-correlation, periodicity, change points) reads
through `read`.

## Differential check

Not yet done. The next slice adds a Python reader oracle in `tests/` and checks
both on the same generated corpus, including malformed and torn cases.
