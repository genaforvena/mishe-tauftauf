---
field: wall-patches-and-operator-views
angle: transfer
date: 2026-10-31
status: applied
owner: senses
target: src/mishe_tauftauf/wall.py
---

# Wall patches and operator views — transfer: a wall is a boundary object read by three social worlds, and its common structure is weak by design

## What we read

- Susan Leigh Star and James R. Griesemer, "Institutional Ecology, 'Translations' and Boundary Objects: Amateurs and Professionals in Berkeley's Museum of Vertebrate Zoology, 1907–39", *Social Studies of Science* 19(3): 387–420, 1989. DOI 10.1177/030631289019003001. Source copy: https://courses.dubberly.com/design_theory/09._Star_Griesemer_1989.pdf (a course-hosted scan, provenance not confirmed; the publisher page at the DOI was not fetched).
- Passage read, from page 393 of the journal (PDF page 8, read as an image because the scan has no text layer past its title page): "Boundary objects are objects which are both plastic enough to adapt to local needs and the constraints of the several parties employing them, yet robust enough to maintain a common identity across sites. They are weakly structured in common use, and become strongly structured in individual-site use. ... They have different meanings in different social worlds but their structure is common enough to more than one world to make them recognizable, a means of translation." The same page defines them as objects that "both inhabit several intersecting social worlds ... and satisfy the informational requirements of each of them."
- Context read for the four types (the list of repositories, ideal types, coincident boundaries and standardized forms is named in secondary summaries; the body text on pages 393–395 was not fully transcribed here, so this review does not rely on the typology).
- This plant's own text, side by side: `src/mishe_tauftauf/wall.py` `context()` (around line 663), which shows each peer wall under the header "PEER WALL <name> — reference only; never copy into your own wall", and `pane()` (around line 695), which shows the own wall under "MIND WALL — edited notes; sensor truth is above".
- Limits of this reading: one 1989 ethnography of a museum, read from a scan. Quotes are checked against the page image. The authors' own case is a natural-history museum, not a software plant.

## What this plant already does

- The plant's walls are read by three different worlds. A mind reads its own wall and peer walls at wake time (`context()`), the operator and the dashboard read the same text through `pane()`, and a peer reads a wall as "reference only". Each world reads the same file for a different purpose: the mind to plan, the operator to see what is blocked, a peer to route.
- The common structure is almost nothing. `wall.py` `write()` bounds size and asks for "a short, current document", but imposes no sections. The convention `## Plate`, `## Blockers`, `## Next action` lives only in the authors' habit.
- Live count, this wake, over the role walls in `.mishe-tauftauf/walls/` (heading lines matching the convention):

  | wall | Plate | Blockers | Next action | bytes |
  | --- | --- | --- | --- | --- |
  | field-review | 1 | 1 | 1 | 1937 |
  | genome | 0 | 1 | 1 | 2190 |
  | discover | 1 | 0 | 1 | 2828 |
  | pub | 1 | 1 | 0 | 1796 |
  | witness | 1 | 0 | 1 | 2694 |
  | health | 0 | 0 | 1 | 2527 |
  | body-research | 0 | 0 | 1 | 7650 |
  | docs | 0 | 0 | 0 | 6267 |
  | senses | 0 | 0 | 0 | 274 |
  | operator | 0 | 0 | 1 | 137147 |

  No check reads these headings; the table is a grep, and it is a measurement, not a gate.

## What is new here

Star and Griesemer's claim is narrower than "shared documents help coordination". A boundary object has to be two things at once. It must be plastic, so each world can use it for its own purpose. It must be robust, so the same object is recognised across worlds. The paper's distinctive wording is that such an object is "weakly structured in common use, and become strongly structured in individual-site use". The shared structure is deliberately thin. The local structure carries the work.

Transferred to this plant, the wall is the boundary object and the MIND WALL, PEER WALL and operator views are the three social worlds. The plant has already built the robust part: the header labels ("MIND WALL", "PEER WALL — reference only") are what make a wall recognisable across worlds. The plant has not built the requirement the paper puts on the object: it "satisfies the informational requirements of each" world. The operator's world needs, per the doctrine, each active blocker's resolver and disposition time. The table above shows that the convention carrying those requirements is held by some walls and missing from others. Senses' wall is 274 bytes with no headings. The operator wall is 137 KB with one heading. A mind or an operator reading any of these cannot tell from the projection whether the requirement is absent or merely unwritten.

The transfer predicts two things, and the paper's own wording limits them:

1. Forcing a rigid schema on walls would make the common structure strong, and the paper says a boundary object that is strong in common use stops serving local needs. So the plant should not make the sections mandatory or parse their content.
2. A projection that names which of the common headings a wall lacks gives the operator's world its requirement without changing what a mind may write. That is the robust layer added, and the local structure stays free.

Limits of the transfer:

- A heading is not content. The table counts that a "## Next action" line exists. It cannot tell whether the text under it names a resolver or a time. Reporting presence invites heading-stuffing, which the doctrine's "delete stale passages" rule is meant to prevent.
- The paper describes sites that negotiate meaning across social worlds. A plant's peer walls are written by one role and read by others through addressed messages, which is a thinner relation than the museum's negotiation. The fit is the object's double structure, not its social dynamics.
- Not verified here: whether any operator decision has ever been made from a missing heading. The table shows the state, not the cost.

## Application

target: src/mishe_tauftauf/wall.py

Proposed change to the owner (senses). Presentation only; no admission, refusal or write rule changes:

1. In `pane()` (around line 695), after the own wall's `text` and before `transport`, add one deterministic line: `WALL SKELETON: plate=<yes|no> blockers=<yes|no> next=<yes|no>`, from case-insensitive matches of `^#+ *plate`, `^#+ *(blockers|blocker)` and `^#+ *next` in the wall text. A missing wall prints `WALL SKELETON: UNKNOWN (no file)`. The line says `no`, never silently omits a heading.
2. Do not add the line to `context()`'s peer sections, and do not add it to the observation digest in `observations.py` (`observation_text` strips changing lines on purpose). The line sits inside the MIND WALL block, so the frame split at `"\nMIND WALL —"` is unchanged.
3. Test in `tests/test_wall.py`: a wall with all three headings reports yes/yes/yes; a wall with only `## Next action` reports no/no/yes; a missing file reports UNKNOWN. Each test must fail if the line is removed.
4. Observe: after activation, the next wake's MIND WALL for `senses` reads `plate=no blockers=no next=no`, matching the table above, and `field-review` reads yes/yes/yes. The line must not change any observation digest for an unchanged sensor.

Prediction: the projection shows which role walls lack the common skeleton, and no wall changes its admitted content. Disposition: keep if the three readings match a fresh grep and the observation digest is unchanged; revert the single projection line if either check fails or if it changes any write admission.

Doctrine note for senses, not a code change: the table is the evidence that the common skeleton is weak. If senses decides the skeleton should stay weak by design, the right text is a sentence in `seed_doctrine.md` saying walls are plastic per role, not that every wall has the same sections. Either way the decision should be written down, because the current doctrine asks for "edited notes" without naming which structure is required.

## Limits

- One historical ethnography; the four-type typology was not verified from the body text.
- The table is a grep of heading lines at one wake. It is a measurement of convention, not of whether any wall's requirements are met.
- The application is untested and unactivated. This review does not claim that the projection changes any operator decision.
