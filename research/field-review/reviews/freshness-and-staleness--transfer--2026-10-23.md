---
field: freshness-and-staleness
angle: transfer
date: 2026-10-23
status: applied
owner: senses
target: src/mishe_tauftauf/feed.py
---

# Freshness: a sense that was detected before and reads `unknown` now is not absent — it is undetected

## What we read

Darryl I. MacKenzie, James D. Nichols, Gideon B. Lachman, Sam Droege, J. Andrew
Royle and Catherine A. Langtimm, *Estimating Site Occupancy Rates When Detection
Probabilities Are Less Than One*, **Ecology 83(8), August 2002, pp. 2248–2255**
(manuscript received 29 May 2001; revised 11 October 2001; accepted 22 October
2001). Full text read — 8 pages, §§Introduction, Methods, Notation, Basic sampling
situation, General likelihood, Extensions, Simulation study, Discussion — at

- <https://www.sfu.ca/~lmgonigl/materials-qm/papers/mackenzie-2002-2248.pdf>
  (local copy `.mishe-tauftauf/artifacts/mackenzie2002.pdf`, 165 203 bytes, PDF 1.1,
  8 pages; text extracted with `pdftotext -f N -l N` per page, 475 lines under
  `-layout`; the page footers 2248–2255 confirm PDF page == printed page, so the
  page attributions below are the printed ones).

The transferable object is **the detection history of a site**, not the
occupancy model, not the frogs. Four passages carry the transfer.

1. **The abstract's first sentence is the whole finding.** p. 2248:
   "Nondetection of a species at a site does not imply that the species is absent
   unless the probability of detection is 1." This is not a caveat about
   statistics; it is a statement about what a single observation can support.
   A passage on the next page fixes the direction of the bias:
   "For most animal sampling situations, detection of a species is indeed
   indicative of 'presence,' but nondetection of the species is not equivalent to
   absence. Thus, we expect most incidence function estimates of the proportion
   of patches occupied to be negatively biased to some unknown degree because
   species can go undetected when present." (p. 2249.)

2. **The sampling situation makes the asymmetry structural, not incidental.**
   p. 2249: "Species are never falsely detected at a site when absent, and a
   species may or may not be detected at a site when present." The plant's
   `verified`/`unavailable`/`available`/`unknown` vocabulary has the same
   asymmetry in the same direction: a read that succeeds says the thing is
   there; a read that fails says nothing at all about whether the thing is
   there.

3. **The likelihood is built from the detection history, and a site with no
   detections is explicitly a mixture, not an absence.** p. 2250: "However,
   nondetection of the species does not imply absence. Either the species was
   present and was not detected after T samples, or the species was not
   present." The site's likelihood for an all-zero history is
   `c_k · Π(1 − p_k,t) + (1 − c_k)` — the first term is *present but missed*,
   and the second is *absent*. The two are **not separable from one visit**.

4. **The paper names the number of occasions that makes the separation
   possible, and the cost of too few.** p. 2253 (Discussion): "We advise
   caution if an estimate of ĉ very close to 1 is obtained when detection
   probabilities are low (<0.15), particularly when the number of sampling
   occasions is also small (<7). In such circumstances, the level of
   information collected on species presence/absence is small, so it is
   difficult for the model to distinguish between a site where the species is
   genuinely absent and a site where the species has merely not been
   detected." And p. 2253: "Increasing the number of visits per site improves
   the precision of the estimated occupancy rate, and the resulting increase in
   information improves the accuracy of the estimate when detection
   probabilities are low."

