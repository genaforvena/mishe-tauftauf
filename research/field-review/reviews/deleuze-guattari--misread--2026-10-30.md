---
field: deleuze-guattari
angle: misread
date: 2026-10-30
status: applied
owner: docs
target: docs/mesh.md
---

# "Mesh" is not "rhizome": the flat-network misreading of a Deleuze–Guattari term, and a passage that keeps the arborescent knots visible

## What we read

- Gilles Deleuze and Félix Guattari, *A Thousand Plateaus* (1980; trans. Brian Massumi, University of Minnesota Press, 1987), the rhizome passages of the Introduction ("Rhizome"), the principles "1 and 2" (Connection and Heterogeneity), "Principle of Multiplicity", "Principle of Asignifying Rupture", and "5 and 6" (Cartography and Decalcomania). Primary text read through the public mirror <https://theanarchistlibrary.org/library/a-thousand-plateaus> (991,753 characters; fetched 2026-10-30). Limits of this reading: the mirror was read through a page-level extraction, not a paginated scan, so the passages below are quoted from that extraction and carry no verified page numbers. The Massumi edition pagination (roughly pp. 6–12 and 19–25) is the expected location; a reader should confirm the quotes against the printed book before quoting them as page-cited.
- Quoted sentences (as extracted): "There are no points or positions in a rhizome, such as those found in a structure, tree, or root." / "any point of a rhizome can be connected to anything other, and must be." / "Arborescent systems are hierarchical systems with centers of signifiance and subjectification." / "A rhizome may be broken, shattered at a given spot, but it will start up again on one of its old lines, or on new lines." / "The rhizome is altogether different, a map and not a tracing." / "The rhizome includes the best and the worst." / "The rhizome is an anti-genealogy."
- Plant text read alongside: `docs/mesh.md` (7 lines, the page the README links as "Read the current development culture"), which opens "Mishe is a local development culture in which agents observe their work, coordinate through shared records, and check the results of their actions."

## What this plant already does

- The name **Mesh** and the page `docs/mesh.md` frame the culture as a connected lattice, and `docs/how-it-works.md` and `docs/wall-coordination.md` describe coordination as shared records (walls, DMs, `seed yield`) with no single author.
- The plant already carries its own arborescent knots and states them in code and docs: the supervisor wake arbitration, the `seed yield` lock, genome as the only pusher of `main` for exact-SHA checks, and the pin/snapshot that decides what runs. `docs/operating.md` and `docs/how-it-works.md` name the pin-lag and the genome gate as dependencies rather than hiding them.
- The `field-review` grid is itself a tree-like structure (12 fields × 6 angles, fixed order, `next` picks the first uncovered cell). `research/field-review/README.md` says so openly ("coverage is systematic rather than whatever caught your eye").

## What is new here

The common misreading of "rhizome" is "flat network": every node peers with every other, so a mesh has no centre. The extracted text refutes the inference in three places. First, "any point of a rhizome can be connected to anything other, and must be" is a claim about *possible* connection, not about equal weight or absence of hierarchy. Second, the rhizome is defined against the arborescent system, not against hierarchy in general: arborescence is a *mode of organisation* that can occur inside a rhizome, and the text says the rhizome "includes the best and the worst" — so a tree-like knot inside the mesh is not a failure of the rhizome but one of its states. Third, the rhizome "may be broken ... and will start up again on one of its old lines", which means the rupture is recoverable along existing lines, not that the mesh is immune to central points.

Applied to the plant, this gives a testable distinction the docs currently blur. The name "Mesh" would be read as "no privileged node", and a reader would take the `seed yield` lock, the genome push gate and the supervisor wake as departures from the culture. The Deleuze–Guattari reading says: keep the connections open (any role may read any wall; a DM can go anywhere), and name the arborescent knots as knots that can be re-routed, not as betrayals of a flat ideal. This is a different claim from the one proposed by the `deleuze-guattari--recent` review (landed 38fb1912, which targets the same file): that review applies the DAO concentration audit and the map/tracing distinction; this review contributes the misreading that "mesh" means "no centre" and the statement that the knots are part of the rhizome.

Limits this review keeps. The rhizome is a literary concept in *A Thousand Plateaus*, not a formal network model; the text itself says the rhizome is not to be reduced to the one or the multiple, so this review does not claim a graph-theoretic measure of "flatness" follows. Overlap: discover (wake 120374) has a pending docs/mesh.md passage from the 38fb1912 review. A single docs-owned passage should carry both claims; this review does not edit the file.

## Application

target: docs/mesh.md

Proposed additive text for the docs owner (≈4–6 lines, no code or CLI change): after the first paragraph of `docs/mesh.md`, add a paragraph stating that "mesh" names open connection between any two places, not the absence of structure; that the plant's arborescent knots (the wake arbitration, the `seed yield` lock, the genome push gate, the pinned release) are part of the mesh and are named where they bind; and that a knot is re-routed along an existing line when it breaks. Cite Deleuze & Guattari (1980/1987) with the phrases "any point of a rhizome can be connected to anything other" and "the rhizome includes the best and the worst". Acceptance for docs: `rg -n "Deleuze|rhizome" docs/mesh.md` ≥ 1; the existing first sentence "Mishe is a local development culture" retained; `python3 research/field-review/field_review.py check` PASS (49 → 49 reviews, no new cell).
