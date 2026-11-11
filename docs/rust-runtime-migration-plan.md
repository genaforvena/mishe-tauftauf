# Rust runtime migration plan

Status: an executed plan, not a proposal. Written from `docs/rust-runtime-investigation-plan.md` (949feffe, with the owner clarification at 3555dec8) and now carrying the executed slices in sections 9 and 10. Slice A (Rust tape framing) and the S2.1/S2.2 Python slices are delivered and observed on the live plant; each cutover's evidence, rollback and reapply checks are in its own section. Raw samples live under `.mishe-tauftauf/artifacts/rust-plan-20261107/` (gitignored).

## Summary

- The owner's direction is a small POSIX-style Mishe with worthwhile responsibilities really migrated to Rust, each cutover naming its authoritative consumer and the predecessor it retires. This plan takes that direction seriously and tests it against the measurements.
- The measured cost is the tape read/verify path, and about two thirds of it is per-record Python overhead inside verification, not the fold (section 2.3). That makes the tape read/verify path the right first responsibility for a Rust core slice. Rust is justified there because the overhead is interpreter cost per record.
- Single-authority rule: the verifier is shared by `Feed.entries()` (44 call sites) and `Feed.role_entries()` (4 call sites). A slice that moves only the role path would leave two verifiers live, which the brief forbids. So the first slice must own tape verification for every reader, and that is larger than a 24-hour task.
- **Go/no-go: NO-GO for delivering the Rust slice in 24 hours.** Section 6 gives the flip conditions and the measurements that would change this. The 24-hour path is still written out, so it can be run as soon as those conditions hold.
- Python-only fixes remain worth doing first on their own. They are language-independent, cheap, and reduce the baseline the Rust slice must beat (section 3.4).

## 0. Provenance of this snapshot

| Item | Value | Source |
|---|---|---|
| Checkout HEAD | `949feffe` on main; upstream `3555dec8` adds the owner clarification (read with `git show`, not merged) | `git log`, `git show 3555dec8:docs/rust-runtime-investigation-plan.md` |
| Untracked, not ours | `checks/work_balance.py`, `tests/test_work_balance.py` (left alone) | `git status --short` |
| Live pin | `runtime/current` -> `releases/4a4b94ce09b5cedc76b2a33bf0009f043c2edba7` | symlink |
| Pin vs HEAD | Pin is an ancestor of HEAD, 5 commits behind. `producers.py`, `duties.py`, `runtime_cli.py` differ. The pinned producer lacks `unknown_codes` (added at `7eec5ed0`). | `diff` against the pin; `systemctl --user show mishe-producer-observer-checks.service` shows no `--unknown-codes` |
| Rust toolchain | cargo 1.97.0, rustc 1.97.0 under `/home/mesh-home/.cargo/bin`. Not pinned: no `rust-toolchain.toml` or `.rust-toolchain` in the repo. rustup 1.29.0 also present. | `cargo --version`, `rustc --version`, `ls rust-toolchain*` |
| Crate cache | `~/.cargo/registry` (2.2 GB) holds cached tarballs including `sha2-0.10.9`, `sha2-0.11.0`, `rusqlite-0.31.0`, `libsqlite3-sys-0.28.0`, `serde_json-1.0.150/151`, `hex-0.4.3`. Cache presence is not proof of a complete offline graph; no `Cargo.lock` exists. | `ls ~/.cargo/registry/cache/*/` |

Coordination note: `bin/mishe wall write --owner rust-plan` refuses the role because no charter declares it. The wall is written directly to `.mishe-tauftauf/walls/rust-plan.md`, as the supervising session directed. No charter was created.

## 1. Source-and-consumer map

### 1.1 Consumers and deployment (pinned snapshot)

Common to all producer units: `Type=oneshot`, `KillMode=control-group`, `MemoryHigh=1 GiB`, `MemoryMax=2 GiB`, `TasksMax=256`, `CPUQuota=infinity`, `MISHE_WORKLOAD_SLICE=mishe-plants.slice`, `PYTHONPATH=…/runtime/current/src`. Source: `systemctl --user show <unit>`.

| Unit | ExecStart (abridged) | Timer | TimeoutStart / Stop | Duty (`duties.json`) |
|---|---|---|---|---|
| `mishe-producer-dispatch` | `python3 -m mishe_tauftauf produce dispatch` | 60 s | 1 min / 1 min 30 s | `duties.json:18-23` (every 60, timeout 30, max_age 300) |
| `mishe-producer-readings` | `signal run readings --timeout 300 -- … discover scan` | 600 s | 5 min 30 s / 1 min 30 s | `duties.json:197-212` (every 600, timeout 300, max_age 900) |
| `mishe-producer-observer-checks` | `signal run observer-checks --timeout 900 -- /usr/bin/python3 …/checks-runner/observer-checks.py --once` | 900 s | 15 min 30 s / 1 min 30 s | `duties.json:93-108` (every 900, timeout 900, max_age 2700) |
| `mishe-producer-host-capacity` | `signal run host-capacity -- producers/host-capacity.sh` | 120 s | 1 min 30 s / 1 min 30 s | `duties.json:168-173` (every 120, timeout 60) |
| `mishe-producer-budget` | `signal run budget` → `producers/budget.sh` | 600 s | — | `duties.json:154-159` (every 600, timeout 300) |
| `mishe-doctor` | `doctor --unit-dir … --prefix mishe --repo … --pythonpath …` | 300 s | 2 min / 1 min 30 s | not a duty |
| `mishe-wake@<role>` (genome, senses, steward, pub, operator, docs, health, field-review) | `wake <role> --repo … --timeout 1800` | path-triggered (`mishe-wake-<role>.path`) | 1 h 35 min / 20 s | `mishe-wake@.service` |

Exit codes for producers: 0 GREEN, 2 RED, anything else an unobserved run that ages to UNKNOWN (`producers.py:26-43`).

### 1.2 Source call sites (pinned snapshot, `runtime/current/src/mishe_tauftauf/`)

| Responsibility | Location | Notes |
|---|---|---|
| Producer runner | `producers.py:26-43` (`run`) | Bounded subprocess, detail file, observe. |
| Dispatch producer | `producers.py:46-57` (`dispatch`) | Reads dispatch records only. |
| Producer table | `producers.py:98` (`produce`), `:105` (`load`) | |
| Signal observation | `runtime_cli.py:129` (parser), `:153` (`run`), `signal_store.py` (`observe`) | |
| Tape framing (header/body validation) | `feed.py:259` (`iter_feed_entries`), `:283`, `:303`, `:310` (malformed raises `FeedError`) | Raises at the first fault. |
| Streamed tape reader | `feed.py:149` (`_iter_streamed_feed`), `:352` (`iter_feed_file`) | Bounded memory. |
| Whole-tape reader | `feed.py:610-614` (`read_bytes`), `:973` (`Feed.entries`) | Reads and parses the whole tape. |
| Record envelope verification | `feed.py:742` (`_verify_records`), `:908` (verified generator in `role_entries`) | Calls `records.py`. |
| Verified-payload store | `records.py:229` (`VerifiedStore`), `:268` (`lookup`), `:314` (`verified_store`), `:323` (`cached_payload`); envelope sha256 at `records.py:208` | sqlite file `records.verified.sqlite3` (544 MB). |
| Role projection | `feed.py:444` (`_role_fold`), `feed.py:53` (`_ROLE_ENTRIES_CACHE`, process-local), `feed.py:862-930` (`role_entries`) | Cache is in process; cold CLI processes always rescan. |
| Checkpoint index | `feed.py:29` (`INDEX_STRIDE = 128`), `:653` (`_read_checkpoint`), `:717` (`_publish_checkpoint`) | Used to validate cache, not to skip the scan. |
| Identity index | `feed.py:527-576` (`_malformed_receipt`, `meta.malformed` count), `:1128` | sqlite `feed.identities.sqlite3` (23.5 MB). |
| Wall context | `wall.py:865-886` (`context`), `:880`, `:965` | Calls `role_entries`. |
| Wake dossier | `wake.py:52` (`role_entries`) | One process per wake. |
| Migration | `migrate.py:29` (`role_entries`) | |
| Discovery scan | `discovery.py:7217` (`scan`), `discovery.py:4602` (`iter_feed_file`, DM-disposition sense) | Module is 7,632 lines. |
| CLI | `cli.py:173-176` (`cmd_wall show`), `:277-281` (`cmd_feed`, whole tape), `:143-147` (`_require_site_role`) | |

### 1.3 Tape scale and fan-out

- Tape: `chat.log`, 209,420,968 bytes at 23:34Z (`feed.py:517` selects it). 298,161 entries (profile count of `_verify_records` calls).
- `Feed.entries()` callers: 44 across 13 modules. `role_entries()` callers: 4 (`wall.py:880`, `:965`, `wake.py:52`, `migrate.py:29`).
- `observer-checks` does not read the tape directly (no `chat`, `Feed`, `entries` reference in its site-local program, which the public tree excludes). It does run `cli append` (line 314), which writes to the tape, and runs configured check programs (line 256). Whether those child programs read the tape is UNKNOWN; the list of them was not traced.

### 1.4 Constraints, history and incidents

- Containment: producer units carry `ConditionPathExists=!/var/lib/mishe-containment/tripped`. In 24 h, `readings` was skipped 2×, `observer-checks` 2×, `doctor` 5×, `household-activity` 3×, `host-capacity` 12×, `dispatch` 24×. At 23:46Z the tripped file is absent.
- 22:48Z: `mishe-producer-readings.service` was SIGKILLed (`code=killed, status=9/KILL`), 150.3 MiB peak, 28.8 s CPU (`journalctl --user -u mishe-producer-readings.service`).
- **OOM 2026-11-03, verified in the journal (user journal, `journalctl --user --since "2026-11-03 13:00" --until "2026-11-03 15:00"`):** `tmux-spawn-…` scopes were OOM-killed at 13:09:53Z, 13:53:03Z and 14:08:01Z; `mishe-host-guard.service` was stopped at 13:09:54Z and 13:53:05Z; then `-.slice`, `mishe.slice` and `mishe-lifeline.slice` each report "killed by the OOM killer" at 13:57:02Z.
- The comment at `feed.py:901-905` says "2026-11-03 13:53Z" and cites "oom_kill 47->48" for supervisors on the 3 GiB lifeline slice. The journal's lifeline kill is at 13:57:02Z, and the 13:53:03Z kill is a tmux scope. The comment's time is approximately right and does not match the lifeline kill exactly.
- Causal link from the tape read path to that OOM: UNKNOWN. The journal does not name the killed process, and no kernel log for that window survives (`journalctl -k` shows nothing for 11-03; boot history starts 11-05).

## 2. Baseline

### 2.1 Method and guard

- Snapshot: `runtime/current/src` (4a4b94ce), interpreter `.venv/bin/python` (Python 3.12.3), environment as the units set it.
- Guard for every measurement: skip and log if 1-min loadavg ≥ 4 or MemAvailable < 4 GiB. Nothing was skipped. Load was 0.78–1.0 and MemAvailable about 28 GB throughout.
- Timing: `/usr/bin/time`, three repetitions per command; samples kept in `artifacts/rust-plan-20261107/baseline/`.
- Side effects checked: `--help`, `pain list` (reads `top-pains/`), `delivery show` (reads `deliveries/*.json`, `delivery.py:603`), `publication status` (reads), `wall show` (reads). Excluded: `discover scan` (writes), `doctor` (writes), `feed` (whole-tape parse, see 2.4), `task coordination-report` (whole-tape parse), `wall write` (writes).
- Warm vs first run: the tape and modules are in page cache (buff/cache ≈ 28 GB). Every repetition is effectively warm. There is no cold-cache sample, and cache dropping is forbidden.

### 2.2 Numbers

| Command (pinned snapshot) | Reps | Elapsed (s) | User (s) | Sys (s) | Max RSS (MB) |
|---|---|---|---|---|---|
| `python -I -c pass` (interpreter floor) | 3 | 0.00 | 0.00 | 0.00 | 9.3 |
| `-m mishe_tauftauf --help` | 3 | 0.08–0.09 | 0.06–0.08 | 0.01 | 25.7 |
| `… pain list` | 3 | 0.08–0.09 | 0.06–0.07 | 0.01 | 25.5–25.7 |
| `… delivery show` | 3 | 0.09 | 0.07–0.08 | 0.01–0.02 | 26.2–26.4 |
| `… publication status` | 3 | 0.09 | 0.07 | 0.02 | 27.0–27.1 |
| `… wall show --owner genome` | 3 | 17.68–18.04 | 15.21–15.50 | 2.33–2.53 | 69.5–69.8 |

Import: `python -X importtime -c "import mishe_tauftauf.cli"` gives 53.9 ms cumulative (`baseline/import_cli.importtime.txt`).

### 2.3 Where the 18 s goes (one cProfile run of `wall show --owner genome`)

One run, same guard, pinned snapshot, no writes (`baseline/profile_wall_show.prof`, `profile_wall_show.top.txt`). Under cProfile the run took 35.3 s wall and 32.6 s user, so per-function times are inflated (about 2×). Use the proportions, not the absolute seconds, and do not add the fractions below; they overlap.

| Component (cumulative in profile) | Share of profiled 35.1 s | Frames |
|---|---|---|
| `_role_fold` (role projection itself) | ~2% (self 0.6 s) | `feed.py:444` |
| Streamed tape parse (`_iter_streamed_feed` → `iter_feed_entries`, `_iter_text_lines`) | ~21% (7.45 s) | `feed.py:149`, `:259`, `:398` |
| `_verify_records` (per-record verification) | ~66% (23.2 s) | `feed.py:742` |
| — of which `cached_payload` → `records.lookup` → sqlite `execute` | ~17% (5.9 s; sqlite execute 2.1 s, lookup 4.6 s) | `records.py:323`, `:268` |
| — of which `check_pass_directory` / `_directory` (reopen directory fd per record) | ~13% (4.7 s; `_directory` 3.7 s) | `records.py:96`, `:91` |
| — of which `pathlib.__eq__` (`Path(entry.home) == home` per record) | ~9% (3.0 s; 196k calls) | `pathlib.py:519` |
| `verified_store` per record | ~7% (2.3 s) | `records.py:314` |

Interpretation:
- Most of the cost is per-record Python work in verification: directory and path bookkeeping (about 13% + 9%, with some overlap), sqlite lookups (about 13–17%), and the per-record loop. The fold is negligible.
- Parse is about a fifth. A verifier that is faster but still does per-record path and sqlite work would recover only part of the 66%.
- Measured floor: parse + fold + verification at per-record cost. Only the verification share is a candidate for a Rust boundary. Parse is also a candidate, but it is the subject of the 24-hour go/no-go, not of a separate claim.
- This is a demonstrated attribution from one profile run. It is not a speedup claim for any Rust code.

### 2.4 Service journals (24 h, read before any measurement)

Source: `journalctl --user -u <unit> --since "-24h" -o cat`. "Consumed" lines are systemd CPU accounting; "memory peak" appears on some of them.

| Unit | Finished | CPU per run | Memory peak (sampled) | Failures |
|---|---|---|---|---|
| `mishe-producer-readings` | 141 | n=140, mean 42.1 s, sum 5,891 s | max 770 MiB (4 samples) | 2 failed starts; 2 containment skips |
| `mishe-producer-observer-checks` | 93 | n=94, mean 27.0 s, sum 2,542 s | 82 MiB (1 sample) | 1 failed start; 2 skips |
| `mishe-doctor` | 256 | n=283, mean 10.1 s, sum 2,861 s | max 591 MiB (4 samples) | **27 failed starts; 25 exit status 1**; 5 skips |
| `mishe-producer-budget` | 140 | n=140, mean 7.5 s, sum 1,049 s | max 529 MiB (2 samples) | 0 |
| `mishe-producer-host-capacity` | 700 | not logged | — | 1 failed; 12 skips |
| `mishe-producer-dispatch` | 1,390 | not logged | — | 0; 24 skips |
| `mishe-producer-household-activity` | 87 | not logged | — | 4 failed; 3 skips |

Wake units (finished in 24 h; CPU sums from Consumed lines, including model-driver CLI work): genome 76 (1,202 s), senses 125 (2,342 s), steward 43 (1,155 s), operator 26 (551 s), health 61 (1,139 s), docs 5 (104 s), field-review 2 (60 s), pub 1 (0 s).

Reading:
- Largest producer aggregate is `readings` (about 5.9k CPU-s/day), then `doctor` (about 2.9k, including failures), then `observer-checks` (about 2.5k).
- `readings` peaks at up to 770 MiB against a 1 GiB `MemoryHigh`, so it is the producer closest to containment.
- Doctor's 25 exit-1 runs are a current failure. The journal shows 25 RED lines alongside 281 "PASS local plant out of Git" lines. The cause of the RED lines is UNKNOWN.
- Per-run CPU for dispatch and host-capacity is not logged (systemd logs only above its floor).

### 2.5 Existing tests for the read path

