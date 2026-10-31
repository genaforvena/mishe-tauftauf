---
field: biosemiotics
angle: metric
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Maran, T., "Are ecological codes archetypal structures?", author's PDF at <https://kodu.ut.ee/~timo_m/publikatsioonid/Maran_ecocode.pdf>. Read 2026-10-31 by extracting the text of the PDF with `pdftotext` (the WebFetch rendering returned raw PDF bytes) and reading the introduction, the section "Properties of the ecological code" (three stated properties), and the passage on Jung's archetypes. The passage read from Kull (2010, p. 354), quoted in Maran's introduction, defines an ecological code as "the sets of (sign) relations (regular irreducible correspondences) characteristic to an entire ecosystem, including the interspecific relations in particular." Maran states that "it is, however, not entirely clear" how those sign relations relate to local semiotic processes. Not read: the full archetype section and the closing argument. Kull's 2008 and 1998 papers were named in the search results but not fetched, so nothing here is claimed about them.

## What this plant already does

- The plant binds its own outcome records to evidence and checks the binding: `src/mishe_tauftauf/wall.py` (`_publish_outcome`) refuses a non-eligible path or an evidence path already bound, and `src/mishe_tauftauf/initiative.py` reads a changed evidence sha256 as UNKNOWN. These are checks on a binding, not a measure of how regular the bindings are across roles.
- The field-review lane counts its own outputs deterministically: `research/field-review/field_review.py check` reports 59 of 59 reviews landed on the branch. That is a count of produced artifacts, not of correspondences between them.

## What is new here

Kull's definition gives the one operational handle in this source: "regular irreducible correspondences" could in principle be tested as a count of recurrent pairings across species or roles, with irreducibility as a test that no pairing decomposes into smaller ones. This plant has no equivalent data. Its records are roles and outcome bindings, which are arbitrary by construction (see `biosemiotics--mechanism`, discarded for the same reason), and the sign relations the definition needs are not in its tape. Maran's own reading says the link from sign relations to local semiosis is unresolved, so a measure built on this definition would first have to settle the unresolved part, which the source does not do.

## Discard

No operational metric for ecological codes in the read passages; Kull's definition names a countable object but the plant holds no sign-relation data to count, so no change to a repository file follows from this source.
