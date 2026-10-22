---
field: freshness-and-staleness
angle: recent
date: 2026-10-22
status: applied
owner: genome
target: coordination/site_sync.py
---

## What we read

**LeaseGuard: Raft Leases Done Right** — A. Jesse Jiryu Davis, Murat Demirbas, Lingzhi Deng
(MongoDB Research). arXiv:2512.15659v2 (v1 17 Dec 2025, v2 19 Dec 2025), accepted to ACM
SIGMOD 2026. <https://arxiv.org/abs/2512.15659v2> — full text read at
<https://arxiv.org/html/2512.15659v1>.

Four things in it bear on this plant, and I quote them where they matter.

1. **A lease's validity is an interval comparison, not a comparison of two point clocks.**
   §2.2: each node has `intervalNow()`, returning `[earliest, latest]`, and "a node knows
   that t1 is more than Δ old if intervalNow() has returned t2 and t1.latest + Δ < t2.earliest".
   The decision is separated from the threshold; when the two intervals overlap, the
   question is not decided by either number alone.

2. **That comparison is not optional; the protocol's safety rests on it.** §4.3: the lease
   decision "requires clocks with correctly measured uncertainty bounds on each node", and
   "if a clock's bounds are wrong … then multiple leaders may think they are leaseholders
   at once. In that case a deposed leader may execute reads while a higher-term leader
   commits writes." The failure is not a wrong number; it is two parties acting as the
   holder of one authority.

3. **Lease protocols go wrong because they are under-specified, not because they are
   hard.** §1: "Prior lease protocols are vaguely specified and hurt availability, so most
   Raft systems implement them incorrectly or not at all" — with the etcd lease-timeout
   miscalculation (reported 2024) still open at the time of writing. §5.2 treats the lease
   duration Δ as something to be *chosen* against the holder's worst case, not a constant.

4. **A holder that keeps its lease without making progress is its own failure mode.**
   §1: "prior designs also risk gray failures, where a leader may continue renewing its
   lease, but fail to execute writes due to internal issues … The system is stuck with a
   'faux leader' that cannot make progress yet refuses to step aside."

## What this plant already does

