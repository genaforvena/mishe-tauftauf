# Field-review lane

Read one real source per (field × angle) cell and land one review that either
names a concrete change in this repository or is discarded in one line.

The lane exists because the retired mesh's own AGENTS.md asked for it — "make
room for literature-led experiments, including Deleuze and Guattari where
useful" — and because that mesh's version of the lane mostly did not land. Its
corpus was 376 reviews, of which 245 said "uncommitted in the tree (steward
lands)". The prose was the product; the change was the promise. This lane is
built so the change is the product.

## The grid

`fields.json` is the literal grid: 12 fields × 6 angles = 72 cells.

The fields are this plant's own subsystems — evidence binding, freshness,
failure and recovery, coordination, sensing and privacy, publication,
self-development, model judgment — plus four cross-domain lenses carried over
from the retired mesh: Deleuze & Guattari, biosemiotics, enactivism, and
second-order cybernetics. The retired mesh's own field list was 18
disciplines chosen for the mesh; this one is chosen for *this* plant, so a
reading has somewhere to land.

The angles are the retired mesh's, unchanged, because they are the ones that
made a review worth writing:

| angle | the question |
| --- | --- |
| `recent` | a RECENT result (2023–2026) in this area — what is new right now |
| `misread` | a foundational idea we may have **misread** or applied too loosely here |
| `critique` | a known critique or failure mode of this area we would reproduce |
| `transfer` | a cross-domain transfer onto one of this plant's parts |
| `mechanism` | an operational mechanism it proposes — implementable, not just a philosophy |
| `metric` | a concrete metric or experiment the area uses to measure itself |

## The three layers

**L1 — selection (deterministic).** `field_review.py next` names the first
uncovered cell in registry order, so two runs on the same tree name the same
cell. Before the brief is printed, the field's keywords are swept across the
tree, and the brief carries where this plant already uses that vocabulary.
The sweep reports *occurrence*, never meaning; the mind still has to read.

**L2 — the reading (the mind's work).** Read the current primary source
itself. Name one concept this plant does not already embody. Cite where you
found it, with the version or date. Then either propose one concrete
application to a named file in this repository, or discard the cell in one
line explaining why it does not apply. A discard is a legitimate outcome and
is counted, not hidden.

**L3 — landing (deterministic).** The review is a tracked file under
`research/field-review/reviews/`, committed to `main` by its author, and the
proposal is routed to the owning role by addressed message. `field_review.py
check` enforces the content contract:

- front matter names field, angle, date, status (`applied`/`discarded`) and
  owner (a plant role);
- the body has `## What we read`, `## What this plant already does`, `## What
  is new here`, and exactly one of `## Application` (with `target:` naming an
  existing repository **file**) or `## Discard` (one line of why);
- the source URL is in the reading section, and that section is not empty.

`check` fixes the form of a review, not what its sections say. Whether a
reading is substantive, and whether the content varies across reviews, is an
open measurement that the contract does not make.

Landing is a separate question from content, and it is answered by the branch:
coverage counts the reviews `git ls-tree HEAD` carries, never the index and
never the working tree. A review that is written but not committed is reported
as unlanded (and as a problem once it has waited past the grace period), and a
landed review that was edited afterwards is reported as edited. The scheduled
check `checks/field-review-contract.py` answers the same questions against the
reviewed tree, so a dirty working tree cannot decide the plant's monitoring.

Coverage is derived from the review files themselves. There is no second
ledger, so nothing can drift from the artifacts it describes.

## What this lane refuses to repeat

The retired mesh's lane had four measurable failures. Each has a named
counterpart here.

| retired mesh | here |
| --- | --- |
| 245/376 reviews ended "uncommitted in the tree (steward lands)" | coverage counts the branch, not the disk; a review that waits past the grace period without landing is a scheduled finding |
| the field registry was node-local runtime state and died in the 2026-07-14 migration | the grid is a tracked file in the repository |
| registry rows were annotated with hit counts that anticorrelated with aboutness ("Raft" scored highest and had dissolved) | selection never counts hits; it reads the source |
| the producer outran its receivers — 27 ideas in flight, oldest 43 days, while the floor read empty | one cell at a time, one review per cell, and the next cell only exists after the previous one lands |

## Running it

```bash
python3 research/field-review/field_review.py next      # the next cell and its sweep
python3 research/field-review/field_review.py coverage  # which cells are covered
python3 research/field-review/field_review.py check     # the landing contract
```

`check` exits 0 when every landed review satisfies the contract, 2 when one
does not, and 2 with an UNKNOWN line when the grid itself cannot be read.

## Completeness

"Complete" is stated here, not inferred from the grid's size. The grid is
complete for this plant when all three hold:

1. **Cells.** Every (field × angle) cell has a landed review that passes
   `field_review.py check`. `field_review.py next` printing `GRID EXHAUSTED`
   certifies only this. It does not certify correctness, reachability or
   impact of any reading.
2. **Subsystems named.** Every plant subsystem module in
   `src/mishe_tauftauf/*.py` is either named by some field (its basename
   appears in that field's `plant_part`, `keywords` or `title`) or is listed
   as plumbing in the exclusion list below. The name test is a substring
   test: it finds candidate gaps and does not prove a field covers a module.
3. **Nothing unnamed is load-bearing.** A module that appears in an
   outcome, recovery or verdict path and is named by no field is a grid gap,
   whatever its count. This is a judgement, recorded in the census artifact.

When (2) or (3) fails, the successor fields are a grid change. The lane does
not edit `fields.json` on its own: it writes the census artifact, routes the
proposal to Genome, and authors the change only on Genome's go, with the
commit SHA shared for CI.

Plumbing exclusions (named, not gaps): `__main__`, `access`, `cli`,
`guard_cli`, `patch_text`, `timestamp_text`, `tmux`, `proc_table`,
`space_text`, `unit_properties`, `window_manifest`, `minimal_supervisor`,
`probe_supervisor`, `clear_journal`, `site_uninstall`, `declared_roots`.