`tests/test_feed.py` covers the role-view cache directly (9 tests in the role-view and cache group, lines 90–225: streaming instead of whole reads, cache extension from the canonical tail, verification of discarded records, refusal of corruption, rejection of a bad appended record, rewrite and corrupt index). It also covers corruption refusal (line 314), corrupt index failing closed (355), checkpoint rebuild (403), identity index crash rebuild (436) and a reader failing closed mid-parse (679). `tests/test_records_verified_store.py` covers the verified store. Coverage of the role-view cache across processes is absent: no test covers a cold process reusing state, because the cache is process-local.

### 2.6 Not measured (UNKNOWN, with safe trigger)

- `mishe feed` (`cli.py:277-281`, whole-tape parse and print): not run. Trigger: loadavg < 2, MemAvailable > 8 GB, no containment trip for 24 h, and a preserved tape copy rather than live `chat.log`.
- Scaling of parse and verify against tape size: UNKNOWN. Needs a preserved-copy series (25/50/100/200 MB prefixes, one repetition each) under the same trigger.
- Cold-page-cache `wall show`: UNKNOWN. Cache dropping is forbidden.
- Number of cold tape scans per wake: UNKNOWN. Hypothesis: each wake process pays one cold scan through `wake.py:52`. Settle it by reading one wake's journal or tracing `openat` on a copy.
- `discover scan` profile (tape iteration at `discovery.py:4602` versus scanner logic): UNKNOWN. Writes a snapshot, so it is not a read-only baseline.
- Serialization cost of handing ~300k parsed entries from a Rust process to Python (for `entries()` callers): UNKNOWN. This decides whether the single-authority slice is viable (section 6).

## 3. Candidates, ranking and the first Rust core slice

### 3.1 What the measurements say

- The cost is in verification (66% of profiled time), mostly per-record Python overhead. A Rust implementation of the same per-record logic removes interpreter cost. It does not, by itself, remove sqlite access costs unless the verified store is also owned by Rust.
- Verification is shared by 44 `entries()` callers and 4 `role_entries()` callers. Moving only the role path would duplicate the verifier, which the single-authority rule forbids.
- The verified store (`records.verified.sqlite3`, 544 MB) is already a durable derived cache of verified payloads. A new derived cache (S1) therefore overlaps it. S1 can still remove the whole cost for cold readers, but only the role readers.

### 3.2 Ranking (measured aggregate cost first; then reliability, boundary clarity, migration risk, build cost)

| Rank | Candidate | Language | Evidence | Verdict |
|---|---|---|---|---|
| 1 | **T1: tape read/verify core** (framing, envelope verification, verified-store lookup, role fold; malformed-line evidence) | Rust | 66% of profiled `wall show` in verification, plus parse 21% (2.3). Shared by all readers. | **The owner's chosen responsibility and the first Rust core slice** (3.3). Not deliverable in 24 h (section 6). |
| 2 | S0: hoist per-record path and directory work out of `_verify_records` (one directory fd per pass, one `Path` comparison, batched verified-store lookups) | Python | 13% directory + 9% path equality + some sqlite (2.3). Language-independent and not a boundary change. | Candidate for the first Python step, before T1 is decided. Needs semantic check against `records.py` verification rules. |
| 3 | S1: derived role projection for cold readers | Python | 18 s per cold `wall show` (2.2); cache is process-local (`feed.py:53`). | Candidate. Overlaps the verified store (3.1). Benefit threshold in 3.4. |
| 4 | S2: `readings` cadence or window-limited tape work | Python | 5.9k CPU-s/day (2.4). Cause unprofiled. | Deferred until the `discover scan` profile exists. Scheduler change, strong. |
| 5 | S3: diagnose `doctor` exit-1 runs | Python | Current failure, cause UNKNOWN (2.4). | Diagnosis first; no migration. |
| — | R2: Rust rewrite of `discovery.py` | Rust | No measured scanner cost; 7,632 lines. | **Rejected** for now. |
| — | R3: Rust CLI front end | Rust | Startup 0.09 s, import 54 ms; cost is data-dependent. | **Rejected**: no startup problem. |
| — | R4: Rust dispatch or host-capacity | Rust | Dispatch is cheap by design; host-capacity has no CPU sample; containment-adjacent. | **Rejected**: no measured benefit; protected limits. |
| — | R5: resident feed service over IPC | either | Adds a resident process; the 2026-11-03 OOM concerns resident readers. | **Rejected**: speculative, and reintroduces the resident-memory failure. |
| — | Budget producer as a Rust candidate | Rust | `budget.sh` calls operator-side scripts and a provider text module outside `src/`. | **Rejected**: not Mishe core. |

### 3.3 The first Rust core slice (T1), precisely

**What it owns.** One responsibility: turning the chat tape into verified entries and role projections. It owns framing, header and body validation, record envelope verification (sha256 check against `records.py:208`), the verified-payload lookup and store (`records.py:229-323`), and the role fold (`feed.py:444`). It does not own locking or writes to the tape, and it does not own sending or appending. It is a core slice, not a runtime rewrite.

**Bounded program contract** (a new program, e.g. `mishe-tape`; name is a proposal):

- `mishe-tape entries --tape PATH --home SITE [--start N] [--limit N]`: writes entries as length-prefixed records to stdout (binary, fixed header with sequence, timestamp, source, body length, body bytes, plus home reference); diagnostics on stderr.
- `mishe-tape role --tape PATH --home SITE --role ROLE`: writes the role projection as one JSON object to stdout.
- `mishe-tape verify --tape PATH --home SITE`: exit 0 when the whole tape verifies; exit 2 when a fault is found and writes the first fault as a single line on stderr, preserving the original text; any other exit code (crash, spawn failure) is unobserved.
- Reads a preserved shared lock: the caller (Python) holds the shared lock during the capture (`feed.py:875-890`), passes the captured size with `--size N`, and the program reads only that prefix.
- Writes: none to the tape. The verified-store updates are the only writes, performed with the same batching semantics as `records.py:299` and the same directory-identity check (`records.py:96`).

**Authoritative consumers that call it:**
- `Feed.entries()` (`feed.py:973`), via `entries` subcommand, for all 44 callers.
- `Feed.role_entries()` (`feed.py:862`), via `role` subcommand, for `wall.py:880`, `wall.py:965`, `wake.py:52`, `migrate.py:29`.

**Predecessor retired in the same release:**
- `feed.py:149` `_iter_streamed_feed`, `:259` `iter_feed_entries`, `:398` `_iter_text_lines`, `:742` `_verify_records`, `:444` `_role_fold`, `:53` `_ROLE_ENTRIES_CACHE`, `:862-930` `role_entries` body (replaced by a subprocess wrapper), `records.py:229-323` verified-store logic (`VerifiedStore`, `lookup`, `store`, `cached_payload`, `verified_store`), `feed.py:610-614` whole-tape `read_bytes` path used by `entries()`.
- Kept (not part of the slice): the Python `Feed` class's locking, checkpoint index, identity index (`feed.py:527-576`), and writers (`Feed.append*`). Retiring any of these needs a later slice.

**Obsolete paths that must be deleted, so one authoritative implementation survives:** the Python framing and verification functions above, and the tests that exercise them only through the deleted functions (rewritten against the Rust contract). No Python verifier may remain callable from production code after the cutover. Temporary isolated validation (a fixture harness calling both implementations) is allowed under the brief, but not in the live path.

**Build provenance (prerequisites, section 6):** Rust toolchain pinned by a committed `rust-toolchain.toml` (currently absent); `Cargo.lock` committed and the dependency graph vendored or verified offline from `~/.cargo/registry` (`sha2`, `rusqlite` with bundled `libsqlite3-sys`, `hex`, plus one JSON crate only if needed); target triple and glibc assumptions recorded; release binary stored in `releases/<sha>/bin/mishe-tape` with a sha256 manifest; the previous release directory retained for rollback.

**Interface tests (must exist before cutover):**
- Byte-identical outputs: `entries` and `role` against the Python implementation on the full 209 MB tape copy, all 298,161 entries, plus the existing fixture suite.
- Malformed evidence: each malformed case in `feed.py:283-310` produces the same `FeedError` text on stderr and exit 2; the `meta.malformed` count written to the identity index (`feed.py:548`) is unchanged.
- Partial and truncated input: a tape cut inside an entry, a missing trailing newline, a bad envelope digest, an unknown verified-store row, a directory swap during the pass (`records.py:96` check).
- Concurrency: a writer appending while the reader holds the captured size.
- Exit-code and stream contract: stdout is only the payload; diagnostics only on stderr; killing the process mid-write leaves the verified store consistent (sqlite transactions).

**Numerical benefit threshold (derived from 2.2 and 2.3):**
- Cold `wall show --owner genome`: user time ≤ 1.5 s (≥10× below 15.2–15.5 s), elapsed ≤ 2 s, peak RSS ≤ 70 MB, output byte-identical.
- `entries` full tape: user time at least 5× below the Python parse plus verify on the same tape. This number is a target. The measured Python baseline for the full `entries` path is not yet taken (2.6).
- No claim of speedup is made here. These are thresholds the first prototype must meet, measured on a preserved copy.

**Invariants:** the tape is never modified by the slice; every record is verified before it is trusted; malformed and unknown input evidence is preserved (`parsing-policy.json`; `docs/protocol-boundaries.md`); a missing or unknown result stays UNKNOWN; unknown exit codes are unobserved, never GREEN.

