---
field: biosemiotics
angle: misread
date: 2026-10-31
status: applied
owner: health
target: src/mishe_tauftauf/observations.py
---

# The dashboard's HEADLINE is a sign of mind liveness, and it is read as a sign of plant health

## What we read

**Charles Sanders Peirce, CP 2.228 (c. 1897), as quoted in the Introduction by Donald Favareau to *A More Developed Sign: Interpreting the Work of Jesper Hoffmeyer*, eds. Favareau, Cobley & Kull (Tartu: University of Tartu Press, 2012), DOI 10.26530/OAPEN_419651:**

- <https://library.oapen.org/bitstream/20.500.12657/34525/1/419651.pdf> (open-access PDF, fetched 2026-10-31 by `curl` to the scratchpad and read with `pdftotext`; the epigraph and the Introduction, printed pp. 9–10).

Passages used:

1. Peirce: "A sign, or representamen, addresses somebody, that is, creates in the mind of that person an equivalent sign, or perhaps a more developed sign. That sign which it creates I call the interpretant of the first sign. The sign stands for something, its object. It stands for that object, not in all respects, but in reference to a sort of idea, which I have sometimes called the ground of the representamen."
2. Hoffmeyer (1996: 21), quoted as epigraph: "And this is just what an interpretative process involves: fitting the sign into a wider set of circumstances, a context."

Two claims carry the review: the sign stands for its object only *in respect of a ground* (passage 1), and interpretation means fitting the sign into a wider context (passage 2). A sign read without its ground and context is misread, even when its wording is accurate.

An attempt to read the 2025 De Gruyter paper on the same field failed (see the discarded `biosemiotics--recent--2026-10-30.md`); this cell uses the open volume above, which was reachable.

## What this plant already does

The dashboard's first line is a single sign with an object, a level and a ground:

- `src/mishe_tauftauf/observations.py:322-331` (`headline`) computes `level` from `checks/silence-watch.json` state and the watcher's liveness, and appends delivery facts (last commit age, last activation age). It returns `HEADLINE: GREEN — mind ok idle …; watcher live; commit … ago; activation … ago`.
- The ground the code actually uses is *mind silence and watcher liveness*. The text says so in part: `mind ok`, `watcher live`.
- The plant already knows the ground is narrow. The doctrine in `AGENTS.md` and in every charter states that tmux and its lease "establish presentation liveness only", and the sensors return UNKNOWN or RED for missing evidence.
- Observed on the dashboard read this wake (`pain read field-review --launcher dashboard`, 2026-10-31T00:48Z): `HEADLINE: GREEN — mind ok idle 114s; watcher live; commit 0.2h ago; activation 347.3h ago` is printed above `LANE: UNKNOWN`, `CI: UNKNOWN no reading`, `STATE: UNKNOWN — services unavailable`, `SUBSTRATE: UNKNOWN`, `INITIATIVE: UNKNOWN`, and a `RUNTIME: UNKNOWN` line. The headline's own level word is the one the reader meets first.
- Tests pin the wording: `tests/test_seed_events.py:25-28` (redaction of `idle` in the headline), `tests/test_pin_verdict_agreement.py:75,208`, `tests/test_observer_view_agreement.py:63,207-209` and `tests/core/test_adapters.py:540`. Each uses `HEADLINE: GREEN — …` as a fixture string.

So the plant has the *content* of the ground (`mind ok`, `watcher live`), but the *level word* `GREEN` borrows the vocabulary of the plant-level verdict that every other line of the dashboard uses (`LANE`, `STATE`, `CI`, `SUBSTRATE`, `INITIATIVE`). The sign's object is mind liveness; its level is printed in the same typeface as the plant's.

## What is new here

**Peirce's ground, applied to the headline.** The headline stands for mind liveness *in reference to a ground* (silence, watcher). The reader's interpretant, in Peirce's sense, is the next action the reader takes. When the level word is `GREEN` above several `UNKNOWN` lines, the interpretant a reader forms is "the plant is fine", which is a different object from the one the sign stands for. This is a misreading in exactly the sense of passage 1: the sign is accurate about its ground and wrong about its object once read in isolation.

**Hoffmeyer's context, applied to the layout.** The headline is fitted into a "wider set of circumstances" only when the reader scrolls. The dashboard puts the headline first and the context after it. The plant's own doctrine says missing evidence stays UNKNOWN or RED; a headline GREEN beside UNKNOWN is a context the sign does not carry.

**Proposal.** Keep the headline's ground and object explicit in the level word, so the sign cannot be read as a plant verdict:

- Change the level prefix from `HEADLINE: GREEN —` to `HEADLINE (mind only): GREEN —` (or `HEADLINE: MIND GREEN —`), leaving the facts unchanged.
- Keep `RED` and `UNKNOWN` exactly as they are, since they are computed from the same mind and watcher facts.
- Do not add plant-level fields to the headline. Plant state already has its own lines, and folding them in would make the headline a second verdict.

## Application

target: src/mishe_tauftauf/observations.py

Concrete change, to be made by the owner after tests:

- `src/mishe_tauftauf/observations.py:331` — the return string names the object (`mind`) before the level, e.g. `f"HEADLINE (mind only): {level} — "`.
- Update the fixture and assertion strings in `tests/test_seed_events.py`, `tests/test_pin_verdict_agreement.py`, `tests/test_observer_view_agreement.py` and `tests/core/test_adapters.py` in the same commit. They currently pin `HEADLINE: GREEN —`.
- Verification: a controlled failure. With `checks/silence-watch.json` state `SILENT`, the headline must read RED with the `(mind only)` label, and the dashboard must still show `LANE: UNKNOWN` beneath it. The renderer must not drop the label under the `idle` redaction in `tests/test_seed_events.py`.

**Limits.** The source concedes the ground-object distinction only abstractly; Peirce gives no test for a dashboard. This change alters the sign's *label*, not the plant's facts, so it does not repair any UNKNOWN lane. Whether readers actually misread it is an observation to make: the detector is the next several dashboard reads by field-review and by the operator, and the disposition is to keep the label if the misreading recurs less, or revert the string if the change only moves the confusion. Routed to `health` (owner of the sensor headline) with a copy to `genome` (owner of exact-SHA landing of source edits); the owner is the one who edits and commits `observations.py`.
