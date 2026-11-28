# Mishe POSIX binaries

## Why this exists

Mishe's Python runtime (`src/`) is about 56,400 lines. Eight Rust crates (about
3,500 lines, tests included) already sit beside it, each one a small program with a stdin/argument
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
network. `mishe-systemd-show` uses 0/1/2 (2 is a data fault).
`mishe-signal-sample` uses 0/1/2 with 2 for a malformed or torn record — on
`read` the valid records are still emitted and the caller marks the window
UNKNOWN for the faulted segments.

## Seams that already are binaries

| Binary | Responsibility | Consumer |
|---|---|---|
| `mishe-tape` | Tape framing: entries, offsets, eligibility facts | Live, read by `feed.py` through `MISHE_TAPE_BIN` / installed component |
| `mishe-records` | Record verification and the verified store | Live since the 2026-11-11 cutover, run by `feed._RecordsChild` |
| `mishe-tape-facts` | One-pass byte-level facts for a disposition reading | Built and installed, differential-tested, **not wired into a live consumer**: its contract says so. Counts as a binary, not as live migration yet |
| `mishe-network-probe` | One network probe row | Live: the `network-probe` duty runs it |
| `mishe-systemd-show` | `systemctl show` text decoding | Live since candidate 1: `unit_psi_text.parse_systemd_show` runs it and `discovery.py` reads the result through `read_unit_psi`. The retired Python decoder is deleted; its recorded answers are GOLDEN in `tests/test_rust_systemd_show.py` |
| `mishe-signal-sample` | The append-only signal sample store | Live on both sides: the `memory-samples` duty appends up to three rows every three minutes into `SITE/samples/memory/`, and the `footprint` duty reads the last 15 minutes of that store back |
| `mishe-unit-paths` | Absolute unit paths declared by `systemctl show` text | Live: `unit_paths.py` composes it and fails closed with `UnitPathsUnknown` when the binary is missing, mismatched or failing; the binary handles ASCII input and emits `DEFER` for non-ASCII text, which the Python decoder handles. Its recorded answers are GOLDEN in `tests/test_rust_unit_paths.py` |
| `mishe-cgroup-counters` | cgroup v2 (the kernel's per-group CPU and memory accounting) `cpu.stat` / `cpu.max` counter decoding | Live: `cpu_stat_text` delegates to it through `cgroup_counters.py` (env override / sha256-pinned site install); the live `slice_throttle` and `unit_cpu` senses use those answers. A configured but broken binary maps to UNKNOWN. The retained Python decoder answers only when no binary is configured (isolated tests); its recorded answers are GOLDEN in `tests/test_rust_cgroup_counters.py` |

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
   `read`, `observe`, `expire`). Measured 2026-11-15 on a copy of the
   live home: 111 signals, 20 sweeps of `read` = 0.46 CPU-s, about 0.023 CPU-s
   per full sweep (method: 20 sweeps of `signal_store.read` over every registered
   signal, timed with `resource.getrusage` CPU deltas; the 16-line script is
   site-local and not shipped, so this line names no path).
   The append stays in Python. Expiry and display share the Python age
   comparison. The two-sweep fixture produced identical signal edges and timer
   receipts with zero freshness subprocesses. The median fell from 0.256 to
   0.051 CPU-s, and the former verifier could delay re-arming by 20 seconds in
   the controlled split stall. See [the comparison](rust-runtime-migration-plan.md#1071-in-process-freshness-removes-the-verifier-delay-2026-11-20).

3. **Duty runner** (`src/mishe_tauftauf/duties.py`, 49 lines, plus the producer
   launch path). Input: `duties.json` and a duty name. Output: the producer's
   status and its `timeout`-bounded exit. Stable: the file format is fixed.
   Testable: a fixture duties file with a stub producer. Measured 2026-11-15: the
   Python part only expands the manifest at timer generation (`runtime_cli.py:468`, the sync path,
   `plant_boot.py:103`), not per run. 60 duties expand in 4.5 ms, so it does not
   pay as a binary and is not a candidate. Duties run as systemd units, not through
   this module.

4. **Scheduler arm and feed remaining scan** (`src/mishe_tauftauf/feed.py`,
   1,936 lines; `discovery.py` readings). Measured: one full scan 11.63 CPU-s,
   674 MB RSS (scan-20261114T224141Z). Most byte work already runs through
   `mishe-tape` and `mishe-records`; what remains is the role fold and the
   readings that consume the shared snapshot. Measured 2026-11-15 on the live feed
   (301,336 entries): one `entries(facts=True)` read is 2.1-2.2 CPU-s. A profile
   puts the cost in the per-entry Python loop of `_iter_streamed_feed` (about 300k
   iterations of two reads and a readline) and the records-child `write` (three
   pipe writes per entry). Coalescing the three writes into one was A/B-tested and
   refuted: 2.29 vs 2.15 CPU-s median over three alternating runs, so no change.
   Framer-loop probe (2026-11-15, two alternating runs each, live tape): the
   framer alone takes 0.88-0.91 CPU-s; the full streamed iteration takes
   0.89-0.90 CPU-s in the parent plus that framer cost. Only the Python share above
   pure draining of the pipe (about 0.75 CPU-s) is the loop, about 36% of the
   2.1 CPU-s `entries(facts=True)` read. That is below the 50% trigger, so candidate
   4 is parked: no Rust framer that returns parsed records. Artifacts:
   `framer-share-probe.py` (site-local).

5. **Role fold and record verification** (`src/mishe_tauftauf/feed.py` fold;
   verification in Python). Ordinary candidate under the whole-system target. Cut
   its byte part first (framing, UTF-8, hashing into `mishe-records`); keep the
   policy call in Python until a contract exists.

6. **Dossier cut** (`src/mishe_tauftauf/dossier.py`). Input: the
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
- Candidate 2 stays in Python. Candidate 3 costs 4.5 ms at generation and
  stays in Python.
- Candidate 4 (feed remaining scan) is parked: the framer-loop share measured
  about 36%, below the 50% trigger. Reopen it only if a live read crosses that line.
- Keep this ranking updated when a candidate is built or retired.