**Actual-running-consumer observation:** after promotion, journal `Consumed` lines for `mishe-wake@genome` and `mishe-wake@senses` and for `mishe-producer-readings`, compared with the pre-cutover window of equal length, plus one timed cold `wall show` on the live tape, plus a counter of `mishe-tape` exits by code (from the caller's log line).

### 3.4 Python-first steps (language-independent, do before or instead of T1)

- S0: hoist per-record work out of `_verify_records` (one directory fd per pass; compare home paths once; batch verified-store lookups). Benefit threshold: wall show user time ≤ 10 s (≥ 35% below 15.2 s), verified before any Rust decision. This is a hypothesis from 2.3, not a measured result.
- S1: derived role projection for cold readers. Benefit threshold: ≥10× as in T1, output byte-identical to `_force_full`. Its rollback is deleting the derived file. Overlaps the verified store, so the measured benefit of S0 comes first.
- The two can be combined. If S0 and S1 meet their thresholds, T1 must beat the combined baseline by the threshold in 3.3 or be abandoned (section 5.2).

## 4. Failure, release and recovery acceptance

### 4.1 Boundary: T1 (`mishe-tape`)

| Case | Expected behaviour | Exists today | Unproven |
|---|---|---|---|
| Termination before a verified-store commit | Store unchanged; retry reads tape again | sqlite transactions and batching (`records.py:299`) | Kill during batch |
| Termination after commit | Stored verifications stay valid; no tape change | Verified-store identity check | Across processes |
| Exec or spawn failure | Python treats exit other than 0/2 as unobserved | Producer convention (`producers.py:26-43`) | For `mishe-tape` |
| Child exit propagation | 0 ok, 2 refused, other unobserved | Producer convention | Fault injection |
| stdout / stderr | Payload only on stdout; diagnostics on stderr | Producer detail file | For the new contract |
| systemd SIGTERM / SIGKILL, descendants | Contained by `KillMode=control-group` | Yes, for producers and wakes | Under `mishe-tape` |
| Lock lifetime | Caller holds shared lock during capture only (`feed.py:875-890`) | Yes | Across the subprocess call |
| Partial, truncated, malformed input | Same `FeedError` text; exit 2; tape untouched | `feed.py:283-310` | Rust parity |
| Rename / fsync durability | Verified store committed by sqlite; no rename needed | sqlite durability | — |
| Interrupted update of the binary | Old release stays pinned until promotion succeeds | Pin mechanism | Needs a test |

### 4.2 Boundary: S1 derived projection file

| Case | Expected behaviour | Exists today | Unproven |
|---|---|---|---|
| Termination before rename | Temporary file ignored | — | Needs test |
| Termination after rename | Valid file or fallback | Process-local checkpoint validation (`feed.py:653`) | Cross-process |
| Partial or foreign file | Fall back to full scan; tape untouched | `FeedError` fallback (`feed.py:917-919`) | Cache-specific cases |
| Fsync and rename | Fsync temp file, then rename | Not present | Needs test |

### 4.3 Release and recovery (any boundary)

- Release: committed-snapshot pin only; no build on the live host; artifact integrity by sha256 manifest.
- Rollback: a single pin revert to the retained prior release (e.g. `releases/4a4b94ce…`). The derived file (S1) is deleted, not migrated. T1's rollback is the pin revert, because the Python predecessor lives in the prior release directory. Nothing else changes.
- Acceptance: observation of the real consumers (section 3.3) after promotion, automatic rollback on failed observation (`AGENTS.md`).

## 5. Policy, abandonment and review

### 5.1 Ordinary versus strong change

| Change | Class | Reason |
|---|---|---|
| S0 hoisting | Ordinary if verification rules are unchanged (proved by the byte-identical test); otherwise strong | Touches `feed.py` and `records.py`, which are in `parsing-policy.json`. |
| S1 derived file | Ordinary if verification and lock are unchanged | Touches the feed reader; a fresh reviewer is still recommended. |
| S2 `readings` cadence | Strong | Scheduler change (`duties.json` `every`/`max_age`). Needs fresh review and activation, revert and reapply checks. |
| S3 doctor diagnosis | Ordinary (diagnosis only) | No behaviour change until cause is known. |
| T1 `mishe-tape` | **Strong** | Protocol boundary, verification, durable verified store, delivery of a binary. |

For any strong change, the required checks cannot be compressed or skipped:
1. A fresh reviewer who did not author the change reviews it. The author cannot review it.
2. Deterministic activation check: the new path is the one the real consumer calls, shown by observation, not by a unit test.
3. Deterministic revert check: the pin revert returns the consumer to the predecessor, and the consumer's output is unchanged.
4. Deterministic reapply check: reapplying the same pin reproduces the same observed behaviour.
5. Only then promotion, and automatic rollback on failed observation.

These checks are defined here, not executed. The owner's one-day target does not waive them.

### 5.2 Abandonment conditions

- T1 is abandoned if the byte-identical acceptance test cannot pass on the full tape, if the serialization cost in section 6 exceeds the Python parse cost, or if S0 plus S1 meet their thresholds and T1 cannot beat the combined baseline by the threshold in 3.3.
- S1 is abandoned if output differs from `_force_full` or if the derived file can be trusted only by bypassing verification.
- Any Rust step is abandoned if it needs a resident process or IPC to be useful.

### 5.3 What would change the recommendation

- A profile of `discover scan` showing the cost is scanner logic, not tape iteration: this raises S2 or R2.
- Evidence that wakes do not call `wall show` cold: S1's benefit falls to producers only.
- Superlinear parse or verify cost in tape size: strengthens T1.
- A cold-cache measurement much larger than the warm one: strengthens S1.
- S0 meeting its threshold: lowers the case for T1.

## 6. 24-hour critical path, go/no-go and build headroom

### 6.1 Go/no-go (decision made before any build)

**Decision: NO-GO for delivering T1 as a running Rust core slice in 24 hours.** The reasons are measured or verified, not estimated:

1. Single-authority: T1 must own verification for both `entries()` (44 callers) and `role_entries()` (4 callers). Moving only one path leaves two live verifiers. Owning both means a whole-tape handoff from Rust to Python for `entries()`, and the serialization cost of that is UNKNOWN (2.6).
2. Verified-store coupling: verification uses `records.py` sqlite (`records.py:229-323`) and sha256 envelopes (`records.py:208`). T1 must own that store or leave Python doing per-record sqlite calls, which the profile identifies as a large share.
3. Strong change: T1 is a protocol and verification boundary. The required fresh review and activation, revert and reapply checks (5.1) cannot be compressed.
4. Toolchain not pinned: no `rust-toolchain.toml`, no `Cargo.lock`. Pinning them and resolving the offline graph is a build step, which is not authorized now.
5. Byte-identical acceptance on 298,161 entries has not been exercised by anything.

**The go condition for a later assignment (all must hold):**
- G1 — Serialization: a measured prototype handoff (not authorized now) shows the Rust→Python `entries` handoff costs no more than the Python parse it replaces, on a preserved copy.
- G2 — Speed: a measured prototype shows user time ≤ 1/5 of the Python parse plus verify path on the same tape, with byte-identical output (3.3 thresholds).
- G3 — Headroom: all build triggers in 6.2 pass at build time.
- G4 — Pin: a `rust-toolchain.toml` and `Cargo.lock` committed, with the dependency graph verified offline against `~/.cargo/registry`.
- G5 — Review: a fresh reviewer assigned, and the strong-change checks in 5.1 scheduled.
- G6 — Rollback: the prior release retained and the pin revert rehearsed on a copy.

**What would say no (any one):** G1 or G2 failing on the preserved copy; the byte-identical test failing on the full tape; the malformed-evidence test changing any `FeedError` text or `meta.malformed` count; a reviewer rejecting the single-authority plan.

**Safe trigger for collecting G1 and G2:** these need a prototype, which is an implementation assignment. Investigation can collect the cheap parts now: the full-tape `entries` Python baseline (the same guard as 2.1, one repetition, on a preserved copy when the trigger in 2.6 passes).

**What still slips if no-go:** the Python-first steps S0 and S1 are not blocked by T1. They can proceed on their own assignments, and their results decide whether T1 is still needed.

### 6.2 Build headroom prerequisites (from 0:op, applied to any build)

Measured values at 23:46Z are shown; each trigger is checked again immediately before a build.

| Prerequisite | Trigger | Value at 23:46Z | Result |
|---|---|---|---|
| Memory available | MemAvailable ≥ 8 GiB (MemFree is not a useful signal: page cache holds 28 GB) | 28.2 GB | pass |
| Memory pressure | `/proc/pressure/memory` some avg10 ≈ 0 | avg10 0.00 | pass |
| CPU load | 1-min loadavg < 4 | 0.96 | pass |
| Revival timer | `plant-mishe-revive.timer` active; last service `Result=success`; not recovering from a trip | timer active, last trigger 23:42:16Z, `Result=success` | pass |
| Containment | `/var/lib/mishe-containment/tripped` absent | absent | pass |
| Mind windows | no `mishe-wake@*` running and no wake starting within the build window | not checked in this pass | UNKNOWN; must be checked immediately before build |
| Niceness and caps | build under `nice -n 19`, `CARGO_BUILD_JOBS=2` (-j2), and a transient cgroup with `MemoryMax` set below the host limit | not applicable (no build) | to be defined by the build assignment |
| Location | build once in a throwaway directory, outside the repo checkout and outside `src/` | not applicable | to be enforced |

If any trigger fails: no build. The slice slips. The Python path stays authoritative, and the Python-first steps continue on their own assignments.

### 6.3 Critical path (24 h planning target, conditional on the go conditions)

Effort estimates are rough planning figures for a skilled author, not guaranteed. They are shown so the owner can see where the 24 hours goes. Items marked **blocked** cannot run inside this investigation.

| # | Step | Effort | Status |
|---|---|---|---|
| 0 | Decision: go/no-go (6.1) | 0.5 h | done here: NO-GO |
| 1 | Pin toolchain (`rust-toolchain.toml`, 1.97.0) and generate `Cargo.lock` offline | 1 h | **blocked** (build/implementation) |
| 2 | Write the byte-identical harness against the Python path on a preserved tape copy (fixture + full tape) | 3 h | **blocked** (implementation) |
| 3 | Prototype `mishe-tape entries` and `role` in a throwaway directory, measure G1 and G2 | 6 h | **blocked** (build) |
| 4 | Malformed-evidence and concurrency tests | 3 h | **blocked** |
| 5 | Cutover patch: delete predecessor paths, wrap `entries()` and `role_entries()` | 4 h | **blocked** |
| 6 | Fresh strong-change review (not the author) | ≥ 2 h, asynchronous | **blocked** (needs a reviewer) |
| 7 | Activation, revert and reapply checks, then promotion, then observation of real consumers | 2 h plus observation window | **blocked** |
| — | Total planning estimate | ≈ 21.5 h of work (steps 0–7), plus review and observation waits | exceeds 24 h once review and observation are counted |

Even if steps 1–7 went smoothly, the observation window and review waits take the path past 24 hours. The owner's one-day target therefore slips. Nothing in this path can be compressed without skipping a required check (5.1).

## 7. Remaining UNKNOWNs

1. `mishe feed` whole-tape cost and peak RSS (2.6).
2. Parse and verify scaling against tape size (2.6).
3. Cold-cache `wall show` timing (2.6).
4. Cold tape scans per wake and their share of 339 wake runs/day (2.6).
5. `discover scan` profile split: tape iteration versus scanner logic (2.6).
6. Per-run CPU for `dispatch` and `host-capacity` (2.4).
7. Cause of `doctor`'s 25 exit-1 runs and 25 RED lines (2.4).
8. Whether `observer-checks`' child check programs (`checks-runner`, line 256) read the tape. Its own runner does not (1.3).
9. Live behaviour of the 5 commits between the pin and HEAD (0).
10. The OOM: the killed process and whether `role_entries` or the tape path was involved. The journal confirms the events at 13:09:53Z, 13:53:03Z and 13:57:02Z but not the cause (1.4).
11. Whether the Rust crate graph resolves fully offline from `~/.cargo/registry`; no `Cargo.lock` exists (0).
12. Serialization cost of the Rust→Python handoff for `entries()` (6.1 G1).
13. Full-tape `entries` Python baseline (not yet measured, 6.1).
14. Whether the `mishe-wake` processes are in a mind window during any build (6.2).
15. Malformed-evidence form: `FeedError` text and the `meta.malformed` count are identified (`feed.py:283-310`, `:527-576`), but the full list of malformed-evidence artifacts the repo keeps is not inventoried.
16. The 2026-11-03 OOM time mismatch with the `feed.py:901-905` comment (1.4).
17. Test coverage of the verified store under concurrency (2.5 covers role views, not `records.py` concurrency).

## 8. Implementation status (owner update: the inner rust-plan mind owns delivery)

This section records what is implemented and what each state means. Terms follow the owner's ladder: **committed**, **reviewed**, **activated**, **observed**, **recovered**. Each is a separate fact; none implies the next.

### 8.1 First Rust core slice: narrowed to framing

The verifier cannot be the first slice alone. `Feed.entries()` (44 callers) and `Feed.role_entries()` (4 callers) share record verification and the verified store, so a verification-only slice would either duplicate it or pull in the whole protocol boundary. Framing is a separate responsibility with one authority. `iter_feed_entries` is the only framing function: both `_iter_streamed_feed` (`feed.py:149`) and `_iter_parsed_feed` (`feed.py:231`) pass through it (`feed.py:259`). The first slice is therefore framing only: header, body and terminator rules, line splitting, and sequence/timestamp checks. Verification, the verified store and the role fold stay in Python.

Implemented in `rust/mishe-tape/`, a dependency-free crate pinned to Rust 1.97.0 (`rust-toolchain.toml`, `Cargo.lock` committed):
- `src/frames.rs`: `split_lines` (the `str.splitlines` break set, CRLF as one terminator), `Framer` (state machine with the same line indexes and error texts as `iter_feed_entries`), and `read` (streaming, one segment and one entry in memory at a time).
- `src/main.rs`: `mishe-tape frames PATH [--size N] [--start N]`. Output per entry: `E <seq> <timestamp> <source> <body-bytes>\n` followed by the body bytes and `\n`. Exit 0 when the range frames, 2 on a framing fault (the Python `FeedError` text on stderr, after every entry before it), 1 on usage or I/O.
- 8 Rust unit tests (`cargo test --offline`), and `tests/test_mishe_tape_frames.py`, a differential test against the checkout's Python reader. It runs when `MISHE_TAPE_BIN` names a built binary and skips otherwise. With the binary it passes 4 tests and 18 subtests.

### 8.2 Verified results

Differential against the pinned Python `_iter_streamed_feed` (site-local harness under `artifacts/rust-plan-20261107/diff/`, not committed):

| Input | Result |
|---|---|
| Live `chat.log` prefix, 209,446,794 bytes, all framed entries | **298,163 entries identical**, no first difference, both streams end together |
| 22 malformed or edge fixtures (bad dates, leap years, bad hour and second, 7-digit fraction, sequence gap, unterminated, empty body, bad body prefix, no trailing newline, `v1 ` prefix, bad source, CR/VT/NEL/LS inside bodies, CRLF, header without newline) | 21 identical, including every `FeedError` text |
| Invalid UTF-8 byte in a body | Both stop with a fault and no entries before it; **only the decoder's own text differs** (documented exception) |

Before the streaming fix the Rust side was correct but held the whole range in memory (485 MB peak). The fix is measured below. The comparison caught two real divergences during development, both fixed: the timestamp range check ran before the sequence check, and the first UTF-8 fixture was not really invalid.

Measurements (live tape prefix, same guard as 2.1, three repetitions each; samples under `artifacts/rust-plan-20261107/timing/`):

| Measurement | User (s) | Sys (s) | Max RSS |
|---|---|---|---|
| Python framing only (`_iter_streamed_feed`, no verification) | 1.80–1.84 | 0.17–0.19 | 34 MB |
| Rust `mishe-tape frames`, streaming | 0.46–0.47 | 0.04–0.05 | 2.4 MB |
| Python consuming the Rust stream into `FeedEntry` objects (handoff) | 0.47–0.51 | 0.06–0.08 | 21 MB |
| Rust child during that handoff | 0.48 | 0.08–0.09 | — |

So a framing pass costs about 1.8 s of Python CPU today and about 0.95 s with the Rust framer plus the Python handoff, with less memory. That is about 0.85 s saved per full-tape framing pass. Against the cold `wall show` baseline of 15.2–15.5 s user, the framing slice alone saves about 5%. **The large cost is verification (about 66% of profiled `wall show`, section 2.3), and this slice does not remove it.** No claim of a larger speedup is made.

### 8.3 States

| State | Status |
|---|---|
| Implemented | Yes: `rust/mishe-tape/` and `tests/test_mishe_tape_frames.py` are committed, and `feed.py` frames through the binary. |
| Committed | `9f94ede6` (crate, test, plan), `a8590afb` (absolute offsets, resume at byte offset, `--offsets`), `270fec06` (ASCII-only header digits) and `a995031a` (feed cutover; retires the Python framer). |
| Reviewed | **Yes.** The `a995031a` moving-pin gate review was clear (`check-moving-pin-a995031a.out`, phase `reviewed`), and the `e90292ba` delivery that made the cutover stick reviewed the feed/framing path clear (`check-moving-pin-e90292ba.out`). Feed reading is a protocol boundary (`parsing-policy.json`), so the cutover is a strong change; the reviewer was not the author. |
| Activated | **Yes.** The cutover has been carried by every pin since `e90292ba` (2026-11-08 11:14Z). The live pin is `7ce12523` (`runtime/current`), delivered 2026-11-09 18:01:38Z with `delivery_verified` true (`operator/plans/moving-pin-20261105/delivery/deliver-run-173429.log`). |
| Observed | **Yes.** Live consumers after the pin move wrote fresh role projections through `feed.role_entries` -> `_iter_streamed_feed` -> `mishe-tape` (`$SITE/feed.role-cache/{genome,rust-plan,health}.json`, 18:29-18:32Z), and `mishe-producer-readings` completed under the pin at 18:07/18:17/18:27Z. A controlled `parse_feed` under the pin's source framed a fixture through the installed binary. |
| Recovered | Not applicable: no failure of this slice required recovery. The `a995031a` moving-pin was reverted once at 2026-11-08 11:07Z because a consumer fence saw a live `mishe-producer-devto.service`; the same bytes were re-delivered by `e90292ba` at 11:14Z. |

Trailers: the crate commits were authored outside a wake and carry only the Claude coauthor line. The cutover and later slices were delivered through the plant's `deliver.sh` gate, not by a wake release request.

### 8.4 Cutover steps (done)

1. `feed.py` cutover: `_iter_text_lines` and `iter_feed_entries` are replaced by a subprocess reader of `mishe-tape frames`. Done in `a995031a`; no Python fallback remains.
2. Retire the second framing implementation: `_frame_offsets` is replaced by `mishe-tape frames --offset N --offsets`. Done in `a8590afb`; `feed.py` passes the captured handle's inode via `/proc/self/fd/N` (`feed.py:200-208`), so the bytes come from the inode the caller locked.
3. Unicode-digit divergence closed: the Rust header parser accepts ASCII digits only, and `feed.py`'s `HEADER_RE` was changed to match (`270fec06`).
4. Build provenance: the binary is built from `rust/mishe-tape` with the pinned toolchain and installed site-locally at `$SITE/bin/mishe-tape`, hash-pinned by `$SITE/bin/mishe-tape.install.json` (`12dde4d2…ba710`, source `a8590afb`). It is not inside the release tree; the release carries only `bin/mishe`. The site-local install is a deviation from the original release-layout proposal, and it is what the live consumers resolve.
5. Strong-change review and delivery: the moving-pin gate reviewed the cutover clear (`e90292ba`, 2026-11-08 11:14Z) and delivered it again with the current pin (`7ce12523`, 2026-11-09 18:01:38Z, `delivery_verified` true), with the activation/revert/reapply verify exercised.
6. Rollback: a pin revert to a pre-cutover release restores the Python framer, which is retained (`releases/e5a3f4e4…`, `releases/4a4b94ce…`). The revert was exercised by the delivery's `revert`/`revert-observe` steps.

### 8.5 Go/no-go, updated

- **GO, done:** the framing core was built, tested in a throwaway directory, proved byte-equal on the live tape, committed, reviewed and delivered. The cutover is live and observed (8.3).
- **24-hour target:** met for the framing slice. The crate was committed 2026-11-07/08 and the cutover has been live since 2026-11-08 11:14Z. The whole core was not migrated in that time, and no claim is made that it was.
- **Next slice (verification, B):** needs the verified-path benchmark (8.6 item 2) and the offline `rusqlite` build cost under the headroom rule.

### 8.6 Remaining UNKNOWNs (updated)

1. Verification's cost split after framing: the per-record share (sqlite lookups, path bookkeeping) is still measured only by the single profile (section 2.3), not by a benchmark.
2. Serialization cost on the **verified** path: framing handoff is measured (0.5 s); the verified-entry path is not.
3. Cold-cache timing (not measured; cache dropping is forbidden).
4. The acceptance comparison named in 9.3 A — journal `Consumed` lines for `mishe-wake@genome`/`mishe-wake@senses` against a pre-cutover window, and a cold `wall show --owner genome` — has not been taken; the live observation is the role-cache and producer evidence in 8.3.
5. The 2026-11-03 OOM cause (1.4).
6. Whether the site-local `mishe-tape` install (8.4 step 4) is acceptable as the permanent provenance mechanism, or whether the binary should move into the release tree.
7. The live pin is `7ce12523`; HEAD has moved past it with other minds' commits (engine router, view readability). Their live behaviour is not this slice's evidence.

## 9. Executed plan and progress table (48 h, operator direction 2026-11-08)

Source: the operator's 48-hour direction (`operator/vision-48h-20261108.md`, DM 298202). It asks for an executed plan of what to migrate, with an inventory, ordered slices, each predecessor, its authoritative consumer, acceptance, rollback and build budget, and as many slices live and observed as evidence allows, starting with the tape read/verify slice. Rules that stay: no shadow implementations (each cutover retires its predecessor), strong review and revert/reapply checks through Genome's delivery path, containment and headroom, and inner delivery never depending on outer panes.

Measured numbers are the ones in sections 2 and 8. Nothing here is a new speedup claim.

### 9.1 Progress table (updated 2026-11-08)

| Slice | State now | Evidence | Next step |
|---|---|---|---|
| **A. Tape framing** (`mishe-tape frames`) | **Live and observed.** Cutover committed (`a995031a`), reviewed clear (`e90292ba` gate), delivered (`7ce12523`, delivery_verified 18:01:38Z). Every pin since `e90292ba` (2026-11-08 11:14Z) carries it. Live consumers wrote fresh role projections through the binary (`$SITE/feed.role-cache/{genome,rust-plan,health}.json`, 18:29-18:32Z) and `mishe-producer-readings` completed under the pin. | Differential test 5/5 with the committed artifact (`MISHE_TAPE_BIN`). A fresh build from HEAD in a throwaway dir (1.97.0, `--locked`) has the same SHA-256 as the artifact (`12dde4d2…ba710`). | Take the cold `wall show --owner genome` before/after comparison (9.3 A) if a direct end-to-end observation is wanted; the role-cache evidence already shows the live path. Next migration target is B (verification). |
| **S0. Python hoist in `_verify_records`** | **Delivered and observed** (2026-11-10). Commit `7e87f218`; the plant's moving-pin driver delivered it with `b06ae51b` (delivery record `.mishe-tauftauf/patches/moving-pin-b06ae51b.json`: phase `applied`, `delivery_verified true`, fresh gate review clear, activation/observation/revert activation/revert observation all exit 0). `runtime/current` points at `releases/b06ae51b…`, whose `feed.py` carries the hoist (`_same_home`, one `entry_home` for the pass). **The 3.4 threshold was not met**: the cold `wall show --owner genome` is ~13 s user, not ≤ 10 s. Section 10.10. | Same-tape A/B on the live tape, one open descriptor shared by both sides, three fresh processes each: live 4.20 / 4.19 / 4.25 CPU-s and 556.7–556.9 MB peak RSS vs baseline 6.15 / 6.17 / 5.88 CPU-s and 611.9–612.2 MB, over 299,302 entries with an identical entry digest (`9a8c3c1f…b3e2e`). The hoist is worth about 1.9 CPU-s and 55 MB and changes no entry. | S0b: the cold read's remaining cost is the per-entry verification pass, not `pathlib` (section 10.10). Batch the role read's verification into one pass — **measured prize: 14.55 → 5.52 CPU-s, identical view digest**. |
| **B. Tape verify and verified-store lookup** (`mishe-tape verify`/`entries`) | **Live and observed** (2026-11-11). The cutover streams record-bearing entries into one `mishe-records verify` child per site home; `patches/moving-pin-6114caf4.json` is `phase=applied` with `delivery_verified`, and the delivered pin is `releases/6114caf4…` (`feed.py` sha256 `f5b556a4…`). Paired live A/B: identical view digest at one tape state, 2.03–2.11 CPU-s (25–26%) removed from the cold role read. Sections 10.18–10.20. Original note, now superseded: A is observed live and S0 is measured, so B's remaining prize was known: after S0's pathlib cut, what is left of the verification cost is the sqlite work (196,303 `execute` calls per cold read). | Verification is about two thirds of profiled `wall show` (2.3). The cold read's split, measured live: 5.83 CPU-s with verification, 1.40 without, so 4.43 CPU-s is verification (section 10.9). | Take S0 first: it removes about 1.6 CPU-s of the 4.43 without Rust, so B's baseline is smaller and B's own work is only the sqlite lookup. The build graph was then checked (section 10.13): `cargo build --offline` cannot resolve `rusqlite` at all, but a fetched build costs 39.5 s single-core wall, 378 MB peak RSS and 24 lock packages, and the bundled store interops with Python's `sqlite3` in both directions. |
| **C. Role projection** (`mishe-tape role`, cold-cache derived file) | **Abandoned on measurement** (2026-11-11, §10.21). The derived-file benefit C was to introduce already exists: `367e3775` (2026-11-09, in the live pin) persists the verified projection to `$SITE/feed.role-cache/<role>.json`. Paired A/B at one tape state: the live cache path is 0.218–0.281 CPU-s, the forced rebuild 5.685–5.703, same digest `542b02a0…`. No prize left to win, and a second projection owner would break single authority. | Cold `wall show` 15.2 s user and "the cache is process-local (`feed.py:53`)" were the pre-`367e3775` premise; both are stale (§10.21). | Do not build. §9.2's "Cold role projection 15.2 s user" row is retired by the same measurement. |
| **Senses baseline** (measurement, not migration) | Not started. Needs its own declared duty, not an edit to `rust-plan-progress` (wall, section "Senses baseline"). | Source artifacts already exist under `artifacts/rust-plan-20261107/`. | Declare the duty with its own `max_age`. It reads UNKNOWN when evidence is missing or stale. |
| Producers (`readings`, `doctor`, `observer-checks`) | Not scheduled for migration. Diagnosis first. | 5.9k, 2.9k and 2.5k CPU-s per day (2.4); fresh systemd table at pin `7ce12523`: `readings` 890 CPU-s / 6 h (36 runs, ~24.5 CPU-s each), 870 MB peak. `doctor` has 25 exit-1 runs, cause now named as transient unit-file drift (30 RED in ~576 runs over 48 h, dominated by `mishe-wake-<role>.path`, §10.21). | The `readings` cost is now profiled on the live plant and S2 is decided: see section 10. The slice is a Python slice (unused whole-tape reads, one snapshot per scan, cadence), not a Rust slice. |
| **S2.1. Delete the tape reads that feed nothing** | Implemented (2026-11-10). `_mind_role_coverage` took an `entries` argument it never read; three readings read the whole tape to fill it and discarded the result. | AST-verified: `discovery.py:980` reads `home`, `role`, `now` and not `entries`. Fresh-process A/B of one live scan: 31.56 CPU-s before, 31.58 after, 74 observations both times. **A deletion, not a speedup** — an earlier 37% figure came from running both variants in one process and was wrong. | Committed with 4 regression tests that fail if a tape read returns to these readings. S2.2 (one physical tape read per scan) delivered; S2.3 (cadence) refuted by measurement 2026-11-11 (§10.23). |
| **S2.2. One physical tape read per scan** | **Delivered and observed** (2026-11-10). Commit `d3c0c57c` went through the strong gate (fresh gate review, verdict clear) and the delivery record `.mishe-tauftauf/patches/moving-pin-d3c0c57c.json` reads phase `applied`, `delivery_verified true`, with activation, observation, revert activation and revert observation all exit 0. `runtime/current` now points at `releases/d3c0c57c…`. `sample()` takes the cold verified read once and passes it to the three readers; each reader keeps its own read as the fallback. This is a snapshot change, not a window change. | Measured 2026-11-10 10:05Z–10:24Z on the live plant at pin `323a87fe`, counting framer spawns rather than wrapper calls: **three** physical whole-tape reads per scan, 643,103,475 bytes, 16.98 CPU-s, 858.6 MB peak, 74 observations. Call sites: `_mind_artifact_recurrence` (the cold verified read), `_ledger_evidence_binding` (the census read), `_coord_dm_disposition_age` (the disposition read). See section 10.2a. | Acceptance met on the live consumer: physical whole-tape reads 3 → 1 (643,325,541 → 214,457,060 bytes) on the delivered pin, same 74 observations, the three readings' values unchanged field for field, and the readings producer's own CPU below every pre-delivery run. Section 10.2c. S2.3 (cadence) refuted by measurement 2026-11-11 (§10.23). |

### 9.1a Independent replaceability (operator vision addendum, DM 298227, 2026-11-08)

Target: small POSIX programs behind versioned contracts, each swapped as one
atomic binary rename with its own observation and rollback, with no whole-tree
pin move. Each slice's contract is written next to its code (`rust/mishe-tape/CONTRACT.md`
is the template), with a differential test that runs without the Python runtime.

