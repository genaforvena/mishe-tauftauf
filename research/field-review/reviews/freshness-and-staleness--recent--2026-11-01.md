---
field: freshness-and-staleness
angle: recent
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

## What we read

**On the Age of Information in Single-Server Queues with Aged Updates** — Fernando Miguelez,
Urtzi Ayesta, Josu Doncel, Maria Dolores Ugarte. arXiv:2506.19648 (submitted 24 June 2025,
revised 27 November 2025). <https://arxiv.org/abs/2506.19648>. Read as the abstract page and
the extracted model description; the full text was not re-read line by line in this turn.

The point that bears on this plant: most AoI results assume a packet enters the monitored
system with zero age. The paper analyses packets that arrive already aged, for example after
earlier delays in a multi-hop or distributed sensing path. Its result is that the average AoI
equals the standard AoI plus a correction term. When the packets' initial ages are independent
of their inter-departure times, the correction is exactly the mean initial age. When that
dependence is unknown, the authors give lower and upper bounds on the correction instead.

## What this plant already does

* **The acquisition interval is already the age origin for scans.** `src/mishe_tauftauf/scan_freshness.py`
  `age_bounds()` (lines 107–121) measures a scan receipt's age from its acquisition
  `start` endpoint, bracketed by `CLOCK_BOOTTIME` bounds, and returns a `[min, max]`
  pair. `classify_age()` then answers `recent`, `stale` or `unknown`. The module's header
  says UTC is diagnostic only. So a scan's own age is not understated by its duration.
* **The windowed AoI does not use that origin.** `aoi_summary()` in the same module
  (lines 150–233) documents its stamps as "prior publish instants … scan `created`". The
  scan receipt sets `created` at the end of `sample()` (`discovery.py:8532`), and the
  sawtooth is built from those `created` stamps in `read_scan_created_stamps()`
  (`feed.py:1063`) and `scan_window_aoi()` (`discovery.py:8913–8966`). So the windowed AoI
  takes the publish instant as the instant the information was generated.
* **The windowed AoI is display-only.** It is printed as the `aoi :` line by
  `checks/coverage-window-expiry.py:240–262`. I found no verdict that consumes it.

**Measurement, 2026-11-01, on the last 200 scan receipts in `.mishe-tauftauf/discovery/`.**
Each receipt has `created` and `acquisition.{start,end}.utc_ns`, so the initial age at
publish can be read directly:

| quantity | min | median | p95 | max |
| --- | --- | --- | --- | --- |
| `created − acquisition.start` (initial age at publish) | 30.6 s | 58.1 s | 175.9 s | 1923.2 s |
| `created − acquisition.end` | −6.6 s | −0.6 s | −0.07 s | −0.01 s |

So `created` is the end of the acquisition, not its start. Every one of the 200 receipts
already held information about 31 s or more old when it was stamped. The latest receipt's
published window (`scan_window_aoi`) reads `current=579 s`, `mean_age=337 s`, `n=99`. That
`current` is measured from `created`, so it is understated by that receipt's own
acquisition duration, around one minute at the median.

## What is new here

The paper's correction term is the thing this plant is missing. The windowed AoI makes the
Yates-style assumption that the information's age origin is the publish stamp. Here the
measured initial age is not small against the 5-second frame, nor against the 20-second
lease limit that `site_sync.py` uses for the same kind of reading. A scan that takes 30–1900 s
to acquire is not a point in time, and `created` is its end.

Two observations for the decision:

1. **The additive correction needs an independence the plant has not checked.** The paper's
   simple form (`AoI = standard AoI + mean initial age`) holds when initial age is independent
   of inter-departure times. Long scans may be exactly the ones that follow long gaps, so the
   correction would be biased in a way I have not measured. The bounded form is the honest
   fallback.
2. **Choosing the acquisition start as the age origin avoids the question.** If the sawtooth
   uses each receipt's `acquisition.start.utc_ns` as its update instant, the windowed AoI is
   measured from the same origin `age_bounds()` already uses for its upper bound (`max =
   current_upper − start_lower`). No independence assumption is needed. The price is that
   each peak now includes an acquisition duration, so the reported AoI grows. That growth is
   the error the current number hides.

## Application

target: src/mishe_tauftauf/discovery.py

1. **Take the windowed AoI's update instants from the acquisition start, not `created`.**
   In `scan_window_aoi()` and the snapshot code at `discovery.py:8625–8631`, use
   `acquisition.start.utc_ns` for each prior receipt, and for `now`. `read_scan_created_stamps()`
   in `feed.py` is the boundary adapter that yields those stamps. It needs a matching change
   with the same owner, which is why that file is named here and not in the target line.
2. **Publish the initial age beside the AoI.** Add `initial_age_median` and `initial_age_max`
   (from the window's `created − start` gaps) to the `scan_window_aoi` dict, so a reader can
   see how much of `current` came from acquisition and re-derive it from published fields.
   This follows the ledger-band rule that the second reader recomputes from published fields
   alone.
3. **Keep `aoi_summary()`'s contract.** It still takes an ordered list of stamps and returns
   the same keys. Only its caller changes what it passes in. Warmup (<2 stamps) stays `None`,
   which is `unknown`, not `recent`.

**Acceptance.** Replay the last 200 receipts from `.mishe-tauftauf/discovery/`. The new
`current` must equal the old `current` plus that receipt's `created − acquisition.start`,
to within a second. The new `mean_age` must be at least the old one. A new unit test in
`tests/test_scan_freshness.py` must show that a window whose stamps are publish instants
and one whose stamps are acquisition starts differ by the initial age. The existing
warmup test (`test_aoi_summary_warmup_is_unknown_not_fresh`) must pass unchanged. The
`aoi :` line in `coverage-window-expiry.py` must still print, now with the corrected numbers.

**Limits of this reading.** The paper models a single-server queue with a known arrival
process. The plant's scanner is a periodic sampler, so the transfer is a hypothesis, not a
verified capability. The 200-receipt sample covers one window, not the whole history. The
acquisition start is an age origin only for data the commands read live. If a sub-command
reads a cache, the true origin is older still, and this change would still understate the
age. The fix does not cover that case; it needs its own measurement.

This role is read-only with respect to the rest of the plant. The change belongs to the
owner of `src/mishe_tauftauf/discovery.py` and `feed.py` (senses). This review is the input
to that decision. Measurement and reading are complete; nothing in the plant was edited.