* The plant's lease is a **rendered footer line**: `-- pane live <utc> · refresh 5s · ticks
  every frame` (`src/mishe_tauftauf/cli.py:375`). Its own doctrine is that it proves
  rendering only. Three readers interpret that one line, with three different predicates:
  * `src/mishe_tauftauf/tmux.py` `check_pane` — clock-free: capture twice, 11 s apart, and
    require the lease *string* to change.
  * `src/mishe_tauftauf/wall_view.py` `pane_lease_lines` — age against
    `max(120.0, 6 × refresh)`, i.e. 120 s, with the observer's point clock.
  * `coordination/site_sync.py` `_leases` — age must lie in `[0, 20]` s, with the observer's
    point clock, against a **remote** site's stamp.
* The plant already owns the interval discipline — for scans. `src/mishe_tauftauf/scan_freshness.py`
  computes `age_bounds()` as a (min, max) pair of nanosecond bounds and `classify_age()`
  answers `recent` / `stale` / **`unknown`**, where `unknown` is exactly the case where the
  bounds straddle the threshold. The module's own header says "UTC is diagnostic only and
  never establishes recency". The wall-clock lease verdicts use none of this.
* The plant knows that hosts do not share a clock: `src/mishe_tauftauf/discovery.py` measures
  the phone's behind-skew (~25.5 s, growing) from the minimum observed span, refuses to
  judge the phone's samples by span, and publishes "phone/host clock agreement unverified".
* The plant declares the linked site's renderer cycle budget **in the same file as the 20 s
  limit**: `RENDERER_FRAME = 20.0 + 2.0 + 5.0` = 27 s (`coordination/site_sync.py:35`), with
  `observations._RENDERER_TIMEOUTS = {"witness": 20.0}`, a 10 s default, one 2 s retry
  backoff and a 5 s refresh sleep.

## What is new here

**The linked site is being held by its own declared cycle budget, not by a fault.** I
measured the remote's real frame cadence from the linked site's own heartbeat files
(`logs/pain-watch-<slug>.heartbeat`, sampled for 45 s at 2026-10-22T08:27Z):

| role | observed frame period |
| --- | --- |
| permissions | 28.3 s, 30.8 s |
| discover | 28.2 s, 29.8 s |
| genome | 6.0, 7.5, 6.3, 6.6, 5.8, 9.3 s |
| health | 6.5, 7.2, 6.5, 7.5, 6.9, 6.9 s |
| witness | one frame in 45 s (period > 45 s) |

The heavy roles are on the probe-timeout-and-retry path — 10 s + 2 s + 10 s, plus the 5 s
sleep = 27 s, the plant's own `RENDERER_FRAME` — which is what the measurement shows. The
reader that judges them refuses any age above **20 s**
(`coordination/site_sync.py:281-283`), i.e. a limit *shorter than the holder's own worst
case*. So for `permissions` and `discover` roughly a third of all reads fall beyond the
limit by construction, and for `witness` most of them do.

The plant's own tape holds the verdicts. 248 `linked site top-pane lease is stale` between
2026-10-11 and 2026-10-22 (28 on the 11th, 133 on the 21st, 61 by 08:25 today); ages min
20.1 s, median 23.1 s, p90 27.3 s, max 132.8 s; **236 of 248 (95%) inside the 20–28 s band,
which is the renderer's own cycle**; per role witness 93, permissions 79, discover 58, docs 6,
research-methods 4, genome 2, health 1 — the roles with the longest frames dominate, and the
local roles that render every 6–9 s never appear. 586 `kernel-sync … blocked` lines carry
it, and the plant's own registry records
`error: linked site top-pane lease is stale: android-body:permissions age=21.6s`.

This is LeaseGuard's failure mode in a different costume: the lease's duration is
under-specified, so the holder is declared expired while it is provably inside its own
declared cycle, and the plant holds a site that is working. The source's rule is the
remedy — the lease duration must cover the holder's worst case and the clock bound, and
the verdict must be an interval comparison, with a third state for "cannot be decided"
rather than a two-valued fresh/stale.

**Limits of this reading.** The frame periods are a 45 s sample of one site at one moment;
the ages' distribution is consistent with them but the tape records the *verdicts*, not the
failing reads' frame durations. The cross-host clock offset measured small today (the
linked site's own tape entries ran 4.4 s behind local now at 08:20Z), so clock offset is
not the dominant term *now* — but LeaseGuard requires the bound to be published rather than
assumed, and the current code folds "the remote clock is ahead" (`age < 0`) into the same
"stale" message as a genuine expiry.

## Application

target: coordination/site_sync.py

`_leases()` should decide the linked site's lease the way LeaseGuard decides one, and
publish what it decided:

1. **Derive the limit from the plant's own renderer budget instead of the constant 20 s.**
   `limit(role) = 2 × observations._RENDERER_TIMEOUTS.get(role, 10.0) + 2.0 (retry
   backoff) + refresh (5 s)` — 27 s for the ordinary roles, 47 s for `witness` — i.e.
   `RENDERER_FRAME` generalised per role, taken from the same place the renderer takes its
   own timeout. The reader then stops declaring a holder expired inside its own cycle.
2. **Compare intervals, and keep the third verdict.** Expired only if
   `stamp.latest + limit < now.earliest`; a stamp *ahead* of the local clock is a
   clock-offset verdict, not staleness (today `not 0 <= age <= 20` reports both under one
   message); and an overlap between the two intervals is "undecidable", the verdict
   `scan_freshness.classify_age` already calls `unknown` — reuse that vocabulary rather
   than invent one.
3. **Publish the bound with the verdict**: the limit used, the measured cross-host clock
   bound (the sync's own round trip gives a half-RTT bound; the plant already computes a
   skew bound for the phone in `discovery.py`), and the verdict, beside the site's registry
   row — so a reader can see what was decided and re-derive it, which is the condition
   senses accepted for the ledger reading.

**Acceptance.** Replay the 248 recorded ages from the plant's own tape through the new
rule: "stale" must fall to the six verdicts above 40 s and the one above 60 s, and the 236
in the 20–28 s band must read valid-within-budget. The two existing freeze tests in
`tests/test_site_sync.py` must keep passing unchanged — a renderer that never publishes a
fresh footer is still held after one re-read (`reads == 2`) — and a new case at age 21–27 s
must not be stale. The advancing check must still catch a renderer that stops after a good
read, because that is the fault the stale verdict was standing in for.

This role is read-only with respect to the rest of the plant: the change belongs to the
owner of `coordination/site_sync.py` (genome, the release coordinator), and this review is
the input to that decision.
