# Mishe POSIX binaries

Status: contract and ranking (rust-plan, 2026-11-14). Step 1 of operator DM
301098. No new binary is built by this document; the first candidate is step 2.
Updated 2026-11-15: two candidates have since been built (candidate 1,
`mishe-systemd-show`, and the signal sample store), so the crate count and the
table below are current.

## Why this exists

Mishe's Python runtime is about 49,500 lines. Six Rust crates (about 3,000
lines) already sit beside it, each one a small program with a stdin/argument
and stdout/exit-code boundary. This document says what counts as a Mishe binary,
which existing seams already meet that bar, and which responsibilities come next.
The goal is architecture first: small programs Python composes, each with one
authoritative implementation. CPU gain is a secondary, measured benefit.

## What counts as a Mishe binary

A Mishe binary owns exactly one responsibility and meets all of these:

1. **One input, one output.** It reads bytes from a named file, a `/proc/self/fd/N`
   handle or stdin, and writes its result to stdout. Arguments select the mode;
   they do not carry bulk data.
2. **No hidden state.** It keeps nothing between runs. It reads no config it was
   not told about, writes only the file it was asked to write (for example the
   verified store in `mishe-records`), and never touches runtime pins, units or
   other nodes.
3. **Explicit exit statuses.** Each status has a fixed meaning (table below), and
   every status outside the table is UNKNOWN to the caller, never GREEN.
4. **Byte-exact framing and UTF-8 policy.** Text the program reads is checked as
   UTF-8. Invalid input fails closed with a documented status; the program does not
   repair it. Malformed and unknown input is kept as evidence, not dropped.
5. **Bounded work.** Time and memory are bounded by the input the caller passed,
   or by an explicit limit in the arguments. A helper process that can hang is
   killed and reaped (see `mishe-network-probe`).
6. **A contract file.** `rust/NAME/CONTRACT.md` states the invocation, output
   format, exit statuses, fault texts and version. A differential test checks the
   binary against the Python reader it replaces. The contract, not the Rust source,
   is what a caller depends on.
7. **Python-side reader with an explicit override.** The caller finds the binary
   through a `MISHE_<NAME>_BIN` environment variable (tests, scratch builds), else
   through the installed component. If neither exists the caller raises a
   named `...Unknown` error. There is no silent default to Python.
8. **Locked build.** `Cargo.lock` and `rust-toolchain.toml` are committed. The
   build runs outside a wake, outside the checkout, with a memory cap and low
   parallelism (see the host-health rule in the rust-plan charter).

Not every responsibility should become a binary. Keep Python where it is
simpler, and keep the role fold and record verification in Python until the
owner changes that decision (see the constraint below).

## Exit statuses (the house convention)

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | The requested range or probe completed | Output is the result |
| 2 | A data-level fault (framing, protocol, verification) | Use what the contract says is valid before the fault; treat the input as malformed there, do not guess |
| 1 | Usage, open, read or write error | Not a result: UNKNOWN |
| 3 | The program could not run its work (helper missing, timeout) | UNKNOWN, never a quiet pass |
| other | Not part of the contract | UNKNOWN, never GREEN |

`mishe-tape` and `mishe-records` use 0/1/2. `mishe-network-probe` adds 3 for
"could not run", which is why an unreachable helper is not mistaken for a quiet
network. The two later binaries keep the same convention with their own
meanings: `mishe-systemd-show` uses 0/1/2 (2 is a data fault), and
`mishe-signal-sample` uses 0/1/2 with 2 for a malformed or torn record — on
`read` the valid records are still emitted and the caller marks the window
UNKNOWN for the faulted segments.

## Seams that already are binaries

| Binary | Responsibility | Live state (2026-11-15) |
|---|---|---|
| `mishe-tape` | Tape framing: entries, offsets, eligibility facts | Live, read by `feed.py` through `MISHE_TAPE_BIN` / installed component |
| `mishe-records` | Record verification and the verified store | Live since the 2026-11-11 cutover, run by `feed._RecordsChild` |
| `mishe-tape-facts` | One-pass byte-level facts for a disposition reading | Built and installed, differential-tested, **not wired into a live consumer**: its contract says so. Counts as a binary, not as live migration yet |
| `mishe-network-probe` | One network probe row | Live: the `network-probe` duty runs it |
| `mishe-systemd-show` | `systemctl show` text decoding | Live since candidate 1: `unit_psi_text.parse_systemd_show` runs it and `discovery.py` reads the result through `read_unit_psi`. The retired Python decoder is deleted; its recorded answers are GOLDEN in `tests/test_rust_systemd_show.py` |
| `mishe-signal-sample` | The append-only signal sample store | Live on the write side: the `memory-samples` duty appends two rows every three minutes into `SITE/samples/memory/`. `read_samples` has no production caller yet, so nothing reads the store back |

