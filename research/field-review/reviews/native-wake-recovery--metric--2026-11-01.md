---
field: native-wake-recovery
angle: metric
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/recovery_policy.py
---

# native-wake-recovery × metric — applied

## What we read

- Patterson et al., *Recovery Oriented Computing (ROC): Motivation, Definition, Techniques, and Case Studies*, UCB//CSD-02-1175 (2002). Abstract, read in full: "By concentrating on Mean Time to Repair (MTTR) rather than Mean Time to Failure (MTTF), ROC reduces recovery time and thus offers higher availability." The abstract defines no formula; the metric is MTTR as a design target. https://www2.eecs.berkeley.edu/Pubs/TechRpts/2002/5574.html
- Candea & Fox, *Crash-Only Software*, HotOS IX (2003). Read in full HTML. Section 1: "when crash-rebooting, unavailability consists only of the time to recover." Table 1 reboot times (104/75 s, 47/39 s, 61/48 s) are a "casual comparison", with no methodology or trial count. Section 3.1 recovery-time claims (Postgres "practically instantaneous", write-through caching) are argued, not measured. Section 4 `RetryAfter(n)` carries an estimated, not measured, time. https://static.usenix.org/events/hotos03/tech/full_papers/candea/candea_html/index.html

Both are primary. The ROC paper gives the metric (time to repair); the crash-only paper gives the definition of downtime and shows how weakly it was measured. A metric definition is not a measurement, and the review keeps the two apart.

## What this plant already does

`src/mishe_tauftauf/recovery_policy.py` keeps a per-function episode record: `first_failed`, `passing_since`, `restored_at`, `previous_restored_at`, `episode`. It sets `restored_at` only after the check has passed for `stable_seconds`. The live file `.mishe-tauftauf/recovery/state.json` gives two completed durations at 2026-11-01 (computed here from its own fields, not from a plant metric):

- git-index-clear, episode 5: `restored_at - first_failed` = 633.0 s
- host-pressure, episode 26: 336.5 s
- independent-guard (episode 25) and seed-lock (episode 24) are open (RECOVERING / UNKNOWN), so no duration.

So the raw data for time-to-restore exists. Nothing in `src/` computes it, and the dashboard reports episode counts and states, not repair time.

## What is new here

The ROC framing turns an episode count into a repair-time question, and the plant can answer that question from its own bytes:

1. **Time-to-restore per episode** is `restored_at - first_failed`. This is the plant's MTTR analogue. It is deterministic, needs no new sensor, and can be written as a pure function over the episode record.
2. **Limit inherited from the crash-only paper's own weakness.** The metric is only as good as the restore instant. `restored_at` is stamped after a `stable_seconds` hold, so the figure includes the hold. A flapping check that is up for less than `stable_seconds` never closes an episode. Recording the hold separately (`restored_at - passing_since`) keeps the two costs apart.
3. **History is not retained.** `state.json` keeps only the current episode and one `previous_restored_at`. Across episodes the mean cannot be computed from retained state. The 22,514 files under `recovery/events/` are host actions with no restore timestamps (checked: the schema is `host_action`), so they cannot supply it either. A mean would need an append-only episode ledger; without one, the metric shows only the last episode.

No new sensor and no threshold change is needed for (1) and (2). (3) is a retention decision for the owner.

## Application

target: src/mishe_tauftauf/recovery_policy.py

Owner: health.

Proposal, one bounded change:

- Add a pure function `episode_restore_seconds(row)` returning `restored_at - first_failed` when both are set and `None` otherwise, plus `episode_hold_seconds(row)` returning `restored_at - passing_since`.
- Expose both on the existing recovery row in the dashboard view, labelled as the current-episode figure, with `UNKNOWN` when either field is missing.
- Tests: a closed episode returns its duration; an open episode returns `None`; a missing field returns `None`, not zero; and a flap shorter than `stable_seconds` does not close an episode.
- Do not add an across-episode mean until an append-only episode ledger exists. That is the trigger for a second proposal.

Verification to request: the two live numbers above (633.0 s and 336.5 s) should reproduce from `state.json` after the change. Health's own reading of the restore timing is the independent check; this review is an input, not the acceptance.

