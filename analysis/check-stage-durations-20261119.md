# Where the delivery check stage's wall time goes (measured 2026-11-19)

The check stage of every promotion (`tools/delivery_check.py` = cargo build +
full pytest suite) measured **1047 s** on the cb47f337 promotion — the largest
single stage of the 20.9–58.9 min promotions recorded in `duties.json`'s note.
This is the first per-test measurement of that stage.

## Measurement

`PYTHONPATH=src .venv/bin/pytest tests/ -q --durations=25` on 32a00b92,
2026-11-19 21:13Z: **5638 passed, 17 skipped, 80 subtests, 892.68 s** (the
promotion's extra ~155 s is the cargo build of unchanged crates from the shared
target cache plus pytest startup).

The top 25 tests sum to **≈ 373 s — 42% of the whole suite in 25 tests**.
Beyond the top 25 the tail is diffuse: hundreds of subprocess tests bounded at
0.5–3 s each. Trimming the tail is not worth it; the 25 are.

## The three groups worth a bounded trim

1. **Doctor feed-cycle tests, `tests/test_runtime_cli.py` ≈ 114 s across 7
   tests** (27.7, 24.5, 19.5, 12.9, 12.4, 9.2, 8.0). They run real doctor
   cycles with monkeypatched feeds and real timers
   (`FEED_VERIFY_SLICE`/`FEED_VERIFY_STALL_FACTOR` are already monkeypatched to
   2 — the sleeps are the cycle-cadence waits). These are scheduler-adjacent
   semantics; a trim must keep each cycle boundary it verifies.
2. **`tests/test_discovery.py` ≈ 111 s across 7 tests** (24.5, 20.6, 17.6,
   17.5, 13.6, 9.4, 8.2). Host sampling windows (cpu busy, io pressure, thermal
   hwmon, inode statvfs, scan evidence). The waits are real sample windows, not
   sleeps; the trim would have to shrink the sampled window, not what it reads.
3. **`tests/test_shipped_book_program_paths.py::
   test_this_repository_satisfies_the_check_it_ships` — 36.5 s alone.** It runs
   the repo's own shipped gate over the working tree. Genuine cost, and its
   verification is exactly "the whole gate passes"; do not narrow it. Worth at
   most a build-cache look at what inside the gate is slow, if ever.

The `sleep(60)`/`sleep(120)` children I suspected last wake are *not* the cost:
`run_bounded` cuts them at 1–2 s bounds, and the engine progress tests hold at
8–10 s each by design (stall-gap bounds), ~74 s across 8 tests.

## Next bounded action

One trim candidate per wake, in order of value: a doctor-cycle test whose wait
is cadence sleep rather than verified boundary, then a discovery sample window.
Keep the test's assertion intact; verify with the touched file plus
`tests/test_runtime_cli.py`/`tests/test_discovery.py` neighbors, then the full
suite only via the next promotion's check stage (its duration is the detector:
expect the 892 s baseline to drop by the trim).

## Detector

Re-run `--durations=10` after any trim and compare against the 892.68 s baseline
in this note; the promotion check-stage timestamps in the delivery records
(`at` fields of check vs observe) remain the standing end-to-end detector.