`mishe-tape-facts` is the honest gap: its consumer is not yet running it. The
signal sample store is the narrower one: it is written live and not yet read.

## Whole-system target

The owner's bound from 2026-11-14 (role fold and record verification stay Python)
is lifted. The target is the whole runtime as small POSIX programs composed by
arguments, files, streams and exit statuses, with exactly one authoritative
implementation per responsibility and no shadow copy kept behind a flag. Python
remains where it is simpler, and it composes the binaries. Where a responsibility
moves to Rust, the Python path is deleted once the binary is the only path (the
systemctl-show decoder is the first case: its Python copy survives only as recorded
golden answers in `tests/test_rust_systemd_show.py`).

Role fold and record verification are ordinary candidates again. They are ranked
below by seam stability, not excluded. Their byte-level parts (framing, UTF-8,
hashing) are the first pieces to cut; the fold's policy stays a Python call on
those outputs until a contract for it exists.

## Ranked next candidates

Ranking criteria: seam stability (the input and output are fixed bytes, not a
moving set of readings), testability (a fixture can be checked differentially
without a live plant), and then the measured Python cost. Costs marked UNKNOWN
have not been measured; the named measurement is the step that would produce
them. Updated 2026-11-15 after the operator DM 301336.

1. **systemctl show decoding.** Built and live as `rust/mishe-systemd-show/`
   (2026-11-14). The Python decoder is deleted from `src/`; its answers are kept
   as GOLDEN in the test. Done.

2. **Signal edge append and freshness read** (`src/mishe_tauftauf/signal_store.py`,
   `read`, `observe`, `expire`; 279 lines). Measured 2026-11-15 on a copy of the
   live home: 111 signals, 20 sweeps of `read` = 0.46 CPU-s, about 0.023 CPU-s
   per full sweep (script `.mishe-tauftauf/artifacts/rust-plan-20261107/signal-read-measure.py`).
   The CPU gain would not pay, so no binary is built. It stays a candidate for
   architecture only and is re-opened if a sweep exceeds 1 CPU-s.

3. **Duty runner** (`src/mishe_tauftauf/duties.py`, 44 lines, plus the producer
   launch path). Input: `duties.json` and a duty name. Output: the producer's
   status and its `timeout`-bounded exit. Stable: the file format is fixed.
   Testable: a fixture duties file with a stub producer. Its cost is mostly the
   child processes, so the next measurement is wall and child CPU per duty, not
   Python CPU. Next to take.

4. **Scheduler arm and feed remaining scan** (`src/mishe_tauftauf/feed.py`,
   1,782 lines; `discovery.py` readings). Measured: one full scan 11.63 CPU-s,
   674 MB RSS (scan-20261114T224141Z). Most byte work already runs through
   `mishe-tape` and `mishe-records`; what remains is the role fold and the
   readings that consume the shared snapshot. The next step is per-reading CPU,
   not a rewrite.

5. **Role fold and record verification** (`src/mishe_tauftauf/feed.py` fold;
   verification in Python). Ordinary candidate under the whole-system target. Cut
   its byte part first (framing, UTF-8, hashing into `mishe-records`); keep the
   policy call in Python until a contract exists.

6. **Dossier cut** (`src/mishe_tauftauf/dossier.py`, 191 lines). Input: the
   readings a wake needs. Output: the dossier sections. Least stable: it composes
   many readings and changes with each of them. Testable only as a whole. Last in
   the ranking until the readings it composes are binaries.

## Next steps

- Candidate 1 is done: `rust/mishe-systemd-show/` was built with the same
  layout as `mishe-tape` (locked build, `CONTRACT.md`, tests, a
  `MISHE_SYSTEMD_SHOW_BIN` override), `unit_psi_text.py` was wired to it with a
  named `SystemdShowUnknown` error when the binary is missing, the Python parse
  path was deleted (2026-11-15 its answers became GOLDEN in the test), and it was delivered through the ordinary
  path on 2026-11-14.
- Candidate 2 is measured and does not pay on CPU (see above). Measure candidate 3
  next and record the numbers here before building anything.
- Keep this ranking updated when a candidate is built or retired.