| Slice | Independently replaceable? | Why |
|---|---|---|
| A. Tape framing | Yes, and live | Input is bytes, output is entries or a fault. Its only Python coupling is the caller in `Feed`, which is the cutover. The contract (`rust/mishe-tape/CONTRACT.md`) and the differential test (`tests/test_mishe_tape_frames.py`) exist; the cutover is delivered and observed (8.3). |
| S0. Python hoist | No | Python code in `feed.py`, not a binary. |
| B. Verify and verified store | No, initially | Reads and writes the verified-store format that Python also reads. It stays coupled to the Python `records` layer until the store contract is written. |
| C. Role projection | Yes, after B | Derived file, can be replaced with a binary that takes the tape and role. Coupled to B's store until B lands. |
| Producers | Not planned for Rust | Systemd units; the replaceable unit is the producer script, not the runtime core. |

### 9.2 Inventory of runtime responsibilities

| Responsibility | Measured cost | Python predecessor | Authoritative consumer | Slice |
|---|---|---|---|---|
| Tape framing | 1.8 s user, 34 MB per full pass. Rust 0.46 s, 2.4 MB. | `feed._iter_streamed_feed`, `_iter_text_lines`, `iter_feed_entries`, `_frame_offsets` | `Feed.entries` (44 callers), `Feed.role_entries` (4 callers) | A |
| Tape verification and verified store | About 66% of profiled `wall show`. Not separately timed in Python. | `feed._verify_records`, `records.VerifiedStore`, `records.lookup` | same as above | S0, then B |
| Role fold | About 2% | `feed._role_fold` | `role_entries` (`wall.py`, `wake.py`, `migrate.py`) | C |
| Cold role projection | 0.218–0.281 CPU-s live (persisted cache), 5.685–5.703 rebuild (§10.21). The 15.2 s user figure was the pre-`367e3775` rebuild. | `feed._ROLE_ENTRIES_CACHE` (process-local) + `feed.role-cache/<role>.json` (cross-process, `367e3775`) | same | C — abandoned (§10.21) |
| Identity index | 23.5 MB sqlite, not timed | `feed.py:527-576` | malformed-count consumers | Kept, not scheduled |
| `readings`, `doctor`, `observer-checks` producers | 5.9k, 2.9k, 2.5k CPU-s per day | `producers.py`, the producer scripts | systemd units | Deferred (S2, S3) |
| `discovery.py` scanner | 890 CPU-s / 6 h, 870 MB peak, every 10 min (consumer-perf, pin `7ce12523`); 24.2 CPU-s and 941 MB peak per run at pin `b3d2cd85` (2026-11-10) | `discovery.py` (7,889 lines) | `mishe-producer-readings` | Candidate again (R2 falsified). Measured 2026-11-09 on a tape copy (298,870 entries, 213 MB, 2 GB cgroup cap): one `sample()` makes five `Feed.entries()` calls, but only the first does the verified read (6.2 CPU-s); the other four hit the process-local `_ENTRIES_CACHE` (about 0.01 s each). Unverified read: 1.5 CPU-s, 604 MB peak. The 43.5 CPU-s "cold verified read" from that copy is **retired**: it was measured without an index checkpoint. Against the live tape with a checkpoint the cold read is 6.08 CPU-s and a bounded indexed read is 0.01 CPU-s (section 10.3). The live per-run cost is now explained: 67.7% of the cold read is record verification, and the scan read the whole tape three times before S2.2 and reads it once now. See section 10 for the S2 decision. |
| Dispatch, host-capacity, budget | Cheap by design; no CPU sample; containment-adjacent | `producers.py`, `budget.sh` | units | Rejected (R4) |

### 9.3 Ordered slices, with acceptance, rollback and build budget

**A. Tape framing.** Retires `_iter_text_lines`, `iter_feed_entries` and `_frame_offsets` in `feed.py`. Consumers: the streamed read path under `Feed.entries` and `Feed.role_entries`. Acceptance: the live differential stays identical after the `HEADER_RE` change; `feed.py` passes the captured handle's inode through `/proc/self/fd/N`; a cold `wall show --owner genome` gives byte-identical output; journal `Consumed` lines for `mishe-wake@genome` and `mishe-wake@senses` are compared with a pre-cutover window of equal length. Rollback: pin revert to `releases/4a4b94ce…`, which is retained. Build budget: no dependencies, so the build is seconds. Build in a throwaway directory, under `nice`, with at least 8 GB available and load below 4.

**S0. Python hoist in `_verify_records`.** No predecessor is retired; this is an optimization that lowers the baseline B must beat. Acceptance: `wall show --owner genome` user time at or below 10 s, with identical output. Rollback: revert the commit. Build budget: none. It is sequenced after A because both touch `feed.py`.

**B. Tape verify and verified store.** Retires `records.VerifiedStore`, `lookup`, `store`, `cached_payload`, `verified_store` and `feed._verify_records`. Consumers: all `entries` and `role_entries` callers, through A's wrapper. Acceptance: byte-identical output on the full tape; a kill during a write leaves the store consistent; the threshold in 3.3 (cold `wall show` at or below 1.5 s user and 70 MB peak RSS); and, measured 2026-11-10 (section 10.14), at least 2 CPU-s of the 2.53 CPU-s store lookup removed with identical output. The store contract is `rust/mishe-records/CONTRACT.md`. Rollback: pin revert. The store format is unchanged, so Python reads it again. Build budget, now measured instead of estimated (section 10.13): six crates fetched (~5.4 MB), a 24-package lock, a single-core bundled-sqlite C build of 39.5 s wall and 378 MB peak RSS — all inside the headroom rule. The build is not reproducible offline from a bare checkout, so the release ships a prebuilt artifact recorded by sha256 (plan 4.3's "no build on the live host" is unchanged) or vendors the crate set.

**C. Role projection.** Retires `feed._role_fold` and `_ROLE_ENTRIES_CACHE`. Acceptance: output identical to `_force_full`; at least 10× faster cold. Rollback: delete the derived file. Depends on B. **Abandoned on measurement 2026-11-11 (§10.21):** the derived file already exists (`367e3775`, live) and a live read is 0.22 CPU-s, 26× below the rebuild this slice would attack, so there is no cold-read prize left to win.

### 9.4 Gates for every apply

- Before any cutover or pin move, tell the operator in one DM, with the slice, the predecessor being retired and the rollback. The vision asks for this, and it is not a request for permission.
- Strong change (`feed` reading is a protocol boundary, `parsing-policy.json`): a non-author reviewer, then the activation, revert and reapply checks, then a release request from a wake. Genome owns the delivery path.
- Nothing counts as live until a real consumer runs the new path, and the journal and the caller's log line show it.

### 9.5 Would change this plan

- A reviewer rejects the framing boundary. A cutover then stops until the boundary changes.
- S0 meets its threshold. Then B must beat the combined S0 baseline by the 3.3 threshold, or be abandoned (section 5.2).
- Non-ASCII header bytes appear on the live tape. The Unicode-digit fix then becomes a blocking item, not a cleanup.
- The 48-hour target was met for the framing slice: A is live and observed, not waiting. The remaining slices (B, C) are the open work.

## 10. S2 decision: the `readings` duty, measured on the live plant (2026-11-10)

Section 9.1 left S2 as "readings cadence or window-limited tape work", cause
unprofiled. This section settles it with measurements taken against the live
plant at pin `b3d2cd85` on 2026-11-10 07:28Z–07:40Z. Every number below was
produced by a probe in `artifacts/rust-plan-20261107/s2-20261110/`, read-only
against the live tape, run under `systemd-run --user --scope -p MemoryMax=2G
-p CPUWeight=20` at `nice -n 19`.

### 10.1 The duty's measured cost

`mishe-producer-readings.timer` runs `discover scan` every 600 s
(`OnUnitActiveSec=600`). The 07:28:15Z run:

| Quantity | Value |
|---|---|
| `CPUUsageNSec` | 24,194,595,000 (24.2 CPU-s) |
| `MemoryPeak` | 941,236,224 (941 MB) |
| wall | 07:28:15Z → 07:28:41Z (26 s) |
| `Result` | success |

At 144 runs/day this is **~3.5k CPU-s/day** and **~135 GB of peak-RSS
allocation per day** for one duty. Its 941 MB peak sits under the
`MemoryHigh=1G` / `MemoryMax=2G` containment on `mishe-plants.slice`, so this
duty is the plant's largest consumer and the one closest to the guard.

### 10.2 Where the cost goes (one live `discovery.sample()`, cProfile)

| Component | Profiled seconds | Share |
|---|---|---|
| `_ledger_evidence_binding` | 12.60 | 33% |
| — of which the cold `Feed.entries()` read | 11.09 | 29% |
| `_mind_treadmill` | 11.21 | 30% |
| `classify_outcomes` (the `_settle_outcome_join` call) | 7.61 | 20% |
| `_coord_dm_disposition_age` | 2.83 | 7% |
| `_head_rewinds` | 2.21 | 6% |

Total 31.69 CPU-s profiled, 850 MB peak. The five `Feed.entries()` calls cost
**one** cold read (the first: 11.09 s; the other four: about 0.01 s each from
the process-local `_ENTRIES_CACHE`). On top of that the tape is streamed
physically **three** times per scan, 642 MB in total (61 GB/day). The three
rows below are the **wrapper calls** that make those reads; two of them are
one read seen twice, and one names a caller the current tree does not have.
Section 10.2a gives the physical-read count and the real call sites.

| Wrapper call | Bytes | Call site |
|---|---|---|
| `feed.parse_feed` | 214,119,574 | `_mind_treadmill` → `Feed.entries` → the cold read |
| `feed.iter_feed_file` | 214,119,574 | `_ledger_evidence_binding` → `classify_outcomes`, which takes no snapshot |
| `store_census.tape_entries` | 214,119,574 | `_ledger_evidence_binding`, collecting the named record digests |

#### 10.2a Correction: the read table above counted wrappers, not reads

The three rows above are three **wrapper calls**, not three physical reads, and
two of them are one read seen twice. Counting the wrapper calls over-counted
the tape traffic, and one row named a call site that does not exist in the
current tree.

Measured on the live plant at 10:05Z–10:24Z (pin `323a87fe`), counting
`feed._spawn_frames` — the framer is the only thing that reads tape bytes, so
counting its spawns counts physical reads exactly:

| Physical read | Bytes | Call site |
|---|---|---|
| 1 | 214,367,825 | `_mind_artifact_recurrence` → `Feed.entries` → the cold read |
| 2 | 214,367,825 | `_ledger_evidence_binding` → `classify_outcomes` → `store_census.tape_entries` → `iter_feed_file` |
| 3 | 214,367,825 | `_coord_dm_disposition_age` → `iter_feed_file` |

Three physical reads, 643,103,475 bytes, 16.98 CPU-s, 858.6 MB peak,
74 observations. Probe: `artifacts/rust-plan-20261107/s2b-20261110/physical_reads.py`.

Two corrections follow:

- **Rows 2 and 3 of the old table are one read.** `store_census.tape_entries`
  is a generator; `classify_outcomes` consumes it, so the same pass through
  the tape appears under both wrappers. The old table counted it twice.
- **The `classify_outcomes` row named a dead caller.** The old table
  attributed the read to "the `_settle_outcome_join` call". In the current
  tree `_settle_outcome_join` has **no production caller**: an AST walk of
  `discovery.py` finds zero references to the name, and a repo-wide grep
  finds only its definition, one test, and an old worktree copy
  (`.claude/worktrees/budget-stale-window/`). The read it was credited with
  is really taken by `_coord_dm_disposition_age`.

The cold read's call site also moved with S2.1: it was `_mind_treadmill`
before that commit and is `_mind_artifact_recurrence` after, because S2.1
removed the three reads that fed the dead `_mind_role_coverage` parameter and
the remaining cold read is the one a reading that needs it owns.

