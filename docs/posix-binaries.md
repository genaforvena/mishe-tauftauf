# Mishe POSIX binaries

Status: contract and ranking (rust-plan, 2026-11-14). Step 1 of operator DM
301098. No new binary is built by this document; the first candidate is step 2.

## Why this exists

Mishe's Python runtime is about 49,500 lines. Four Rust crates (about 2,600
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
network.

## Seams that already are binaries

| Binary | Responsibility | Live state (2026-11-14) |
|---|---|---|
| `mishe-tape` | Tape framing: entries, offsets, eligibility facts | Live, read by `feed.py` through `MISHE_TAPE_BIN` / installed component |
| `mishe-records` | Record verification and the verified store | Live since the 2026-11-11 cutover, run by `feed._RecordsChild` |
| `mishe-tape-facts` | One-pass byte-level facts for a disposition reading | Built and installed, differential-tested, **not wired into a live consumer**: its contract says so. Counts as a binary, not as live migration yet |
| `mishe-network-probe` | One network probe row | Live: the `network-probe` duty runs it |

The first three are Mishe binaries by the rules above. `mishe-tape-facts` is
the honest gap: its consumer is not yet running it.

## Constraint kept

Role fold and record verification stay Python until Ilya changes that decision
(his earlier decision, carried into the 2026-11-14 DM). The Rust scope is
byte-level: framing, UTF-8 and performance on byte work. Verification is
Python-side logic and so is not a candidate for rewriting here, although
`mishe-records` already performs the byte-level part of it.

## Ranked next candidates

Ranking criteria: seam stability (the input and output are fixed bytes, not a
moving set of readings), testability (a fixture can be checked differentially
without a live plant), and then the measured Python cost. Costs marked UNKNOWN
have not been measured; the named measurement is the step that would produce
them.

1. **systemctl show decoding** (`src/mishe_tauftauf/unit_psi_text.py`,
   `parse_systemd_show`). Input: `systemctl show` text on stdin. Output: the
   key/value map as JSON, or exit 2 on a malformed line. Stable: the format is
   `Key=value` lines fixed by systemd. Testable: a fixture per property set.
   Measured Python cost: UNKNOWN (expected negligible; it is a small pure parse).
   Its gain is architectural: it is the smallest complete binary and the template
   for the rest. This is the step 2 pick.

2. **Signal edge append and freshness read** (`src/mishe_tauftauf/signal_store.py`,
   `observe`, `_append_edge`, `read`, `expire`; 279 lines). Input: the signal
   home and a `now` value. Output: the signal state and age as JSON. Stable: the
   edge file is append-only JSONL. Testable: fixture homes with known edges and
   times. Measured Python cost: UNKNOWN. Measurement to take: time `read` and
   `expire` over the live signal home, with the same `scancount`-style harness.

3. **Duty runner** (`src/mishe_tauftauf/duties.py`, 44 lines, plus the producer
   launch path). Input: `duties.json` and a duty name. Output: the producer's
   status and its `timeout`-bounded exit. Stable: the file format is fixed.
   Testable: a fixture duties file with a stub producer. Less stable than 1–2
   because it launches other programs and depends on their statuses. Measured
   Python cost: UNKNOWN; the cost is mostly child processes, not Python CPU.

4. **Feed reader, the remaining scan** (`src/mishe_tauftauf/feed.py`, 1,782 lines;
   `discovery.py` readings). Measured: one full scan 11.63 CPU-s, 674 MB RSS
   (scan-20261114T224141Z); `Feed(home).entries(facts=True)` 2.25 CPU-s, 668 MB.
   Most of the byte work already runs through `mishe-tape` and `mishe-records`.
   What remains in Python is the role fold and the readings that consume the
   shared snapshot, so this candidate is large and less stable. The next step
   here is a measurement, not a rewrite: per-reading CPU and whether the snapshot
   can stream without a contract change.

5. **Dossier cut** (`src/mishe_tauftauf/dossier.py`, 191 lines). Input: the
   readings a wake needs. Output: the dossier sections. Least stable: it composes
   many readings and changes with each of them. Testable only as a whole.
   Measured Python cost: UNKNOWN. Last in the ranking until the readings it
   composes are binaries.

## Next steps

- Step 2 (next wake): build candidate 1 as `rust/mishe-systemd-show/` with the
  same layout as `mishe-tape` (locked build, `CONTRACT.md`, tests, a
  `MISHE_SYSTEMD_SHOW_BIN` override), wire `unit_psi_text.py` to it with a named
  `...Unknown` error when the binary is missing, retire the Python parse path
  once the differential test passes, and deliver it through the ordinary path.
- Measure candidates 2 and 3 before choosing them; record the numbers here.
- Keep this ranking updated when a candidate is built or retired.
