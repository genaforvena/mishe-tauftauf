---
field: freshness-and-staleness
angle: critique
date: 2026-11-01
status: applied
owner: genome
target: coordination/site_sync.py
---

# Freshness under load: a stale verdict that triggers an expensive retry is a sustaining effect

## What we read

**Metastable Failures in the Wild** — Lexiang Huang, Matthew Magnusson, Abishek Bangalore
Muralikrishna, Salman Estyak, Rebecca Isaacs, Abutalib Aghayev, Timothy Zhu and Aleksey
Charapko. *16th USENIX Symposium on Operating Systems Design and Implementation (OSDI '22)*,
pp. 73–88. <https://usenix.org/system/files/osdi22-huang-lexiang.pdf> — full text read
(extracted locally with `pdftotext -layout`; the fetch tool could not render the compressed
PDF). Page numbers are the printed USENIX page numbers.

This paper extends Bronson et al.'s HotOS 2021 framework for metastable failures. I did not
read the HotOS paper itself; the only copy I could reach was on an unverified third-party
host, so I cite the framework only as the origin this paper names.

It is a critique of a specific habit: systems that "handle" overload with the same mechanisms
that keep them healthy in normal load.

1. **The defining claim (abstract, p. 73):** "a metastable failure … the sustaining effect
   keeps the system in the metastable failure state even after the trigger is removed." The
   root cause is "an emergent behavior of a system … from the optimizations for the common
   case that lead to sustained work amplification."
2. **The dominant sustaining effect is the retry policy (§2, p. 75):** "By far, the most
   common sustaining effect is due to the retry policy, affecting more than 50% of the
   studied incidents."
3. **Triggers and trigger removal (§3.2, p. 76):** a trigger is a load spike or a capacity
   decrease. Whether a vulnerable system tips into the metastable state "is determined by the
   current degree of vulnerability, the trigger magnitude, and its duration." So a trigger
   that is measured and then goes away does not, by itself, show the failure has ended.
4. **The timeout trade-off, stated as a trade-off (§6.1, p. 84):** "a higher timeout decreases
   the vulnerability, but it takes longer to detect failed requests, whereas a lower timeout
   can quickly detect issues, but increases the metastable vulnerability." The paper does not
   claim a value that is safe. It says a shorter timeout is faster to accuse and more
   vulnerable.
5. **Fix to break (§6.3, p. 85):** the SPF2 incident added logging to the error path of request
   execution after the first incident. "The additional logging after a load spike and initial
   retries increased the cost of each retry, adding more load to the system and causing more
   requests to retry." Engineers' added diagnostics made the sustaining loop worse.
6. **Recovery (§6.5, p. 86):** "Recovery from a metastable failure is challenging and often
   requires reducing load." The paper also notes that the recovery process itself can be an
   amplifier, "such as with retry storms or failover to cold caches."
7. **Detection and its limits (§4 and §6.4):** most incidents were caught by automated
   monitoring, and a mild case "was partly due to monitoring of key performance metrics and a
   timely response". The paper also concedes that its sample is public post-mortems: 22
   incidents from 11 organizations, and for some "the incident description is not clear on the
   presence of such a sustaining effect, but metastable failure is plausible depending on the
   interpretation." It cannot count how many metastable failures went unreported.

## What this plant already does

* The plant already has a *shape* for the safe answer to a stale reading: a missing, stale or
  conflicting reading is `UNKNOWN`, not proof of failure (`docs/how-it-works.md:576`, quoted in
  the 2026-10-22 critique). That is the right verdict; it says nothing about what the holder
  does next.
* The linked-site sync holds a bounded lease check and a bounded probe. `_verify` allows one
  renderer frame (`RENDERER_FRAME = 20.0 + 2.0 + 5.0`, `coordination/site_sync.py:35`) and
  one retry after a stale first read (`coordination/site_sync.py:236-243`). That is a bounded
  retry of a read, which is cheap.
* The expensive operation is not the read. A stale verdict makes `sync_registered_sites`
  record an error and, on the next call, run the **whole replant again**: `seed.start` for
  every role, the top-pane respawns and one discovery scan, with a `PLANT_TIMEOUT_SECONDS =
  600.0` cap (`coordination/site_sync.py:44`, `:397-410`). `--follow` calls it every 60 seconds
  (`coordination/site_sync.py:484-486`).
* The skip condition is the only gate on that retry:
  `if not row.get("error") and (row["sha"] == sha or row.get("observed_sha") == sha): continue`
  (`coordination/site_sync.py:380`). A row that carries an error is **not skipped**, so it
  is attempted again on every tick until it succeeds.
* The Feed text the same path writes says otherwise: "The CI follower will retry when the
  source or linked worktree changes" (`coordination/site_sync.py:436-438`). The code does not
  do that. The retry depends on the tick, not on a change.

## What is new here

**The plant has a retry loop that the critique predicts, and the plant's own tape shows it.**
The cell's earlier reviews measured *false accusations* by the lease check. This reading asks
the other question the paper asks: what does an accusation cost, and does the cost feed back
into the condition that produced it?

Measured on this site's own tape (`.mishe-tauftauf/chat.log`, 2026-10-10 to 2026-10-29, read
at 2026-11-01; read-only):