The three physical reads are therefore: one cold verified read that a reading
needs, one census read that collects the record digests the entries name, and
one disposition read. S2.2's job is to name which of these can be served from
one snapshot, and to prove it with the physical-read count rather than the
wrapper count.

### 10.2b S2.2 implemented: one snapshot serves all three reads

The three physical reads of section 10.2a are **not three responsibilities**.
Two of them are the same entries read twice, and the third is a window of the
same entries. Measured on the live plant on 2026-11-10 (pin `323a87fe`, tape
214,398,317 bytes, 299,230 entries), one process, counting framer spawns:

| Reader | Served from the snapshot | Its own read | Reading unchanged? |
|---|---|---|---|
| `_mind_artifact_recurrence` | 0 physical reads | 0 (it already reads the whole tape) | **yes** — every field identical |
| `_coord_dm_disposition_age` | 0 physical reads | 1 | **yes** — every field identical, `dms=40 … stall=35` both ways |
| `_ledger_evidence_binding` | 0 physical reads | 1 | the verdict is the same; the named-record set is the same |

The snapshot is the cold verified read (`Feed(home).entries()`), 299,230
entries at 6.36 CPU-s. It already exists; the three readers simply ask for
it instead of making their own.

**The census's named-record set is identical.** This was the one open risk,
because the digests are collected from the entry bodies during the pass. A
separate fresh-process probe compared the two sets entry by entry:

    named_snapshot 171,962   named_live 171,962
    named_only_in_live 0     named_only_in_snapshot 0
    sequences only in one 0  sequences with differing content 0

So the census needs no pass of its own: the digests a body names are a
function of the body, and the snapshot's bodies are the live bodies.

**The three readers' window and verification needs are met.**

| Reader | What it needs | What the snapshot has |
|---|---|---|
| recurrence | the whole tape, verified | the whole tape, verified |
| census | the entries, plus the digests their bodies name | the same entries; the digests are the same set |
| disposition | a 6-hour window of entries, unverified | the same entries; the window is a subset it already selects |

The disposition reading streams the tape to keep only the window, so it never
holds the tape; the snapshot is already held by the cold read, so the
disposition reading's window now costs no extra memory.

**The change is a snapshot change, and each reader keeps its own read as the
fallback.** The snapshot's read verifies records and raises on a damaged
record reference; the census's `classify_outcomes` reports such an entry as a
coverage gap instead of failing. So the shared snapshot must be an
optimisation with a per-reader fallback, not a replacement: a shared read that
fails leaves each reader on the read it makes today, and no state word
changes.

**One revision per scan, and the tape grows during a scan.** The snapshot is
one revision of the tape taken at the scan's start; today each reader sees a
revision from its own moment, so the readers already disagree when the tape
grows between them. One revision makes the scan internally consistent. The
measured delta in this probe was zero entries appended during the scan; the
per-scan growth is bounded by what the plant writes in a scan's runtime
(about 26 s), which the next probe must measure rather than assume.

**Implemented.** `sample()` takes the snapshot once (`_scan_snapshot`) and
passes it to the three readers, each of which takes an optional `entries` and
keeps its own read as the fallback. `store_census.collect_named` collects the
digests from a supplied entry list.

Measured on the live plant, one scan per process, same probe both ways
(2026-11-10 10:52Z–10:53Z, tape 214,411,788 bytes):

| | physical whole-tape reads | bytes read | CPU-s | observations | peak RSS |
|---|---|---|---|---|---|
| before | **3** | 643,235,364 | 16.90 | 74 | 858.2 MB |
| after | **1** | 214,411,788 | 15.06 | 74 | 860.0 MB |

The three readings report the same values either way: the recurrence reading
`verified`, the census `drift` with `owed=75 all_bad=202 permanent_loss=123
bound=4722`, and the disposition reading
`dms=40 reply_edge=2 … stall=35 p50=27m p90=151m`. The census's `read_at`
stamp differs because it stamps its own instant.

Probes: `artifacts/rust-plan-20261107/s2b-20261110/shared_snapshot.py` and
`census_named_diff.py`.

#### 10.2c S2.2 delivered and observed on the live consumer