**The limits of the source, which this review keeps.** MacKenzie et al. estimate
a *population parameter* (`c`, the proportion of sites occupied) by maximum
likelihood over many sites, under three assumptions the plant cannot adopt: the
sites are "closed" to occupancy change during the survey (p. 2249: "Sites are
occupied by the species of interest for the duration of the survey period, with
no new sites becoming occupied after surveying has begun, and no sites abandoned
before the cessation of surveying"); detections are independent across sites
(p. 2249); and `p` is modelled (constant, or a logistic function of covariates)
rather than free. The paper's own simulation says the estimates of `c` are
"reasonably unbiased" only when detection probability is 0.3 or greater with
T ≥ 5 (p. 2251), and "for low detection probabilities … ĉ tends to 1".
**What transfers is the *question* and the *history*, not the estimator.** This
review does not propose estimating occupancy. It proposes that the plant stop
reading a single non-detection as a statement about presence at all, and start
reading its own retained corpus for the history that makes the statement
decidable.

## What this plant already does

- **The plant already keeps the corpus the paper needs.** Every scan publishes a
  receipt under `SITE/discovery/scan-<stamp>-<scan_id>.json`; `scan()` writes one
  per acquisition (`discovery.py:7687-7689`). The live site retained **2 781**
  such receipts when this review was re-measured on 2026-10-23T19:3xZ — the
  corpus is a growing one, and a reader must say which instant it read — of which
  **1 793** carry an `acquisition` block, spanning **25 distinct boot ids** — a
  per-occasion detection history for every sense the plant wires, already on
  disk.
- **Every sense is probed every scan, so the "number of sampling occasions" is
  large.** Measured over the retained corpus: `sense.proc.loadavg` has **2 781**
  probes, `sense.ledger.evidence-binding` **1 695**, `sense.space.light` **1 528**,
  `sense.repo.branch-inventory` **1 676**. The plant is, in the paper's terms, a
  monitoring programme with `T` in the thousands, not a programme with `T = 2`.
- **One reader already reads the corpus, and it works — for three senses.**
  `feed.py:871` `read_scan_series(home, sense_id)` globs `scan-*.json`, keeps the
  senses whose `state == "verified"`, and returns `(timestamp, int)` tuples. It
  was added for `sense.disk.trend` (`b020925 senses: disk.trend derived rate/floor
  sense from scan corpus`) and it is the plant's only detection-history reader.
  `sense.disk.free` returns **2 781** rows from it; `sense.sys.wakeup-count`
  **2 781**; `sense.disk.inodes-available` **2 698**.
- **The plant is otherwise careful about exactly this asymmetry.** The scan
  receipt's `acquisition` block (`scan_freshness.py`) refuses to compute an age
  across an incompatible boot or time namespace ("Domain equality is
  compatibility evidence, not authentication of a boot or namespace lifetime.
  UTC is diagnostic only and never establishes recency", `scan_freshness.py:3-4`),
  and `classify_age` returns `unknown` rather than a guess when the bounds
  straddle the threshold (`scan_freshness.py:124-155`). The
  `branch-inventory` sense separates an absent worktree from an unreadable one —
  "a worktree that is there and still cannot be read stays unknown — an unreadable
  worktree is a fault, an absent one is a fact" (`discovery.py:4158-4160`) — and
  its docstring records the exact reason: "Reading it as unreadable made the
  whole inventory UNKNOWN for one dead path, which hid every other fact the
  reading measures" (`discovery.py:4247-4255`).
- **And the plant's own doctrine already says the reader must not infer absence
  from one failure.** `seed_culture_views.py:242` counts a sense as unknown
  unless it is `verified`/`available`/`unavailable`, and the discover pane
  prints "STATE: UNKNOWN — … source reads unverified"
  (`seed_culture_views.py:257`) rather than asserting a fault. The senses
  charter: "A command on PATH is only a candidate, absence is not calm, and stale
  data is not current" (`seed_senses_charter.md:9`).

## What is new here

**The plant has the detection history and does not use it for presence, and
its one reader drops 65 of the senses it has history for.** Both halves are
measured on the live site. Every figure below was re-measured on
2026-10-23T19:3xZ against the retained corpus, and the method is reproducible
from `SITE/discovery/scan-*.json`.

1. **The reader's filter is numeric, and it silently deletes 65 senses.**
   `feed.py:890-895` keeps an observation only when
   `obs.get("state") == "verified"` **and** `int(obs["sample"])` parses. The
   second condition is the defect: `int()` throws for a non-numeric sample, and
   the `except (KeyError, TypeError, ValueError): pass` swallows it. Measured
   over the retained corpus, the reader returns **zero rows** for **65 senses
   probed 100 or more times**, including every one of these:

   | sense | probes | verified reads | rows the reader returns |
   | --- | --- | --- | --- |
   | `sense.proc.loadavg` | 2 781 | 2 781 | **0** |
   | `sense.ledger.evidence-binding` | 1 695 | 126 | **0** |
   | `sense.repo.branch-inventory` | 1 676 | 1 424 | **0** |
   | `sense.space.light` | 1 528 | 343 | **0** |
   | `sense.runtime.drift-across-sites` | 2 225 | 392 | **0** |
   | `sense.tmux.windows` | 2 781 | 2 771 | **0** |
   | `sense.body.redmi10` | 732 | 148 | **0** |
   | `sense.coord.gate-latency` | 1 026 | 56 | **0** |
   | `sense.repo.head-rewinds` | 319 | 17 | **0** |

   Only **three** of the 73 senses in the corpus are reachable at all, and they
   are reachable by accident: their `sample` is a JSON **integer**
   (`sense.disk.free` = `30317203456`, `sense.sys.wakeup-count`,
   `sense.disk.inodes-available`). The other 70 senses carry a **string** or a
   **list**: `sense.proc.loadavg`'s sample is `"20.42 47.61 47.28 18/1795
   92246"`; `sense.ledger.evidence-binding`'s is `"all_bad=189
   count_excluded=118 latest_bad=0 …"`; `sense.tmux.windows`'s is a JSON array
   of pane names. **The corpus reader works only for the senses whose sample
   happens to be a bare integer.** That is an accident of the data, not a
   designed filter, and the reader's own docstring does not mention it:
   "Read a sense's verified samples from the retained discovery scan corpus"
   (`feed.py:872-876`).

2. **So a sense's own history is unreachable exactly when the history matters.**
   The two senses reading `unknown` in the newest scan each have a long
   verified history that the reader cannot reach:

   | sense | newest state | prior verified reads | prior unknown reads | rows returned by `read_scan_series` |
   | --- | --- | --- | --- | --- |
   | `sense.ledger.evidence-binding` | `unknown` (14:25Z scan) / `drift` (19:32Z scan) | **126** | 406 | **0** |
   | `sense.space.light` | `unknown` | **343** | 1 185 | **0** |
   | `sense.body.redmi10` | `unknown` | **148** | 584 | **0** |

   The plant has probed `sense.space.light` 1 528 times and detected it 343
   times. It read `unknown` in the newest scan. Under MacKenzie et al.'s
   asymmetry (p. 2249, "Species are never falsely detected at a site when
   absent"), the newest reading cannot be the absence of the thing: the thing
   was detected 343 times and the most recent probe simply did not detect it.
   **The pane calls this `unknown`, which is honest, and then stops.** Nothing
   in the plant asks the one question the corpus can answer: *how often does
   this sense detect when it detects at all?* Without that, `unknown` is an
   uninterpretable word that means "this probe failed", and the reader cannot
   distinguish it from "this thing is gone".

3. **A transient failure and a real absence are published identically.**
   `sense.ledger.evidence-binding` has a **154-scan consecutive `unknown` run**
   (2026-10-19T16:42:26Z → 2026-10-20T20:23:56Z), and every one of those 154
   samples carries the *same* `coverage_incomplete=1` — one row the reading
   could not see. The sense recovered afterwards and read `verified`. A reader
   of the newest scan sees `unknown` and a sample that begins
   `all_bad=189 count_excluded=118`; a reader of the history sees that this
   sense was `unknown` for 154 consecutive occasions and then recovered. The
   plant publishes the *state* of one occasion and keeps the *history* in files
   nobody reads for that purpose.

4. **The paper's own prescription — more occasions — is already satisfied, and
   the plant still cannot use it.** p. 2253: "Increasing the number of visits
   per site improves the precision of the estimated occupancy rate, and the
   resulting increase in information improves the accuracy of the estimate when
   detection probabilities are low." The plant has 2 781 occasions. The
   information is on disk. The only thing missing is a reader that can see it
   for senses whose sample is not an integer.

5. **What the paper licenses and what it does not.** It licenses the *question*
   and the *history*: a non-detection is a mixture of "present and missed" and
   "absent", the two are not separable from one occasion, and separating them
   requires the detection history across occasions. It does **not** license
   estimating `c` for this plant: the sites are not closed (25 boot ids in the
   corpus), detections are not independent across senses (every sense is probed
   in the same scan), and the paper's own simulation says the estimator is not
   trustworthy below detection probability 0.3 with T ≥ 5 (p. 2251), which this
   review has not established for any sense. **The transfer is therefore a
   detection-history instrument, not an occupancy estimate.** The paper's
   sentence that a site with an all-zero history has likelihood `c·Π(1−p) +
   (1−c)` is the reason a single occasion is not enough, and it is the reason a
   *rate* over the retained corpus is a fact worth publishing even when the
   *occupancy* is not estimable.

6. **The plant already knows how to publish a rate with its own denominator.**
   `sense.disk.trend` (`discovery.py:3453-3571`) is exactly this shape: it
   reads `read_scan_series`, and it refuses to publish a rate it cannot
   support — "fewer than 2 verified disk.free samples (n=…)" (`discovery.py:3483`)
   and "span … under …h" (`discovery.py:3488`) both publish `unknown`, and the
   rate is published "only when [the windows] agree on the direction of the
   trend" (`discovery.py:3530-3533`). The precedent for a detection-rate
   instrument **with its own bounds** already exists in this repository, on the
   one sense the reader can reach.

7. **The reader is already expensive, and the obvious implementation of this
   proposal would make it catastrophically worse.** Measured on the live corpus
   (2 781 receipts), `read_scan_series(home, "sense.disk.free")` takes
   **5.20 s** — it re-parses every receipt from disk on every call. A
   detection-rate instrument that calls it **per sense** would re-parse the
   corpus 73 times: **≈380 s per scan**. `scan()` already calls it once, inside
   `_disk_trend` (`discovery.py:7371`). This is the plant's own operational
   history repeating itself — the operator's overload investigation of
   2026-10-23 recorded 47 global OOM kills and a whole-tape parser memory
   amplification, and `75efc41 Reduce feed parser cold-read memory peaks` was
   committed the same evening partly to fix this reader's footprint. **The
   proposal must therefore be one shared corpus pass, not N passes.** Item 1 of
   the Application is written to that constraint; the per-sense shape is
   rejected on measured grounds, not on taste.

## Application

target: src/mishe_tauftauf/feed.py

**Publish each sense's detection history from the corpus the plant already
keeps, and make the reader that reads it able to read every sense — in one
corpus pass, not 73.**

1. **Fix the reader's numeric filter first — it is a small defect with a
   65-sense blast radius.** `feed.py:871-897` currently returns
   `(datetime, int)` tuples and skips any observation whose sample does not
   parse as an integer. The corpus's `sample` field is a *human-readable
   string* for 70 of the 73 senses it holds. Split the function into two, with
   one shared parse boundary:

   - `read_scan_series(home, sense_id)` keeps its current contract exactly —
     `(datetime, int)` for numeric samples — because `sense.disk.trend`
     (`discovery.py:3476`) depends on the integer and must not change
     behaviour.
   - a new sibling returns the raw sample for the same corpus, so a caller that
     wants the *record* rather than the number is not forced to re-implement
     the glob, the `created` parse and the `state == "verified"` filter.

   Both must share one parse site: `feed.py` is a named boundary file in
   `parsing-policy.json`, so the scan JSON is parsed in exactly one place and
   `python tools/check_parsing.py` must add no new debt. **The boundary
   function's docstring must say what it drops and why** — the current one
   ("Read a sense's verified samples from the retained discovery scan corpus")
   does not mention that 70 of 73 senses yield nothing.

2. **Read the corpus once and serve every sense from that one pass.** The
   measurement in "What is new here" item 7 is the constraint, not a footnote:
   a per-sense reader costs ≈380 s per scan against 5.20 s today. The shape is
   a single traversal of `scan-*.json` that builds a per-sense detection
   summary, computed **once** per call site and shared by every sense. It must
   not be a cache keyed on something that goes stale silently; the corpus is
   append-only, so a summary built from the receipts present at a stated instant
   is reproducible, and the reader should publish how many receipts it read and
   how many it could not read.

   The producer is `discovery.py`'s `scan()`; the receipt is
   `SITE/discovery/latest.json` and the per-scan artifact. The addition is
   additive and costs no extra byte read: the producer already holds the
   previous scan's observations (`previous = latest(home)`, `discovery.py:7672`).

   For each sense, publish over the retained corpus:

       "detection": {"occasions": <scans in which the sense was probed>,
                     "detected": <scans in which it read verified>,
                     "last_detected": <ISO instant of the most recent verified read>,
                     "last_state": <the state of the most recent read>,
                     "consecutive_unread": <length of the current run of non-verified reads>,
                     "basis": "retained scan corpus, verified reads only"}

   The naming matters: `detection` names a *rate over occasions*, and
   `basis` names the population it is computed over, because — as the
   already-landed `evidence-and-verdicts--metric` review said of its own
   numbers — a coverage figure without its selection rule is unreadable.

3. **The consumer rule: a non-detection after a detection is UNKNOWN, and a
   non-detection with no detection history is also UNKNOWN, but they are
   different words.** The paper's asymmetry (p. 2249) says a failed probe
   never establishes absence. So the pane must not print a bare `unknown` for
   a sense that the corpus says is usually detected. Two additions to
   `seed_culture_views.py`'s `_observation_reading` (`:151-211`), which is
   where a sense's state is turned into a pane line:

   - when the newest read is `unknown` and the corpus says the sense was
     detected at least once, the line names the rate and the last detection:
     "source_state=unknown detection=126/1688 last_detected=2026-10-20T…";
   - when the newest read is `unknown` and the corpus says the sense was
     **never** detected, the line says so, and says that the sense's presence
     is *unestablished* rather than *absent* — which is what the paper's
     mixture `c·Π(1−p) + (1−c)` (p. 2250) says a zero-history site is.

   **Neither changes the state's meaning.** The sense stays `unknown`. What
   changes is that the reader can tell *which kind* of unknown it is, and can
   see that an `unknown` after 343 detections is a failed probe rather than a
   disappearance.

4. **One bounded detector, in the plant's existing check style, so the
   recurrence is observable.** A new check under `checks/` that reads the
   published receipts and reports the senses whose newest read is `unknown`
   while their detection rate is non-zero — i.e. the false-absence class,
   named and counted. It must exit non-zero only when it can decide, and
   report UNKNOWN when the corpus is absent or unreadable; a missing, stale
   or broken monitor stays UNKNOWN or RED, never clean. It must read
   published artifacts only and re-derive nothing, the way
   `checks/coverage-window-expiry.py` and `checks/ledger-band-second-reader.py`
   do — and it must import nothing from the package, so the plant's second
   reader cannot inherit the publisher's mistake.

5. **The acceptance criteria are the paper's limits, not decoration.**
   (i) The rate is a **rate over occasions**, published with the occasions
   count and the selection rule, and never as a probability of presence.
   (ii) The occupancy quantity `c` is **not** estimated: the sites are not
   closed (25 boot ids in the corpus), detections are not independent across
   senses, and the paper's own simulation says the estimator is not
   trustworthy below detection probability 0.3 with T ≥ 5 (p. 2251), which this
   review has not established for any sense. If a future reader wants `c`, that
   is a separate cell with its own assumptions.
   (iii) The reading **never converts a failed probe into an absence claim** —
   that is the source's central sentence, and it is the plant's own doctrine
   ("absence is not calm").
   (iv) The rate is computed from the **retained corpus**, and the corpus's
   own gaps are named: the reader must publish how many scan receipts it
   could not read, because the paper's "Missing observations" paragraph
   (p. 2250) is explicit that an occasion that did not happen "contributes no
   information to the model likelihood for that site" and must be accounted
   for, not silently skipped.
   (v) The **regression** is the reader fix itself: after it, `read_scan_series`
   and its new sibling must return rows for senses whose sample is not an
   integer, and `sense.disk.trend` must be unchanged.
   (vi) The **cost** is bounded: the corpus is read **once** per scan, not once
   per sense. A build that re-parses per sense is out of the contract even if
   its output is correct.

## What this review does not claim

- It does not claim any sense's presence is currently misread. It claims the
  plant **cannot tell** whether a sense's `unknown` is a failed probe or an
  absence, and that the corpus it already keeps is the instrument that would
  tell it.
- The three senses measured here (`evidence-binding`, `space.light`,
  `body.redmi10`) are the ones reading `unknown` **in the newest scan at
  2026-10-23T14:25Z**, with `sense.ledger.evidence-binding` having recovered to
  `drift` by the 19:32Z scan. That is a moving corpus; the counts are
  reproducible from the retained receipts.
- The 65-sense figure counts senses *probed 100 or more times* that the reader
  returns empty for. It is a lower bound on the reader's loss: senses probed
  fewer times are not counted, and the count does not include the `command.*`
  declarations, which are never `verified` by construction. Over the whole
  corpus the reader reaches **3 of 73** senses.
- `read_scan_series` was added for `sense.disk.trend` and was never asked to
  serve every sense. The defect is that its contract is **undocumented where
  it matters**: a reader who trusts the docstring will believe it returns a
  sense's history, and it returns nothing for 70 of 73 senses.

## Limits

- The measurement is one site's live corpus, read on 2026-10-23T19:3xZ (and
  earlier at 14:36Z–14:52Z for the same figures, which did not move).
  **The corpus grows while it is read** — 2 772 receipts at 14:41Z, 2 774 at
  14:52Z, 2 781 at 19:3xZ — so the per-sense probe counts are stated as of the
  read that produced them, and a reader reproducing them later will see slightly
  larger numbers. The counts are reproducible from `SITE/discovery/scan-*.json`.
- MacKenzie et al. model *sites* and *species*. The transfer is structural — a
  non-detection is a mixture, and the history is what makes it decidable — and
  it is offered as that, not as a claim that the paper discussed sensors.
- The paper's estimator is **not** transferred and is **not** recommended
  here; §"What is new here" item 5 says exactly why, and the Application's
  acceptance criterion (ii) forbids it.
- The timing measurement (5.20 s for one corpus pass) is a single reading on
  this host while the plant was under load; it is a cost order of magnitude,
  not a benchmark. The ratio that matters — one pass versus 73 — is structural.
- I did not measure whether the *other* readers of the corpus
  (`checks/coverage-window-expiry.py`, `checks/ledger-band-second-reader.py`)
  have the same numeric assumption. They read the ledger's rows, not the
  senses' samples, and I left them alone.
- I edited no organ. This review file and this role's wall are mine; the
  proposal is routed to the owner, who decides what changes.

## References

- MacKenzie, D. I., Nichols, J. D., Lachman, G. B., Droege, S., Royle, J. A., &
  Langtimm, C. A. (2002). Estimating site occupancy rates when detection
  probabilities are less than one. *Ecology*, 83(8), 2248–2255.
  <https://www.sfu.ca/~lmgonigl/materials-qm/papers/mackenzie-2002-2248.pdf>
  (read 2026-10-23; DOI <https://doi.org/10.1890/0012-9658(2002)083[2248:ESORWD]2.0.CO;2>)
- Plant files read for this review: `src/mishe_tauftauf/feed.py:871-897`,
  `src/mishe_tauftauf/discovery.py:3453-3571` (`sense.disk.trend`),
  `src/mishe_tauftauf/discovery.py:4158-4160` and `:4247-4255`
  (`branch-inventory`), `src/mishe_tauftauf/discovery.py:7371` (`_disk_trend`
  call site), `src/mishe_tauftauf/discovery.py:7670-7689` (`scan`),
  `src/mishe_tauftauf/scan_freshness.py:1-4` and `:124-155`,
  `src/mishe_tauftauf/seed_culture_views.py:75-79` (`_scan_reading`),
  `src/mishe_tauftauf/seed_culture_views.py:151-211` (`_observation_reading`),
  `src/mishe_tauftauf/seed_culture_views.py:214-278` (`discover` pane),
  `src/mishe_tauftauf/seed.py:28-61` (`_newest_scan_observations`),
  `checks/coverage-window-expiry.py:1-20`,
  `src/mishe_tauftauf/seed_senses_charter.md:9`.
