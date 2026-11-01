---
field: native-wake-recovery
angle: transfer
date: 2026-11-01
status: discarded
owner: field-review
---

# native-wake-recovery × transfer — discarded

## What we read

- Erlang/OTP Design Principles, "Supervisor Behaviour" (restart intensity), https://www.erlang.org/doc/system/sup_princ.html (fetched 2026-11-01). The page defines restart intensity as MaxR restarts within MaxT seconds ("If more than `MaxR` number of restarts occur in the last `MaxT` seconds"); when exceeded, the supervisor terminates its children and itself, and the next level up decides. Limit: this was read through a fetched-page extraction, not the raw HTML, so the wording above is the extraction's quotation and not verified byte for byte. The mechanism itself is documented on that page and does not depend on the exact phrasing.

## What this plant already does

- `DEFAULT_RETRY_LIMIT = 3` (`src/mishe_tauftauf/native_recovery.py:100`) bounds fresh attempts per wake number. `fresh_attempt` (`:489`) allocates at most `<wake>-retry-1..3`, and `attempt_disposition` (`:568`) holds the wake for reconciliation once they are used, with the attempts' own bytes as evidence (`:603-615`). That is a count bound, the per-wake analogue of OTP's MaxR.
- A transient, effect-free failure releases the busy marker only when classification is RETRYABLE (`src/mishe_tauftauf/native_resident.py:607-620`). Any dispatched or unknown attempt retains the marker and the stop path.
- The live record: `find` over the whole site tree for `*-retry-*` returns 9 directories. All 9 are canary or experiment trees (`native-canary/docs/wakes/75277-retry-{1,2,3}`, `experiments/docs-native-failure-class/trial/fresh/70040-retry-{1,2,3}`, and three worktree or artifact names that are not wake attempts). No `-retry-` attempt exists for the field-review role or any live wake.

## What is new here

OTP's restart intensity is a window over time, across children and across restarts. The plant's bound is a count per wake number. The two differ on one case: a role whose wake keeps failing transiently and effect-free across many successive wakes. Each new wake gets a fresh budget of three, so the aggregate retry rate over time is bounded only by the trigger rate, not by a rolling MaxR/MaxT.

That is the whole transfer, and the measurement does not show it happening. There is no retry directory in the live tree, so no storm has been observed. Adding a time-windowed intensity check to `native_recovery.py` now would be a new quota with no failing case behind it. The doctrine this plant runs under warns against arbitrary quotas and against changes made to look busy. The OTP mechanism also assumes a supervisor that restarts children on its own; here a new wake is a deliberate chat trigger, and each wake's hold is already visible on the dashboard.

Recorded for the future: the change becomes worth making only if the live tree starts to show `-retry-` directories across several consecutive wake numbers for one role. That is the detector. A retry storm would appear there first, and the check for it is a count of such directories per role per time window, which needs no code change to observe.

## Discard

Discarded: OTP restart intensity (MaxR within MaxT) would bound cross-wake retries, but no live `-retry-` attempt exists in the tree (9 found, all canary or experiment), so the aggregate gap is unobserved. Trigger to revisit: `-retry-` directories for one role across consecutive wake numbers in the live attempts tree.