Commit `d3c0c57c` ("discovery: one tape revision serves the scan's three
readers") went through the strong gate and is live. The delivery record is
`.mishe-tauftauf/patches/moving-pin-d3c0c57c.json`: phase `applied`,
`delivery_verified true`, activation code 0, observation code 0, revert
activation code 0, revert observation code 0. The gate's own review was the
fresh reviewer (not the author): "The scoped patch adds a shared verified tape
snapshot for discovery readings with per-reader fallback … The supplied
deterministic test run passed (905 passed, 13 subtests), and no concrete code
defect is visible in the supplied bytes." — verdict clear.

The delivery ran on the plant's own path, under a short activation hold:
check at 11:14:11Z, apply at 11:23:55Z, verify at 11:27:55Z, hold released.
`runtime/current` now points at `releases/d3c0c57c…`.

**Observed on the live pin.** The same probe, run against the delivered pin's
own `src` first on `PYTHONPATH`, one fresh process:

| | physical whole-tape reads | bytes read | CPU-s | observations | peak RSS |
|---|---|---|---|---|---|
| before (pin `323a87fe`, 11:16Z) | **3** | 643,325,541 | 16.37 | 74 | 861.9 MB |
| after (pin `d3c0c57c`, 11:28Z) | **1** | 214,457,060 | 14.57 | 74 | 863.1 MB |

The single read site is the snapshot (`_scan_snapshot` → `Feed.entries`); the
census read and the disposition read are gone from the tape traffic.

**The readings' values are unchanged on the same tape.** A fresh-process A/B of
the old pin and the delivered pin against the same tape (11:29Z–11:30Z),
field for field: the recurrence reading is byte-identical (0 differences); the
census differs only in its own `read_at`/`measured_at` stamps; the disposition
reading differs only in `oldest_open` (`genome:5h36m` against `genome:5h35m`),
which is the age of the same open DM a minute later. Every count
(`dms=44 reply_edge=2 … dispositioned=3 open=41 stall=38 p50=27m p90=151m`)
and every census figure (`owed=75 all_bad=202 permanent_loss=123 bound=4722`)
is the same. The states are the same: recurrence `verified`, census `drift`,
disposition `verified`.

**The live consumer's own accounting.** `mishe-producer-readings.service` runs
the scan every ten minutes, and the kernel accounts it. Three runs on the new
pin against a pre-delivery baseline of 25 runs from 07:00Z to 11:18Z:

| | runs | CPU-s min | median | max | peak RSS median |
|---|---|---|---|---|---|
| before (pin `323a87fe`) | 25 | 23.14 | 24.01 | 26.99 | 904.2 MB |
| after (pin `d3c0c57c`) | 3 | 20.760 | 21.107 | 21.932 | 901.5 MB |

**Every post-delivery run is below every pre-delivery run.** The three runs
after the delivery are 21.932 (11:28:59Z), 20.760 (11:38:52Z) and 21.107
(11:48:55Z) CPU-s. The closest pre-delivery runs were 24.274 and 24.614
CPU-s, and the tape grew throughout the comparison, so the baseline drifts
upward; the nearest comparison is still favourable by about 3 CPU-s.

That is larger than the 1.43 CPU-s the isolated probe predicted, and the reason
is in the probe's own numbers: the probe measured `discovery.sample()` alone,
while the producer also runs the signal wrapper, and the two removed passes
(the census's and the disposition's) were over a tape that grew between them.
The live unit's own accounting is the authoritative measurement; the probe is
the attribution.

The live scan (`discovery/scan-20261110T112856Z-…json`) has 74 observations
and reports the three readings with the same states and values. Its `read_at`
stamp for the census is `11:28:39Z`; the probe's own was `11:29:20Z`. That is
the scan's own clock, not a difference in the value.

Producer numbers: `artifacts/rust-plan-20261107/s2c-20261110/producer-cpu-after.json`.

Probes: `artifacts/rust-plan-20261107/s2b-20261110/physical_reads.py`,
`artifacts/rust-plan-20261107/s2c-20261110/readings_values.py`.

### 10.3 The cold/warm split, measured live (not on a copy)

The 43.5 CPU-s "cold verified read" recorded in the section 9.2 table was
measured on a tape **copy without an index checkpoint**. Against the live
tape, which has a valid checkpoint, the same read is:

| Read | CPU-s | Peak RSS |
|---|---|---|
| `Feed(home).entries()` cold | 6.08 | 613 MB |
| `Feed(home).entries()` second call in the same process | 0.01 | — |
| `Feed(home).view_entries(start=1, limit=200)` | 0.01 | — |

**The 43.5 CPU-s figure is retired.** The live cold read is 6.08 CPU-s, and a
bounded indexed read of the same tape is 0.01 CPU-s. The difference was the
checkpoint's absence, not the tape's size.

### 10.4 The cold read's own split: parse vs record verification

Two fresh processes, same live tape, one with `verify_records=True` and one
without:

| Mode | Total CPU-s | Peak RSS |
|---|---|---|
| with record verification | 6.53 | 609 MB |
| without record verification | 2.11 | 607 MB |
| **difference** | **4.42** | |

**Record verification is 67.7% of the cold read.** It is 196,318 sqlite
lookups plus per-record directory and path bookkeeping — not byte-level work.
A Rust port of the *byte* layer cannot touch it; only the sqlite and
filesystem layer can.

### 10.5 The Rust byte-level fact extractor, measured against the Python it mirrors

`mishe-tape-facts` is built, contract-tested and installed. Measured against
the live tape, with both sides charged for self **and** child CPU (the
Python side spawns the Rust framer, so `process_time()` alone under-reports
it by 4×):

| Side | Facts | Total CPU-s | Peak RSS |
|---|---|---|---|
| Python (`iter_feed_file` + three regexes) | 254,158 | 1.75 | 368 MB |
| Rust (`mishe-tape frames` → `mishe-tape-facts`) | 254,158 | 0.97 | 73 MB (children) |
| whole consumer `_coord_dm_disposition_age` | — | 1.70 | — |

**1.8× on total CPU, and 5× on peak RSS.** The facts agree: 252,750 identical
facts, 0 disagreements. The 1,408 remaining pairs are exactly the contract's
documented `!` deferral (an ASCII digit run touching a non-ASCII byte, where
Python's `\d` matches Unicode digits and the caller must apply the Python
rule). The crate is correct and fast, and it is **not** where the duty's cost
is.

### 10.6 What the cost scales with

Reading frame-aligned prefixes of the live tape in fresh processes:

| Fraction | Bytes | Entries | Total CPU-s | CPU-s per MB |
|---|---|---|---|---|
| 12.5% | 26.7 MB | 46,464 | 0.23 | 0.0086 |
| 25% | 53.5 MB | 76,160 | 0.38 | 0.0071 |
| 50% | 106.9 MB | 120,576 | 0.62 | 0.0058 |
| 100% | 214.1 MB | 299,179 | 1.39 | 0.0065 |

Framing is linear in tape size at about 0.0065 CPU-s/MB. The duty's cost is
therefore **proportional to a tape that grows every minute**; the tape has
reached 214 MB and grows with every wake, message and record on the plant.

### 10.7 The decision

S2 was framed as "readings cadence or window-limited tape work". The
measurements split it into three separate problems, and only one of them is
Rust's:

1. **One cold whole-tape read, shared.** The scan's five `Feed.entries()`
   calls cost **one** cold read: the first parses and verifies 299,179
   entries, the other four are served from the process-local `_ENTRIES_CACHE`
   at about 0.01 s each (section 10.2). The tape is then read physically
   twice more, by the census read and the disposition read, neither of which
   uses that cache. So the redundancy is three physical reads, not five, and
   two of the three are not the cache's to reuse. The physical-read count and
   the three call sites are in section 10.2a; an earlier count of three that
   was really two is corrected there.
2. **Record verification (67.7% of the cold read).** sqlite lookups and
   per-record path bookkeeping. Not byte-level, not Rust's, and the same cost
   that slice B (section 9.3) exists to attack. It needs its own benchmark
   against S0's Python hoist before any Rust decision.
3. **Byte-level extraction (0.97 CPU-s of a 24 CPU-s duty).** `mishe-tape-facts`
   already does this, correctly, at 1.8× the Python's CPU and 5× less RSS.
   Its share of the duty is under 5%. Migrating more byte-level work into
   Rust cannot pay for itself here.

**Decision: the S2 slice is not a Rust slice.** It is a Python slice with
two parts, in measured cost order:

- **S2.1 — delete the reads that feed nothing.** `_mind_role_coverage`
  (`discovery.py:980`) takes an `entries` argument it never reads. Verified by
  AST inspection of the function body: parameters `home`, `role`, `now` are
  read; `entries` is not. Three readings (`_mind_treadmill`,
  `_mind_loop_stall`, `_mind_watch_wait`) read the whole tape and pass the
  result to it. **Measured effect: none on CPU.** A fresh-process A/B of one
  live scan gives 31.56 CPU-s before the change and 31.58 CPU-s after, and
  the whole-tape read count is 3 before and 3 after: the cold read was
  already shared through `_ENTRIES_CACHE` with `_mind_artifact_recurrence`,
  which needs it. An earlier in-process measurement of a 37% saving was
  **wrong** — it ran both variants in one process, so the second variant
  inherited a warm page cache and a populated entry cache. The change is
  still worth keeping: it removes a dead parameter, three reads whose result
  is discarded, and the `FeedError` that a discarded read can raise into a
  swallowed `except (OSError, ValueError)`. It is a deletion, not a
  speedup.
- **S2.2 — one physical tape read per scan.** A scan reads the tape
  physically three times (section 10.2a): the cold verified read that
  `_mind_artifact_recurrence` needs, the census read that
  `_ledger_evidence_binding` takes to collect the record digests its entries
  name, and the disposition read that `_coord_dm_disposition_age` takes.
  **The target is decided by measurement** (section 10.2b): all three can be
  served from one snapshot, which is the cold verified read the scan already
  makes. The census's named-record set is identical from the snapshot
  (171,962 digests, no difference in either direction) and the disposition
  reading's output is field-for-field identical, so this is a snapshot change,
  not a windowing change. Each reader keeps its own read as the fallback, so
  a shared read that fails changes no state word.
- **S2.3 — schedule. Refuted by measurement 2026-11-11 (§10.23).** The duty
  reads 941 MB every 10 minutes, but the stored receipts show the reading is
  *not* slow-changing: 0 of 143 consecutive 10-minute pairs carry an identical
  observation set, and sampling the same series every 1200 s drops a distinct
  verdict word for 15 of 74 readings, three of them tape-derived. The cadence
  is not a free lever; no change was made.

**Rust's place in the readings duty** is unchanged and still real: it is
slice B, the verified-store lookup, where 4.42 of the 6.53 CPU-s cold read
sits. That is a larger prize than anything S2 offers, and S2.1–S2.3 lower the
baseline B must beat rather than compete with it.

### 10.8 What would change this decision

- `_mind_role_coverage` starts using `entries`: then the whole-tape read is
  load-bearing and S2.1 must be re-scoped to a bounded read instead.
- The `!`-deferred facts turn out to change a reading's verdict: the crate's
  deferral is then a defect, not a contract.
- A window-limited read is shown to change a reading's output: windowing is
  then unsafe for that reading and the whole-tape read stands.
- `classify_outcomes` is shown to need the whole tape for correctness: S2.2
  is then a snapshot change only, not a windowing change.
- The disposition read is shown to need entries the census read cannot
  supply, or the other way round: then the two reads stay separate and S2.2's
  target is the cold read instead.
- A shared snapshot is shown to change a reading's state word when the shared
  read fails: then the fallback is wrong and the readers must not share.
- The dead `_settle_outcome_join` is shown to be reachable by a caller this
  tree does not have: then it is live work, not dead code, and it must be
  measured again before any slice is taken against it.

### 10.9 S0 measured: the pathlib churn is real, and it is Python's, not Rust's

Section 3.4's S0 ("hoist per-record work out of `_verify_records`") was a
hypothesis. It is now measured on the live tape, on the live pin, in fresh
processes.

**The current split.** One cold verified read, unprofiled, one process each:

| Read | CPU-s | peak RSS | entries |
|---|---|---|---|
| `Feed(home).entries()` with record verification | 5.83 | 610.7 MB | 299,240 |
| the same read with `verify_records=False` | 1.40 | 608.1 MB | 299,240 |
| **difference** | **4.43** | | |

**Where the per-record cost sits.** A profiled run of the verified read
(196,301 records verified) shows the cost is dominated by `pathlib` object
churn, not by sqlite alone:

| function | calls | tottime | cumtime |
|---|---|---|---|
| `sqlite3.Connection.execute` | 196,303 | 1.563 | 1.563 |
| `pathlib.Path.__init__` | 495,559 | 0.794 | 0.905 |
| `pathlib._str_normcase` | 392,602 | 0.308 | 1.815 |
| `pathlib.__eq__` | 196,301 | 0.094 | 1.924 |
| `records.cached_payload` | 196,301 | 0.692 | 5.002 |
| `records.lookup` | 196,301 | 0.602 | 3.743 |

The `pathlib` calls come from two sites, and neither is a verification rule:

1. `feed._iter_streamed_feed` builds `Path(home)` **once per entry** (299,240
   times), although `home` is constant for the whole pass.
2. `feed._verify_records` compares `Path(entry.home) == home`, re-wrapping an
   already-`Path` `entry.home` once per record (196,317 times).
   `PurePath.__eq__` compares a cached `_str_normcase`, so a fresh wrapper
   never reuses the cache — the comparison re-normalizes the string every
   time.

**The hoist, measured.** A probe patches exactly those two sites — one `Path`
for the pass, and a direct `str(entry.home) == str(home)` comparison — and
nothing else. No verification rule is touched. Three repetitions, one fresh
process each, whole tape:

| | CPU-s run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| baseline | 6.46 | 6.38 | 6.17 | 6.38 |
| hoisted | 4.70 | 4.98 | 4.77 | 4.77 |
| **difference** | **−1.76** | **−1.40** | **−1.40** | **−1.61** |

Peak RSS also falls, 610.8 MB to 555.7 MB, because the 196,317 discarded
`Path` objects are not allocated.

**The hoist changes no value.** Every run, both modes, produced the same
digest of the whole read — every entry's sequence, timestamp, source, body and
home, plus every verified payload the pass resolved:

    entry_digest    8c750f7d0bb38d99b02a951855f9cd00291ec44aa3c9eea44c052cab71d0816d
    payload_digest  028390ec2ae778dde542c7b7528a536219a5a788f5fa40d9e6c1ed6ccbd9974b

The payload digest was taken over a bounded window (20,000 entries, 13,755
verified payloads): the sink decodes every verified record it is given, and
decoding all 196,301 costs far more than the read being measured.

**What this changes.** The plan said the cold read's 4.42 CPU-s of
verification is "sqlite lookups plus per-record directory and path bookkeeping
— not byte-level work", and that slice B is where a Rust prize sits. The
measurement says the pathlib part of that is worth about 1.4–1.8 CPU-s **in
Python, with no Rust and no protocol change**. The sqlite part (196,303
`execute` calls, ~1.56 CPU-s profiled) is what is left, and it is what slice B
would have to own.

So S0 should land before slice B is scoped: it takes a quarter of the
verification cost out with a two-line change and no boundary crossing, which
lowers the baseline B must beat and removes work B would otherwise have
reimplemented in Rust. The plan's ordering was already S0 before B; this
measurement says S0's prize is real and sized.

Probes: `artifacts/rust-plan-20261107/s2d-20261110/verified_clean.py`,
`verified_profile.py`, `s0_probe.py`.

### 10.10 S0 delivered and observed: the prize is real, the threshold is not met, and the next cost is the pass structure

S0 is committed (`7e87f218`) and live. The plant's moving-pin driver delivered
it with `b06ae51b`; the delivery record
`.mishe-tauftauf/patches/moving-pin-b06ae51b.json` reads phase `applied`,
`delivery_verified true`, a fresh gate review with verdict clear, and
activation, observation, revert activation and revert observation all exit 0.
`runtime/current` points at `releases/b06ae51b…`, whose `feed.py` carries the
hoist.

**The same-tape A/B.** Both sides read one open tape descriptor, so an append
between the two reads cannot change what either side measured. Three fresh
processes each, whole tape:

| side | CPU-s | peak RSS | entries |
|---|---|---|---|
| live (`b06ae51b`, hoisted) | 4.20 / 4.19 / 4.25 | 556.7–556.9 MB | 299,302 |
| baseline (`d3c0c57c`, un-hoisted) | 6.15 / 6.17 / 5.88 | 611.9–612.2 MB | 299,302 |

The entry digest is identical on both sides (`9a8c3c1f…b3e2e`), so the hoist
changes no entry. It is worth about **1.9 CPU-s and 55 MB**.

**The threshold is not met.** Section 3.4 set S0's acceptance at "wall show
user time ≤ 10 s". Measured cold — the role cache removed before **every**
run, so no side reads a cache the previous run wrote — the live cold read is:

| side | user-s | peak RSS |
|---|---|---|
| live | 13.32 / 12.98 | 73.2 / 73.5 MB |
| baseline | 15.51 / 15.09 | 78.8 / 78.7 MB |

Both sides produce byte-identical output (`171b4e2e…f56d9e`). So S0 saves
about 2.5 s of a cold read that is still ~13 s: **the 10 s threshold is missed
by about 3 s**, and this section records that rather than the earlier,
incorrect figure.

> The first cold measurement of this wake was wrong and is corrected here. The
> probe moved the role cache aside once, before the live runs; the first live
> run was cold, wrote the cache, and every later run (live run 2, both baseline
> runs) then measured a warm cache. That produced "live 12.84 / baseline 0.22",
> which is a cache hit, not a comparison. The corrected probe removes the cache
> before each side.

**Where the remaining ~13 s goes.** A profile of the cold role read
(`Feed.role_entries("genome", _force_full=True)`, the path `wall show
--owner genome` actually takes) attributes it like this:

| function | calls | tottime | cumtime |
|---|---|---|---|
| `feed._verify_records` | **299,304** | 1.81 | 28.73 |
| `sqlite3.Connection.execute` | 196,305 | 2.84 | 2.84 |
| `records.cached_payload` | 196,303 | 0.92 | 8.33 |
| `records.VerifiedStore.lookup` | 196,303 | 1.20 | 6.61 |
| `records._directory` | 196,303 | 0.18 | 5.63 |
| `records.check_pass_directory` | 196,303 | 0.62 | 6.84 |
| `feed._iter_streamed_feed` | 299,305 | 1.74 | 3.31 |

The number that matters is the first one. `_verify_records` is called **once
per entry** (299,304 times), because `role_entries` verifies with
`self._verify_records((entry,))` — a one-entry tuple (`feed.py:931`). Each of
those calls is a whole verification pass: its own `pending` batch, its own
records-directory fd (`records._directory`, 196,303 opens), its own
`check_pass_directory` re-stat of the live records path (196,303 `lstat`), and
its own store flush. The per-pass bookkeeping S0 removed was the *cheap* part;
the pass *structure* is what remains.

**What this changes.** Two things follow.

1. **S0 did not meet its threshold, and its threshold was the wrong target.**
   The hoist removed the `pathlib` churn, which was real, but the cold read's
   dominant remaining cost was never `pathlib` — it is the per-entry pass.
   A slice that removed 1.9 CPU-s from a 15.5 s cold read could not reach 10 s
   on its own. The threshold was set from a 15.2 s baseline that included both
   effects, and it was attributed to the wrong cause.
2. **The next slice is S0b, not B.** S0b batches the role read's
   verification into one pass: one directory fd for the whole read, one
   `pending` batch, one store flush at the end, the same verification rules and
   the same store. It is a `feed.py` change, so it is a strong change
   (`parsing-policy.json` names `feed.py` and `records.py` as boundaries), and
   it needs a fresh reviewer who did not author it plus the activation, revert
   and reapply checks (5.1).

   The expected prize is the per-entry overhead the profile attributes:
   ~5.6 CPU-s of `records._directory`, ~6.8 CPU-s of `check_pass_directory`
   and ~2.8 CPU-s of `execute`, less what one batched pass still owes.

   **That prize is now measured.** The same cold role read
   (`role_entries("genome", _force_full=True)`), same tape, same process, with
   `_verify_records` wrapped so the one-entry calls accumulate and flush as one
   pass every 500 entries:

   | side | CPU-s | verification passes | view entries | view digest |
   |---|---|---|---|---|
   | batch 1 (today) | 14.55 | 299,304 | 30,336 | `ff01c9f2…ce905` |
   | batch 500 | 5.52 / 5.55 | 599 | 30,336 | `ff01c9f2…ce905` |

   The view digest is identical on both sides, so the batching changes no
   output. **S0b is worth about 9 CPU-s**, which would put the cold read under
   the 10 s threshold that S0 missed on its own.

   What the prize is made of: with one pass per entry, every record paid for
   its own records-directory fd, its own `lstat` of the live records path and
   its own sqlite commit — 196,303 commits. Batched, the pass is 599 times,
   so the fds, the lstats and the commits are paid 599 times.

   **What S0b changes, stated plainly**, because it is a strong change:

   - The records-directory swap check (`records.check_pass_directory`) fires at
     each flush and at each pass end. Batched, that check fires once per batch
     instead of once per record, so a directory swap is detected **up to 500
     entries later** than today. The pass still fails closed; the detection
     point moves. This is a bound change to a verification boundary and must
     be reviewed as one.
   - The verified store is written in one transaction per batch instead of one
     per record, so a kill mid-pass loses at most one batch of newly verified
     payloads rather than one record's. Nothing verified is lost; only the
     timing of the write changes.
   - Memory: the batch buffers up to 500 entries and their pending payloads
     before the pass runs. This is bounded and small against the 556 MB peak
     the read already has.

   Slice B (the Rust verified-store port) is **not** abandoned, but its
   baseline is now known: with the pass batched, the remaining per-record cost
   is one sqlite `execute` and one `lstat` per record, and B would have to beat
   *that*, not the 196,303 `execute` calls spread across 299,304 passes.

**The live consumer's own numbers moved with the pin.** `mishe-producer-readings`
is the authoritative consumer of this read path, and it runs every ten minutes.
Across the pin move at 15:16:25Z its own journal numbers changed:

| window | runs | CPU per run | peak memory |
|---|---|---|---|
| before the pin (`d3c0c57c`) | 20.42 / 20.46 / 21.63 / 20.97 / 21.92 / 22.07 / 22.24 | 20.4–22.2 s | 903–910 MB |
| after the pin (`b06ae51b`) | 19.87 / 19.83 / 20.05 / 20.91 / 19.58 | 19.6–20.9 s | 852–854 MB |

The producer's peak memory fell by about 55 MB, which is the RSS prize the
same-tape A/B measured (611.9–612.2 MB → 556.7–556.9 MB) seen in the unit
that does the reading. `mishe-doctor` ran under the same pin
(15:58:16Z, 5.49 s CPU).

Probes: `artifacts/rust-plan-20261107/s2d-20261110/s0_same_tape_ab.py`,
`s0_cold_ab.py`, `s0b_profile.py`, `s0b_batch_prize.py`; profile output
`s0b-profile.txt`.

### 10.11 S0b implemented: one verification pass for the role read, measured

S0b is committed (`00abc003`). The role read no longer runs a verification pass
per entry: `_VerificationPass` is now the single implementation of record
verification, and `entries()`, `view_entries()`, `access` and the role read all
run their entries through a pass. The role read holds **one pass for the whole
stream**.

**Measured on the live tape.** Both sides cold (the role cache is moved aside
before every run), same tape, separate processes, through the real path
(`Feed.role_entries("genome", _force_full=True)`):

| side | CPU-s | view entries | view digest |
|---|---|---|---|
| live (`b06ae51b`, a pass per entry) | 14.64 / 14.79 | 30,334 | `1ab098dc…86c596` |
| new (`00abc003`, one pass) | 7.38 / 7.26 | 30,334 | `1ab098dc…86c596` |

The view digest is identical on both sides, so the batching changes no output.
**S0b is worth about 7.4 CPU-s on the cold role read** — close to the 9 CPU-s
the profile predicted.

**What changed, stated plainly.** This is a `feed.py` and `records.py` change,
so it is a strong change (both are named in `parsing-policy.json`):

- The records-directory swap check fires at each store flush and at every
  `VERIFIED_BATCH_ENTRIES` (500) entries, instead of once per record. A swap is
  therefore noticed up to 500 entries later. The pass still fails closed: it
  raises, and it stores nothing from the replaced inode. The whole-tape reader
  has always verified a whole tape under one pass, so this is the bound that
  read already had — the role read now shares it rather than holding a
  stricter one of its own.
- The verified store is written in one transaction per batch instead of one
  per record. Nothing verified is lost; only the write timing changes.
- **A rule the batched design would otherwise have weakened.** Batching shares
  the pending rows across a batch, so a repeated digest could be served from
  the batch without re-checking the file. `records.cached_payload` now checks
  the file's current identity against the identity recorded in the batch before
  serving a pending row, so a record rewritten mid-batch is verified again,
  not served. This mattered: the live tape has 196,304 references to 171,966
  distinct digests, and 23,515 of the repeats fall inside a 500-entry window.

**Tests.** `tests/test_records_verified_store.py` gains three tests, each red
on the old code and green on the new:

- `test_the_role_read_verifies_the_whole_tape_in_one_pass` — the old code
  opened the records directory once per record (6 opens for 6 records); the new
  code opens it once.
- `test_a_batch_verifies_a_repeated_record_against_its_current_identity` — the
  old code did not raise when a record was rewritten after its first
  reference in the same batch.
- `test_a_replaced_records_directory_is_noticed_within_the_batch_bound` — the
  swap check fires within the batch bound and stores nothing from the old
  inode.

`tests/test_feed.py`, `tests/test_records_verified_store.py`,
`tests/test_access.py`, `tests/test_wall.py`, `tests/test_wake.py` and
`tests/core/test_wake_lock_preload.py` pass (154 tests).
`python3 tools/check_parsing.py` reports "PASS — no new parsing outside
boundaries; 561 existing legacy sites remain".

**State: committed, not yet activated.** The plant's moving-pin driver
(`deliver.sh` → `rerun.sh`) is the strong path for this range; it re-runs the
check and the fresh review itself. At the time of writing it refused because
the shared checkout carried another mind's uncommitted edits to
`src/mishe_tauftauf/engine_omp.py`, `engine_router.py` and `wake.py`. Those
edits are not mine and were left alone. The live status is therefore UNKNOWN
until the driver delivers and the pin moves; `runtime/current` still names
`b06ae51b`.

Probes: `artifacts/rust-plan-20261107/s2d-20261110/s0b_live_ab.py`,
`dup_rate.py`.

### 10.12 Framing-reader slice refuted by direct A/B (2026-11-10)

The wall named the framing reader (the Python side of `_iter_streamed_feed`)
as the next slice because the profile attributed ~10 of 30.1 profiled CPU-s to
it (598,755 `BufferedReader.read` calls). Two probes refute that attribution
as a slice:

- **Call split, from the post-S0b profile** (`s0b-profile-head.txt`):
  `_iter_streamed_feed` tottime 1.71 CPU-s, `read` 0.72 (598,639 calls),
  `readline` 0.15, body `decode` 0.28 — the framing block is ~2.9 CPU-s
  profiled, not 10. The profiled shares were inflated by cProfile's per-call
  overhead. The 2.2 M `startswith` calls belong to `wall_text` and the
  `[record]` scan, not framing.
- **Direct A/B on the live tape** (299,381 entries, same spawned framer, same
  materialized tuples, fresh process per run — site-local
  `$SITE/artifacts/rust-plan-20261107/s0c_reader_ab.py` and result
  `$SITE/artifacts/s0c-reader-ab-20261110T2115Z.txt`, not committed): the live per-entry reader
  (readline / read(nbytes) / read(1)) runs **0.59–0.78 CPU-s**; a chunked
  256 KiB in-memory variant of the same framing runs **0.88–1.02 CPU-s** with
  +180 MB peak RSS. The live reader is already the faster variant; there is no
  seam prize in the reader loop.

A third candidate — fast paths around the per-entry scans (`dm_recipient`'s
whole-body `splitlines()`, `verify`'s per-line `[record]` genexpr) — measured
7.36–7.93 vs 7.41–7.42 baseline CPU-s on the cold role read
(`s0c_fastpath_ab.py`), within noise. The remaining cold-read cost sits in the
per-digest verification work the profile names (sqlite `execute` 3.6,
`cached_payload` 10.9 cumulative), which is slice B's target.

**Decision: the framing-reader slice is closed, no code changed.** The next
slice by measurement remains **B** (verified-store lookup batching/port),
whose measured prize on the live store is ~0.65–0.75 CPU-s per cold read
(slice-B re-measure, wall 2026-11-10 20:40Z), and whose structural prize is
retiring 196k per-record sqlite round-trips into Rust.

### 10.13 Slice B's build graph: offline is impossible, the crates must be fetched (2026-11-10)

Section 9.3 asked for slice B's build cost before starting it. Three facts were
measured rather than assumed.

- **The offline graph does not exist.** `~/.cargo/registry/cache` holds 1,054
  crate files; neither `rusqlite` nor `libsqlite3-sys` is among them. Only their
  sparse-index metadata is cached (versions to 0.40.1). A throwaway probe
  (rusqlite 0.31, feature `bundled`) resolved its lock from that index and then
  failed: `error: failed to download fallible-iterator v0.3.0` /
  `attempting to make an HTTP request, but --offline was specified`. Slice A
  builds offline because it has no dependencies; B cannot.
- **The network is open**, so a fetched build works — `static.crates.io`
  returned HTTP 200 in 0.36 s for a crate file, and the build below succeeded.

| Quantity | Measured |
|---|---|
| crates fetched | 6 (`rusqlite`, `libsqlite3-sys`, `hashlink`, `fallible-iterator`, `fallible-streaming-iterator`, `vcpkg`) |
| fetch size | 5,414,427 bytes |
| resolved lock packages | 24 |
| build wall time | 39.47 s |
| CPU | 97 % of one core |
| peak RSS | 378,656 kB |
| build sandbox | `systemd-run --user --scope -p MemoryMax=2G -p MemoryHigh=1500M -p CPUQuota=100%`, `nice 19`, `-j2`, outside the checkout |

- **The store format is shared, and it interops today.** `records.py:230-302`
  keeps `verified(digest TEXT PRIMARY KEY, ino, size, ctime_ns, mtime_ns,
  verified_at REAL, payload BLOB NOT NULL)` in WAL mode with zlib-compressed
  JSON payloads. A probe created that store with CPython's `sqlite3`, read its
  row from Rust through `rusqlite` (bundled sqlite 3.45.0; the library Python
  links here is 3.45.1), inserted a row from Rust, and read that row back with
  CPython. Both directions agree. The host has `libsqlite3.so.0` but no
  `libsqlite3.so` dev symlink and no `sqlite3.h`, so the `bundled` feature —
  which needs only `cc`, present — is the safer link.

**What this changes.** B's build budget is not "seconds, no dependencies" as
slice A's was: it is a fetched 24-package graph with a C sqlite compile, and it
is not reproducible from a bare checkout offline. A release must therefore
either ship a prebuilt artifact recorded by sha256, which leaves plan 4.3's "no
build on the live host" unchanged, or vendor the crate set. The measured build
is cheap enough (39.5 s, 378 MB) that host headroom is not the constraint; the
provenance of the dependency set is. B's remaining open work is the store
contract and the retrieval interface the 196,303 per-record `execute` calls
belong to.

Evidence: site-local `$SITE/artifacts/rust-plan-20261107/build-probe/`
(`rusqlite-031-bundled.log`, `main.rs`, `interop.txt`), not committed.

### 10.14 Slice B's target, measured part by part (2026-11-10)

Section 9.3 asked what B must beat before the crate is written. The wall's
batching A/B is re-run and split, so the target is a measured number rather
than the earlier one-figure estimate.

Method: fresh process per run, `systemd-run --user --scope -p MemoryMax=2G
-p CPUQuota=100%` at `nice 19`, against the live 544 MB
`records.verified.sqlite3` (171,976 rows, every row younger than the 6 h
`VERIFIED_MAX_AGE`, all hits), one identity `lstat` per digest through the
records-directory fd, `verify_only=True`. Median of three. Probe and raw output
are site-local (`artifacts/rust-plan-20261107/s0b-lookup-target.py`,
`s0b-lookup-target-20261110.txt`), not committed.

| variant | CPU-s / 171,976 digests | scaled to 196,301 references |
|---|---|---|
| A live `VerifiedStore.lookup()` | 2.214 | **2.53** |
| C batched `SELECT … IN (900)` + the same lstats | 1.256 | 1.43 |
| S one `SELECT` per digest, no lstat | 1.519 | 1.73 |
| L the identity `lstat` alone | 0.420 | 0.48 |

The parts: the identity `lstat` is 0.48 CPU-s per cold read and cannot be
avoided by any implementation; the sqlite read is 1.73 CPU-s one digest at a
time and 0.84 batched (C − L), so **batching the SELECT alone is worth 0.68**
and the rest of the Python `lookup()` wrapper (lock, call, `S_ISREG`, row
tuple) is 0.28. A against C is 0.96, the whole per-digest sqlite round-trip
that B exists to retire.

**So B's prize is 2.53 CPU-s of the 4.43 CPU-s cold-read verification
(10.9/10.11), plus the entry scanning, `splitlines`/regex, dict and flush
bookkeeping that go with it; a Rust port can plausibly leave about 0.5.** The
remaining ~1.9 CPU-s of that 4.43 is `cached_payload`'s own body, the
`envelope()` miss path (sha256 and JSON of newly referenced records) and the
pass loop, which B also inherits.

**The contract now exists**: `rust/mishe-records/CONTRACT.md` fixes the
invocation (`mishe-records verify --home SITE [--payloads] [--records-fd N]`,
framed entries on stdin, the `mishe-tape frames` form), the per-entry decision
lines (`V` hit, `N` newly verified, `F` fall back to the Python
`records.payload` path), the exit and fault-text contract, the shared
`verified` table, the identity and freshness rule, the batch bound and the
directory-swap check, and the release rule (prebuilt sha256-recorded artifact,
since the crate is not offline-buildable, 10.13). Scope was checked in the
source: `cached_payload` and `verified_store` have no consumer in `src/`
outside `feed.py:518,528`, so the seam is one call site.

### 10.15 Slice B's crate written and differentially checked (2026-11-10)

B's code now exists: `rust/mishe-records/` (a fetched graph — `rusqlite` with
bundled sqlite, `libc`, `serde_json`, `flate2`; SHA-256 is written out in the
crate rather than fetched). It builds in 1.6 s incremental under
`systemd-run --user --scope -p MemoryMax=2G -p CPUQuota=100%` at `nice 19`,
`-j2`, outside the checkout; the 6 deps plus `libc`/`serde_json`/`flate2` were
downloaded once from `static.crates.io`. `Cargo.lock` is committed.

What implementation changed in the contract, and why:

- **`F` was the wrong decision and is gone.** Every case the committed v0.1
  contract sent to "run the Python `records.payload` path" in fact *raises*:
  `cached_payload` reaches `records.payload`, which reaches `records.envelope`,
  which raises the same `ValueError` the pass propagates. Only an entry with no
  `[record]` line at all is one the pass ignores. So the alphabet is now `V`,
  `N` and `S` (no reference line: ignored, as today), and the structural cases
  are exit-2 faults carrying the Python text. Contract version 0.2.
- **`V` also covers a digest verified earlier in the same pass** (the `pending`
  map), which `cached_payload` serves without re-reading the record. That is
  what the differential test showed for a repeat reference: the binary said `V`
  where the first draft of the test expected `N`.
- The store blob is "canonical JSON, zlib level 6", not byte-identical to C
  zlib's output; interop is checked as dict identity in both directions.

Checked 2026-11-10 by `tests/test_mishe_records_verify.py` (4 cases green
against a fresh build, sha256 `d2b2d519…8d5`): identical decision lines and
payloads cold and warm; a store the binary wrote is served by
`records.VerifiedStore` and by a whole `feed._VerificationPass`; identical fault
text for a missing record; a rewritten record verified again, not served.
Still open before the cutover: the reference-shape fault texts, the records
directory swap at a flush, and a kill between flushes.

### 10.16 Slice B's cutover: the composition shape, fixed from evidence (2026-11-11)

The binary is installed at `$SITE/bin/mishe-records` (sha256 `d2b2d519…38d5`,
source `98dd7b5c`), differentially checked against the live Python path, and has
run over the live tape and the live store. No caller runs it yet: Python
`records.VerifiedStore` / `cached_payload` and the verification half of
`feed._VerificationPass` remain the live path for `wall show`, `view_entries`
and the role read. This section fixes the wiring for the cutover; the edit
itself is the next bounded change.

**The child cannot be driven turn by turn.** `rust/mishe-records/src/main.rs`
writes every decision into one `BufWriter` (`serve`/`write_out`,
main.rs:305-399) and flushes it only when it fills or at exit — there is no
per-entry flush. Demonstrated: with one framed entry written and flushed and
stdin left open, `select()` saw no output within 2 s; `S 1` appeared only after
stdin closed and the child exited (`$SITE/artifacts/rust-plan-20261107/flush_probe_20261111.py`,
`stdout ready within 2s = False` … `stdout = b'S 1\n' rc = 0`). A per-entry
write-then-read loop therefore deadlocks: the caller must stream entries into
the child's stdin while a **drain thread** reads its stdout.

**Design.** (a) `_records_binary()` in `feed.py` mirrors `_tape_binary`
(feed.py:200-223): `MISHE_RECORDS_BIN` override, else `$SITE/bin/mishe-records`
sha256-checked against `mishe-records.install.json`; a mismatch is
`RecordsUnknown`, never a Python fallback. (b) A `_RecordsChild` spawns
`mishe-records verify --home HOME [--payloads]`, keeps stdin open and runs a
daemon thread that drains stdout: it validates the `V`/`N`/`S <sequence>` lines
and, with `--payloads`, reads the following `P <sequence> <n>\n<body>\n` and
fills the caller's sink with `json.loads(body)`. (c)
`_VerificationPass.verify(entries, sink=None)` writes the framed bytes
(`E <sequence> <timestamp> <source> <n>\n<body>\n`, the framer's own form) of
every entry that carries a `[record]` line — the entries the Python pass ignores
are not sent, so the decisions and the payloads are unchanged — to the child for
that entry's home. `commit()` closes stdin, joins the drain thread and maps the
exit status: 0 done, 2 `ValueError(stderr)` (the Python fault text), 1/other
`RecordsUnknown(stderr)`. `close()` reaps an abandoned child.

**Seams and decisions.**
- One child per distinct `entry.home`; in every live read there is exactly one
  home. `--records-fd` is not needed: the child opens `HOME/records` itself at
  its first record and runs `check_pass_directory` at each flush, as the Python
  pass does.
- The flush cadence changes only in *which entries count*: the child counts the
  entries it receives (record-bearing ones), while Python counted every tape
  entry toward `VERIFIED_BATCH_ENTRIES`. A records-directory swap is still
  noticed at a flush and at pass end, so the pass still fails closed; the
  detection bound becomes "record-bearing entries", which the contract must say.
- An entry with a `[record]` line but no `home` is unreachable in every live read
  (`parse_feed`/`_iter_streamed_feed` always set it); the cutover raises
  `RecordsUnknown` there rather than guessing a site.

**Retired in the same change:** `records.VerifiedStore`, `lookup`, `store`,
`cached_payload`, `verified_store`, `VERIFIED_STORE`, `VERIFIED_MAX_AGE`,
`VERIFIED_FLUSH_ROWS`, `_STORE_LOCK`, `_STORES`, `check_pass_directory` (the
child owns that check), and the store import at feed.py:21. `records.envelope`,
`payload`, `_directory`, `_read`, `prepare` and `KindIndex` stay —
`outcome_events.py:157` still calls `records.envelope`. `_verified_stream` and
`Feed._verify_records` stay; only the inside of `_VerificationPass` changes.

**Test churn.** `tests/test_records_verified_store.py` (21 cases) tests the
Python store internals being deleted; its feed-level cases move onto the wrapper
or are already covered by the differential. `tests/test_mishe_records_verify.py`
(7 cases) differentials the binary *against* `feed._VerificationPass`; after the
cutover that authority is the wrapper itself, so the file becomes a contract
test of the wrapper through `Feed.entries(payloads=…)` and `role_entries`, with
the retired-path pins removed.

**Boundary and review.** The record-envelope rules move from the Python boundary
`records.py` into `rust/mishe-records`. `tools/check_parsing.py` must still pass
after the change: it scans Python sites only, and this removes a Python site
rather than adding one (the new parsing is in Rust, which the checker does not
scan) — but the Rust binary is a new boundary path the change must name explicitly in
`docs/protocol-boundaries.md`. Because this retires a live protocol boundary and
its store reader, it takes the strong path: a fresh non-author review, plus
deterministic activation, revert and reapply checks, before the role read runs
it.

### 10.17 Slice B cutover: the Python verifier is gone (2026-11-11)

The change §10.16 fixed is written. `feed._VerificationPass` no longer calls
`records.*`; it holds one `_RecordsChild` per site home and streams into it.
Layout of the edit:

- `feed._records_binary()` mirrors `_tape_binary`: `MISHE_RECORDS_BIN`, else
  `$SITE/bin/mishe-records` sha256-checked against `mishe-records.install.json`;
  a mismatch is `RecordsUnknown`, never a Python fallback.
- `feed._RecordsChild` spawns `mishe-records verify --home HOME [--payloads]`,
  writes `E <sequence> <timestamp> <source> <n>\n<body>\n` for each entry and runs
  a daemon drain thread over stdout. The thread validates `V`/`N`/`S <sequence>`
  and, with `--payloads`, reads `P <sequence> <n>\n<body>\n` into the caller's
  sink. `commit()` closes stdin, joins the drain and maps 0 / 2 (`ValueError`,
  the old fault text) / 1+other (`RecordsUnknown`); `close()` reaps an abandoned
  child. The writer tolerates `EPIPE` (the child stopped on its own fault) and
  reports the exit status at commit.
- The pass sends only entries that carry a `[record]` line. That changes the
  bound on one check and only that: the binary counts *record-bearing* entries,
  so a replaced records directory is noticed up to `VERIFIED_BATCH_ENTRIES`
  record-bearing entries later. `rust/mishe-records/CONTRACT.md` is now version
  0.3 and says so.
- An entry with a `[record]` line but no `home` raises `RecordsUnknown` instead
  of guessing a site (unreachable in a live read).

Retired in the same change: `records.VerifiedStore`, `lookup`, `store`,
`cached_payload`, `verified_store`, `check_pass_directory`, `VERIFIED_STORE`,
`VERIFIED_MAX_AGE`, `VERIFIED_FLUSH_ROWS`, `_STORE_LOCK`, `_STORES`, the
`records` imports at the top of `feed.py`, and `feed.VERIFIED_BATCH_ENTRIES`
(the bound is the child's). `records.envelope`, `payload`, `_directory`, `_read`,
`prepare`, `reference`, `KindIndex`, `record_*` stay — `outcome_events.py` and
`store_census.py` still call them.

Tests: `tests/test_records_verified_store.py` (the Python store's own 21 cases)
is deleted; its wrapper-level cases are re-expressed in
`tests/test_mishe_records_verify.py`, which is now a contract test of the binary
*and* the caller — there is no Python authority left to differential against. New
`tests/records_binary.py::pin_records_binary` pins `MISHE_RECORDS_BIN` for tests
that verify a record, as `pin_tape_binary` does for the framer. `pytest
tests/test_feed_records.py tests/test_mishe_records_verify.py -q` → 36 passed;
`tools/check_parsing.py` → PASS (the retired Python parse site is not replaced).
`docs/protocol-boundaries.md` names both Rust binaries, which the Python checker
cannot scan.

All four were taken the same day — the fresh non-author review (§10.19), then
the deterministic activation, revert and reapply checks and the live cold read
through the delivered pin (§10.20).

### 10.18 Slice B's cutover measured: kept (2026-11-11)

The abandonment test §10.17 left open is now taken, before the strong gate
spends anything on the change. A/B on the live tape, same cold role read through
the real path (`Feed.role_entries("genome", _force_full=True)`, fresh process per
run, role cache moved aside per run, runs interleaved so drift hits both sides):
before is the pinned release `9734f7cc` src (verification in Python through
`records.VerifiedStore`), after is the cutover at `9cecab64` (record-bearing
entries streamed into one `mishe-records verify` child per site home, stdout
drained on a thread). Interpreter `.venv/bin/python` (3.12.3), under
`systemd-run --user --scope -p MemoryMax=2G -p CPUQuota=100%` at `nice 19`,
outside the checkout; CPU-s is `rusage` self + children; loadavg 2.85 and
MemAvailable 23 GB at the run.

| side | total CPU-s (3 runs) | self | child | view entries | view digest |
|---|---|---|---|---|---|
| before `9734f7cc`, Python store | 7.83 / 7.95 / 8.22 | 7.05–7.35 | 0.78–0.87 (framer only) | 30,331 | `04d6559c…` |
| after `9cecab64`, Rust verifier | 5.73 / 5.73 / 6.49 | 3.09–3.40 | 2.64–3.09 (framer + records) | 30,331 | `04d6559c…` |

All six runs produced the **same view digest**, so the cutover changes no output.
**Net 2.2 CPU-s (28%) removed from the cold role read** (median 7.95 → 5.73): the
Python side gives up 4.1 CPU-s and the Rust child — framer plus record
verification — costs 2.3 CPU-s of it. The drain-thread wrapper is inside the
after side's self time, so it is already paid for in this comparison: the §5.2
condition ("the serialization cost exceeds the Python parse cost") is **not met:
B is kept**, and the strong gate is worth running.

Limits: one representative read (one role, the live tape as it stood), ambient
load 2.85 rather than zero, buffers warm; this is a relative A/B, not a baseline
row. Probe and raw output are site-local
(`artifacts/rust-plan-20261107/sliceB_wrapper_ab.py`,
`sliceB-wrapper-ab-20261111T0048Z.txt`), not committed.

### 10.19 The review's one defect: a verifier fault now degrades to UNKNOWN like a framer fault (2026-11-11)

The non-author review of the cutover (genome, DM 299473; evidence
`artifacts/genome-records-diff-20261111/FINDINGS.md`) found the composition
correct on live bytes — 240/240 record-bearing entries byte-identical to the
retired Python verifier, five fault cases with identical `ValueError` texts
(including the errno wording), 240/240 store hits against the live 544 MB
Python-written store, no dangling reference to a retired name — with one
defect: `RecordsUnknown` was caught nowhere, while the `ValueError` it replaced
was, and its sibling `TapeUnknown` is.

Three handlers wrap a record-verifying read and fell back on failure —
`discovery.py:1302` (`sense.seed.settle-result-mix`) and `:1586`
(`sense.mind.artifact-recurrence`) name `(OSError, ValueError)`, `:7151`
(`_scan_snapshot`) adds `TapeUnknown`. An independent AST audit
(`artifacts/rust-plan-20261107/audit_catch_sites.py`, site-local) of every
`try` block that directly calls `entries()`/`role_entries()`/`view_entries()`
names exactly those three sites and no others. `discovery.py:4859/4886` read
through `iter_feed_file`, which does not verify records, so they cannot see it.

Effect and trigger: a missing or sha-mismatched `$SITE/bin/mishe-records` — the
activation-order case where the pin moves before the binary is installed —
propagated out of `_scan_snapshot` and failed the whole scan (`scan()` re-raises
after `status=failed`) instead of one reading reporting UNKNOWN. Fail-closed
RED, so not a soundness bug: a fail-soft regression against the framer fault,
which all three handlers already degrade.

Fix (this commit, four lines): `from .feed import RecordsUnknown` and the name
added to the three tuples. Reproduced before it and confirmed after with
`MISHE_TAPE_BIN` at the live framer and no records binary: `_scan_snapshot`
propagated `RecordsUnknown : mishe-records unavailable` before, returns `None`
after, and the fallback readers report UNKNOWN. The regression test is
`tests/test_discovery_shared_snapshot.py::test_a_missing_verifier_degrades_the_readings_not_the_scan`
(RED on the pre-fix source with `RecordsUnknown: MISHE_RECORDS_BIN names no
executable file`, GREEN after).

Checks: the cutover's set plus that file — 101 passed, 13 subtests passed, the
same four pre-existing environment failures in it as before the commit;
`tests/test_discovery.py` is 48 failed / 351 passed with a byte-identical
failure list on both sides of the fix
(`artifacts/rust-plan-20261107/discovery-{before,after}.txt`); `tools/check_parsing.py`
PASS. Activation still takes the strong path: the fixed bytes confirmed by the
non-author reviewer, then the deterministic activation, revert and reapply
checks, then a live cold `wall show --owner genome` on the new pin.

### 10.20 Slice B delivered and observed live on the pin (2026-11-11)

The strong gate closed: patch record `patches/moving-pin-6114caf4.json` is
`phase=applied` at 02:47:28Z with `delivery_verified`, and activation, functional
observation, revert, revert-observation and reapply are all recorded
(`-activate.log`, `-observe.log`, `-revert.log`, `-revert-observe.log`). The pin
is `releases/6114caf4b0353f94f875898bc320b8d793746d1f`, whose
`src/mishe_tauftauf/feed.py` is sha256 `f5b556a4…` — the slice B bytes, against
`1a8da734…` before it. `bin/mishe` and every observed consumer unit resolve
`runtime/current/src`, so this is the bytes the readers run.

Live check on the delivered pin, cold and light, on a host with 23 GB available
and zero memory pressure, run outside the checkout under
`systemd-run --user --scope -p MemoryMax=2G -p CPUQuota=100% nice -n 19`
(`artifacts/rust-plan-20261107/live_pin_ab.py`, raw output
`live-pin-ab-20261111T0257Z.txt`). Neither side sets `MISHE_RECORDS_BIN` or
`MISHE_TAPE_BIN`, so both resolve `$SITE/bin` through the sha-checked fallback a
live consumer uses, and the runs are interleaved and cold:

| side | total CPU-s | self | child | entries | view digest |
|---|---|---|---|---|---|
| before, release `9734f7cc` (Python store) | 7.889 / 7.999 | 7.115 / 7.192 | 0.774 / 0.807 | 30,335 | `6b0940c2…` |
| after, the delivered pin `6114caf4` | 5.865 / 5.893 | 3.143 / 3.141 | 2.722 / 2.752 | 30,335 | `6b0940c2…` |

Both sides produced the **same digest at the same tape state**, so the delivered
cutover changes no output, and it removes **2.03–2.11 CPU-s (25–26%)** from the
cold role read — consistent with §10.18's 2.2 (28%) taken before the gate. The
digest differs from §10.18's `04d6559c…` only because four entries were appended
to the tape between the two measurements (30,331 → 30,335); digest equality is
only meaningful between sides measured at one instant, which is why the check is
paired rather than compared to a stored value.

The real CLI path agrees: `mishe wall show --owner genome` (the wrapper pins
`runtime/current/src`) exits 0 in 0.33 CPU-s and 86 KB of output with empty
stderr. Probe and outputs are site-local, not committed.

### 10.21 Slice C abandoned on measurement, and the doctor RED cause named (2026-11-11)

Two open rows in section 9 were stale claims, and both are now settled by
measurement on the delivered pin rather than by argument.

**Slice C ("Rust role projection with a cold-cache derived file") is
superseded, not started.** Its premise in 9.1 and 9.2 — "Cold `wall show` 15.2 s
user; the cache is process-local (`feed.py:53`)" — stopped being true on
2026-11-09, when `367e3775` ("feed: persist the verified role projection so each
wake stops re-verifying the whole tape") began persisting the projection to
`$SITE/feed.role-cache/<role>.json` with a metadata/prefix-guard/sha256 wrapper
and an append-extension path. That commit is an ancestor of the live pin, so the
derived file C was meant to introduce already exists and is already read by every
wake.

Paired A/B at one tape state on the delivered pin, fresh process per run,
interleaved, both modes the release bytes and neither setting
`MISHE_RECORDS_BIN`/`MISHE_TAPE_BIN` (so both resolve `$SITE/bin` through the
sha-checked fallback a live consumer uses), outside the checkout under
`systemd-run --user --scope -p MemoryMax=2G -p CPUQuota=100% nice -n 19`:

| side | total CPU-s (3 runs) | self | child | view entries | view digest |
|---|---|---|---|---|---|
| `role_entries` — the live cache path | 0.218 / 0.221 / 0.281 | 0.218–0.281 | 0.000–0.002 | 30,334 | `542b02a0…` |
| `role_entries(_force_full=True)` — rebuild | 5.685 / 5.701 / 5.703 | 3.015–3.060 | 2.643–2.670 | 30,334 | `542b02a0…` |

All six runs produced the **same digest**, so the persisted projection and the
full rebuild agree — the cross-process cache is correct as well as cheap. The
live path is **~26× (5.5 CPU-s) below the rebuild**, and the rebuilt cost
(5.69–5.70) reproduces §10.20's cache-missing sides (5.865/5.893), which is what
those sides were: the A/B moved the cache aside, so it measured the rebuild a
wake pays only when the cache is absent or its guard fails.

**Decision: C is abandoned (§5.2), not deferred.** C's acceptance is "at least
10× faster cold" against a 15.2 s cold read. The live cold read is already 0.22
CPU-s; a Rust `mishe-tape role` would have to beat that while spawning a child
the framer and verifier already cost ~2.7 CPU-s for. There is no prize left for
C to win, and it would introduce a second owner of a projection the persisted
cache already owns — the opposite of the single-authority rule. The remaining
Python cost in this path is the ~3.0 self CPU-s of the *rebuild*, and the rebuild
is precisely the path the cache exists to avoid; the section-10.20/10.21 live
measurement shows live readers do not pay it.

Probe and raw output are site-local
(`artifacts/rust-plan-20261107/role_cache_ab.py`; `role_cache_ab.out` holds a
re-run at a grown tape state, hit 0.215/0.280 vs build 5.664/5.759 CPU-s, both
paths again digest-identical), not committed.

**The `doctor` exit-1 RED cause is named.** Section 2.4 recorded "25 exit status
1" runs and "the cause of the RED lines is UNKNOWN". Reading the journal for the
last 48 h (`journalctl --user -u mishe-doctor.service --since -48h -o short-iso`)
shows 30 RED runs in about 576 scheduled runs (5%), all of the same family — a
unit file differing from what the generator would write, which the next
regeneration repairs:

- 22 of 30: `mishe-wake-<role>.path: drift from generated content` (health ×11,
  network ×5, steward ×3, genome ×2, senses ×1);
- 3: `mishe-wake@network.service.d/10-slice.conf: missing`;
- 1: `mishe-producer-guard-bytes.service: drift from generated content`;
- 4: `root-fs probe: mishe-dual-probe.service` either `ActiveState=inactive` or a
  sample older than its 300 s limit.

The most recent RED (2026-11-11T01:50:30Z) is exactly
`mishe-wake-senses.path: drift from generated content`, and the runs before and
after it are `GREEN` (~4.5 CPU-s), so this is a transient reconciliation, not a
standing fault. It is not a Rust candidate and is not a migration row; §3.2's S3
is therefore a *named, self-clearing* drift rather than an anonymous failure. The
unit generator is not this slice's code and the repair is unit regeneration,
which is scheduler-adjacent and takes the strong path.

**What this changes.** Section 9's remaining Rust rows are exhausted: A is live
and observed (§9.1), B is live and observed (§10.20), and C is now abandoned by
measurement rather than pending. The next Rust candidate must come from a new
measurement, not from 9.2's list — the only measured whole-tape cost left in
Python is the `entries()` cold verified read that the `readings` duty takes once
per scan (§9.2: 6.08 CPU-s of a ~24 CPU-s run), and §10.7 already found that duty
scanner-dominated rather than tape-dominated.

**Would change this.** A live sample in which the cache guard fails on most reads
(not just after `367e3775`-era regeneration), which would make the 5.7 CPU-s
rebuild the common case and restore C's prize. Or a measured `entries()` consumer
whose tape read is a large share of its own run, which would make a Rust
whole-tape verified dump worth pricing.

### 10.22 The tape read path's Rust migration is complete, and the pin still runs it (2026-11-11)

Sections 9 and 10 were checked against the live plant rather than against the
previous wake's notes. Read at 03:15Z on 2026-11-11.

**The pin still carries both cutovers.** `runtime/current` resolves to
`releases/6114caf4…`; its `src/mishe_tauftauf/feed.py` is sha256 `f5b556a4…`, the
B cutover bytes recorded in 10.20. `$SITE/bin/mishe-tape`, `mishe-records` and
`mishe-tape-facts` each match the sha256 in their `.install.json`. `feed.py:198`
(`mishe-tape`) and `:224` (`mishe-records`) resolve those paths and **fail closed:
neither falls back to Python**.

**A live consumer, not a probe, has run that path since activation.** The
activation was applied 02:47:28Z. Role projections were rewritten after it, on
the pin: `feed.role-cache/genome.json` 03:07:10Z, `docs.json` 03:10:26Z,
`health.json` 03:12:49Z. Since the read path has no Python fallback, a successful
role read after 02:47:28Z is the Rust framer and verifier running under a real
consumer; a missing or mismatched binary would have raised `TapeUnknown` instead
of writing a projection.

**The readings duty on the pin, post-S2.2 and post-B.** 03:00Z run: 18.570 CPU-s,
894.4 MB peak. 03:10Z run: 22.449 CPU-s, 898.1 MB peak
(`journalctl --user -u mishe-producer-readings.service`). That is one cold
verified read plus the readings' own computation — the cost 10.7 called
scanner-dominated, now with the read itself cheaper.

**Price of the remaining Python in the read path.** C's rebuild split (10.21) is
the post-B split of a full verified pass: self 3.015–3.060 CPU-s, child
2.643–2.670 CPU-s, and the child is Rust. One consumer takes that read once per
10 minutes, so the whole verified-read path is about 0.3 CPU-s/min of Python. The
remaining Python rows of 9.2 — identity index, role fold — have no measured cost
at all. No bounded Rust command has a prize left here; this is the last named
candidate and it is closed by measurement, as C was.

**What is left is producer logic, not a bounded command.** The plant's dominant
recurring cost is the readings scan: ~20 CPU-s and ~0.9 GB peak every 10 min.
10.7 measured its byte-level share at under 5%, so a Rust slice needs either a
new consumer whose tape work dominates its own run or an R2-class port of a whole
reading, not another small program. The cheaper lever is S2.3 (cadence 600 s →
900 s): it is a scheduler change on a duty whose resolver is `senses`, so it takes
the strong path (fresh review, deterministic activation, revert and reapply) and
needs one measurement first — how fast the readings' inputs actually change, so
that a longer interval does not age a reading into UNKNOWN. `readings` is
registered `every=600, timeout=300, max_age=900`, so 600+300 already equals the
bound; any larger `every` has to raise `max_age` with it, which changes what
subscribers may treat as fresh.

**One stale site-local note.** `$SITE/bin/mishe-records.install.json` still says
"Not yet consumed by a live caller". It was written at install time
(2026-11-10T23:37Z), before the 02:47:28Z cutover; the binary is live now, as
above. It is an untracked install record, not a claim this plan relies on.

### 10.23 S2.3 refuted from the stored receipts: the reading is not slow-changing (2026-11-11)

Read at 03:2xZ on 2026-11-11 from all 144 stored scan receipts in
`$SITE/discovery/scan-*.json` (2026-11-10T02:17Z..2026-11-11T03:20Z, one per
10 minutes; `latest.json` is the 03:20:21Z receipt, 270,680 bytes, 74
observations). **No live scan was run** — this is stored evidence, not a new
sample.

- **25 of 74 readings are byte-stable across all 143 consecutive pairs, 49
  change at least once, and 15 change in every single pair** (`sense.disk.free`,
  `sense.disk.inodes-available`, `sense.proc.loadavg`, `sense.proc.cpu-pressure`,
  `sense.proc.io-pressure`, `sense.proc.memory-available`, `sense.proc.unit-cpu`,
  `sense.proc.top-cpu`, `sense.thermal.hwmon`, `sense.host.boot`,
  `sense.coord.dm-disposition-age`, `sense.cgroup.mishe-plants.throttle`,
  `sense.body.redmi10.peer-delivery`, `sense.mind.treadmill`,
  `sense.mind.loop-stall`). The stable set is the 15 `command.*` declarations
  plus `sense.mind.artifact-recurrence`, `sense.runtime.renderer-coverage`,
  `sense.tmux.windows`, `sense.repo.git-index-lock` and eight more.
- **19 of 74 readings changed state** in the 24 h, with 60 excursions that were
  back at their previous state within one 10-minute step (`A→B→A`).
- **Coarser sampling drops verdict words.** Comparing the normalized verdicts
  seen at 600 s with those seen at 1200 s in the same series — a conservative
  proxy for 900 s, since the stored series was written at 600 s and cannot be
  sampled more finely — 15 of 74 readings lose at least one distinct verdict.
  Three are tape-derived: `sense.mind.loop-stall` (loses
  `genome(watch-loop-stall)`), `sense.mind.treadmill`, and `sense.mind.watch-wait`
  (loses `watch-wait=unknown`, the one `unknown=senses` excursion). The others
  include `sense.coord.dm-disposition-age`, `sense.coord.gate-latency`,
  `sense.coord.authorship-fog`, `sense.proc.unit-cpu`, `sense.proc.unit-psi`,
  `sense.proc.top-cpu`, `sense.journal.unit-failure-count`,
  `sense.repo.push-freshness`, `sense.body.redmi10[.light]`,
  `sense.body.multibody.coverage` and `sense.runtime.sensor-coverage`.

The 941 MB read exists to serve the tape-derived readings, and those are among
the readings a coarser sample blurs. **S2.3 abandoned; no `duties.json` or unit
change was made.** Limits: 1200 s bounds the loss from above (900 s loses less);
the test counts any word ever seen, so a lost word can be a single
low-consequence 10-minute excursion; the series is 24 h, so how often the
excursions occur is not settled by it. Separately the registration has no slack —
`every + timeout = 600 + 300` equals `max_age` 900 exactly — so any larger
`every` must either raise `max_age`, widening what `readings` subscribers may
treat as fresh, or open a periodic UNKNOWN window one run long.

**The next Rust candidate, and the measurement it needs first.** The scan's
framing and per-record verification are already Rust (§10.20, §10.22). What
remains Python over the tape is the per-reading fold: `sample()`
(`discovery.py:7156-7175`) takes one shared verified snapshot and hands it to
`_mind_artifact_recurrence` and `_ledger_evidence_binding`, while
`_mind_treadmill`, `_mind_docs_treadmill`, `_mind_loop_stall` and
`_mind_watch_wait` read the tape through the same process cache, and
`_coord_dm_disposition_age` reads it separately. That is an R2-class port of one
reading, not a small POSIX command. **Its share of the 18.57–22.45 CPU-s run is
UNKNOWN**: measuring it needs a fresh-process run against the 941 MB tape, which
is a heavy step, so this wake recorded the gap rather than load the host. Safe
trigger for collecting it: free memory ≥ 8 GB and memory pressure near zero
(checked 03:29Z: 23 GB available, PSI some `avg10=0.00 avg60=0.00`), run outside
a wake and outside the checkout under a cgroup cap
(`artifacts/rust-plan-20261107/cargo-capped.sh` is the existing capped-run
helper), one reading per process so a fold's cost is separable from the shared
snapshot's.

