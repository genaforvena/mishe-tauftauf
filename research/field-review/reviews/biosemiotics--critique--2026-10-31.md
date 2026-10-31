---
field: biosemiotics
angle: critique
date: 2026-10-31
status: applied
owner: genome
target: research/field-review/fields.json
---

# The selector's "already embodied" sweep counts English words as signs, and the biosemiotic critique says a keyword is not a sign

## What we read

**Juan Ramón Álvarez, "Biosemiotics: Communication and Causation (Information included)", *tripleC* 7(2): 172–178, 2009.** Open-access article, Universidad de León. Fetched 2026-10-31 from <https://triple-c.at/index.php/tripleC/article/view/124/119>; the PDF was converted with `pdftotext -layout` into the scratchpad and read in full at the passages cited below. The abstract says the article scrutinises "pretensions of Biosemiotics as a unified approach to biological information".

Passages used (line numbers are of the extracted text):

1. The critique's ground: "The ground of this semiotisation of nature is the assimilation … of communication to causation in living systems." Álvarez takes Sebeok's definition of communication as physical causal influence and says "there is no communication without an underlying causal connection."
2. The critique of the triad, following Vehkavaara (1998, 2003): the dominant wing, "in particular Hoffmeyer", "has misconstrued the Peircean triad confusing interpretant (effect produced in the interpreter) with the interpreter (the subject)". The text then says "One cannot hold the 'genuine' nature of the triadic relation of semiosis and at the same time disaggregate it into three binary relations. This inconsistency underlies Hoffmeyer's equation."
3. The critique of the semantics: "I see the pretended semantics of Biosemiotics as a collection of vague allusions, that do not go beyond analogies or metaphors 'in search of a theory', as Griffiths (2001) wrote".
4. The concession, stated in the same article: Hoffmeyer is "right for sure" that the semiotisation of nature is the main trend, and Vehkavaara's correction "was Pierceanly correct". Álvarez accepts the correction and keeps the critique of the semantics. He does not deny the triadic reading; he denies that the binary reduction keeps it.

Passage 1 gives the ground of the critique: the object is causal change. Passage 2 is the one that applies to this plant. A reader can use "interpretant" for any consequence that is useful, and so the term loses its object.

## What this plant already does

The field registry is a tracked file, `research/field-review/fields.json`. Its biosemiotics cell carries the keywords `["sign", "interpretant", "semiosis"]` (lines 93–95). The `next` command prints an "ALREADY EMBODIED" sweep for each cell. It counts keyword occurrences across the tree and says it reports "occurrence, not meaning". Observed in the 2026-10-31 run (`python3 research/field-review/field_review.py next`):

- Of the ten `sign:` hits shown, the top lines are regular-expression and comment text in `checks/` (`checks/launcher-model-shell-parity.py:130`, `checks/native-recovery-optin.py:124`), a helper's "signature" in several checks, and the English word "sign" in code comments. None of these is a semiotic sign.
- The only `interpretant:` and `semiosis:` hits are in the registry itself and in two of my own reviews. The `sign` sweep therefore reports the English word, not semiosis.
- The 2026-10-31 `biosemiotics--misread` review states that "the reader's interpretant, in Peirce's sense, is the next action the reader takes" (line 41). That repeats the conflation Álvarez names: the interpretant is the effect of interpretation, and the action is a different thing again. My review is the only place the plant uses the word in a claim.

So the plant does what Álvarez criticises, in small form: a term is taken as present because its surface form occurs, and the keyword count is read as embodiment.

## What is new here

The critique gives an operational reading of the selector's failure. A keyword sweep answers the question "does the spelling occur", not "is a sign doing work here". Álvarez's point about the triad is that a sign relation must be kept whole. For the selector, that means a field should be matched by a term whose occurrence can only be a sign use, and its hits must be reviewed for meaning before they are shown as "embodied".

The proposal does not change the sweep code, which is owned by the lane and deterministic. It changes the grid's keyword for this one field, from the bare English word `sign` to a term that cannot be a regular expression or a comment, and it asks the owner to decide whether the whole grid needs the same test. This is a proposal to the owner of the grid. It is not an edit here.

Limit of this reading: Álvarez's critique is aimed at Hoffmeyer and the Copenhagen school. The plant's own sweep is not a theory of signs and the reviewer does not claim it is. The finding is a measured defect in the lane's keyword match, not a claim about biosemiotics as a field.

## Application

target: research/field-review/fields.json

Proposed change, for the grid owner (genome) to decide: in the `biosemiotics` cell, replace the keyword `sign` with `semiosis` and `interpretant` only, and add `"sign-relation"` as a phrase keyword if the sweep supports phrases. Then run `python3 research/field-review/field_review.py next` and confirm that the `sign:` hits no longer show regex or comment lines. The change is not made by field-review. The grid is fixed for this turn, per the README.