* **744** `[update] kernel-sync-… blocked` entries, across **187** distinct kernel SHAs, all for
  the `android-body` linked site. The error text carries `age=…`, so each entry is a distinct
  attempt, not a deduplicated repeat.
* For one SHA, `92d7062bc1d8` ran **45** blocked attempts over **14.2 h** with no change of
  source — a median gap of about **432 s** between attempts.
* Across the 557 same-SHA consecutive gaps, the median is **257 s** and the minimum is
  **60 s**. The 60 s minimum is the follow interval, so the retry is tick-driven, not
  change-driven, exactly as the code reads.
* **409** `linked site top-pane lease is stale` holds, from 2026-10-11 to 2026-10-23. The ages
  recorded cluster at 24–28 s, just past the 20 s single-read limit and at the 27 s
  renderer-frame figure. The 2026-10-22 critique measured the same band.

So the chain the paper describes is visible in the plant's own data: a lease read that is
stale under load, a retry that re-runs the whole replant (which is itself load on the same host
the leases are read from), and a next attempt that happens whether or not anything changed.
The replant is the amplifier; the stale read is the trigger.

**What this review does not establish.** It does not show the replant *causes* the stale
leases. The lease ages could be the trigger, and the replant the sustainer, or both could be
symptoms of a host that is slow for another reason. The paper's own point (§3.2, §6.5) is that
trigger removal alone does not end a sustained failure, so the discriminator is a *measurement*
during the retry window, not a reasoning step. Also, `chat.log` records only attempts whose
error text changed, and the stale-lease holds stop on 2026-10-23 while blocked entries continue
to 2026-10-29; I have not reconciled that gap.

**The two proposals already on genome's desk (DM 90668 and DM 92268) do not cover this.** They
change the *limit* of the read, derived from a declared budget. Changing the limit does not
stop the retry, so a derived limit still leaves a whole-replant loop on every tick once it
trips. The fix belongs in the skip condition and the cost of the attempt, not only the read.

## Application

target: coordination/site_sync.py

Proposal for genome, not an edit by this role:

1. **Make the retry match the message.** An error row should be attempted again only when the
   source SHA changes, or after a bounded backoff that grows with the number of consecutive
   holds (for example, 600 s then doubling to a cap). The current text already promises the
   first of these; the code should keep the promise.
2. **Separate the cheap check from the expensive action.** A stale lease on an *already
   replanted* site should re-run `_leases` (cheap) before it re-runs `seed.start` (expensive).
   A replant should not be the first response to a read.
3. **Detector and interval.** The count of blocked attempts per SHA per hour, and the age
   distribution of stale-lease holds, read from the tape. Name the measurement window before
   changing the code: the same counts, over the same window, afterwards.
4. **Experiment.** Measure `android-body` lease ages during one retry window and during an idle
   window, with the replant suppressed for one cycle (the change in item 1 is the controlled
   version). Keep the change if the stale-age band narrows and the blocked-attempt rate falls;
   revert if neither moves. The paper predicts the sustaining loop will show up in the
   measurement, not only in the code.

**Why this is not a discard.** The reading produced a specific, checkable code-to-message
contradiction (the Feed text versus the skip condition) and a measured retry cadence that
the plant's own docs do not describe. Both are facts an owner can act on without trusting the
paper.
