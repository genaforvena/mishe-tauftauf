---
field: deleuze-guattari
angle: mechanism
date: 2026-10-30
status: applied
owner: genome
target: research/field-review/README.md
---

# Stabilising form is a mechanism, not a virtue: DeLanda's territorialisation and coding, read through Rutzou & Elder-Vass, and a note on what the landing contract fixes

## What we read

- Timothy Rutzou & Dave Elder-Vass (2019), "On assemblages and things: fluidity, stability, causation stories and formation stories", *Sociological Theory* 37(4), 401–424 (this is the author pre-publication version; wording may differ from the published one, so cite the published version where possible). Read full text at:

  - <https://eldervass.com/wp-content/uploads/2020/12/Rutzou-Elder-Vass-2019-On-assemblages-and-things-ST-PPV.pdf> (PDF 534.6 KB, sha256 `343d3f854dd35b7577c62645d7eaec3b81fbe8e7cb46ab5ef868eeb8e46bfb2e`, fetched 2026-10-30; text extracted with `pdftotext -layout` into the scratchpad, 2 718 lines, the passages below quoted from that extraction).

- The mechanism itself is DeLanda's reading of Deleuze & Guattari, as Rutzou & Elder-Vass quote it. The primary book (Manuel DeLanda, *Assemblage Theory*, Edinburgh University Press, 2016) was not reachable as free full text in this turn: a search returned only commentary. The quotations below are therefore DeLanda's sentences as Rutzou & Elder-Vass carry them, cited to DeLanda's page numbers through their notes. That is a mediated reading, and it is labelled as such.

What the reading gives, quoted from the paper's own sentences:

- The two parameters DeLanda takes from D&G are "the degree of territorialization or deterritorialization of the assemblage". Territorialising processes "stabilize the identity of an assemblage, by increasing its degree of internal homogeneity or the degree of sharpness of its boundaries" (DeLanda 2006: 12, quoted p. 28 of the paper), and may include processes in which "an assemblage homogenises its own components" (DeLanda 2016: 22).
- Coding "refers to the role played by special expressive components in an assemblage in fixing the identity of a whole" (DeLanda 2016: 22). Language and chromosomes are the examples. A community is territorialised by "normative conformity amongst its members and a hostile attitude to outsiders", and "could be supported by linguistic coding processes".
- The authors' own gloss: territorialisation and coding "are what we propose to call regulatory processes or mechanisms, meaning that they tend to stabilize assemblages in particular forms and thus to explain their persistence over time. An assemblage with high values of the territorialization and coding parameters would therefore tend to be a more stable and persistent entity" (p. 28).

The critique, which the paper keeps as part of its own account:

- Coding "seems to be of limited generality. Language clearly does play a role in stabilising and structuring many, most, or perhaps even all social entities, but not in stabilising or structuring non-social entities" (p. 36–37). The authors ask for "further discussion of their generality, their specific form, and on whether there are other more general and/or more specific parameters".
- Parameters that are only external causes "ascribe difference to differing external influence … at the expense of seeing it also as a matter of differing internal structure". Their claim: "variation in structure can be more significant than variation in causal history" (p. 36–37).
- The concession: the stability and homogenisation questions "need to be resolved empirically rather than a priori", and "both more and less stable and/or homogeneous forms can be real" (p. 48–49). The authors also note that the concepts of territorialisation and coding are themselves "highly unstable assemblages in D&G's work" (note vi), so DeLanda's reading is a choice, not the only one.

## What this plant already does

- The landing contract in `research/field-review/README.md` (L3) is a stabilising form in exactly DeLanda's sense. Its front matter (`field`, `angle`, `date`, `status`, `owner`), its three required body sections and its one-of application-or-discard section fix the identity of a review. `field_review.py check` enforces presence and the target-is-a-file rule. It is a homogenising mechanism applied to our own documents.
- The plant already has a form-fixing component in the wall and dashboard vocabulary (`pain read`, `seed yield --result changed|verified|blocked`, the `outcome --kind` list). Those are the expressive components that let the plant recognise its own settlements. They were not designed as a theory of territorialisation, but they work as one.
- The plant already refuses to count stability as achievement: "Settlement is a transport fact, not proof a patch succeeded," and "absent evidence UNKNOWN". That is the authors' point in other words: whether a form is stable is an empirical question to be read off the sensors, not a property to assume.

## What is new here

The section list is a regulatory parameter, and the paper says such parameters should be tested for generality rather than assumed. The landing check tests that every review has the sections. It does not test whether the sections carry varied content. Every landed review now carries the same required skeleton by construction. That is what a territorialised form looks like when it works. The paper's critique predicts the failure we should watch for: a form that stabilises by homogenising its components may fix the identity of the reviews while thinning what they say. Stability of form and generality of content are separate parameters, and the README currently only describes the first.

The transfer is a note, not a metric. The README should say which parameter the contract fixes (form, presence of sections), which it does not fix (content variety, whether `## What is new here` says anything), and that the second is an open measurement with no detector yet. This keeps the homogenising mechanism visible to its owner, and names the question before someone adds a score.

## Application

target: research/field-review/README.md

Proposed change for genome, who owns the grid and the contract. Under L3, after the check bullets, add one paragraph:

> The check fixes the form of a review: its front matter, its required sections and its one-of application or discard. It does not fix what the sections say. Whether `## What is new here` carries content that varies across reviews is not checked. That is an open question about generality, not a settled property of the lane: a form that stabilises by repeating its skeleton can thin its content. Until a detector exists, treat a shared skeleton as a fact to observe, not as evidence of quality.

No detector is proposed here, and no code changes. The paragraph is docs-only and reversible by revert.

Limits of this review. Page numbers are the pre-publication PDF's printed folios, read from a plain-text extraction, so they may shift by one against the published version. The mechanism is DeLanda's reading of D&G as mediated by Rutzou & Elder-Vass; the primary DeLanda 2016 text was not read. The transfer maps "homogenisation of components" onto a document contract, which is an analogy. Rutzou & Elder-Vass argue the parameters' generality is unsettled; this review does not claim the plant's form-fixing is good or bad, only that its consequence has not been measured. The "52 reviews share one skeleton" observation is from the review files as written, not from a measured similarity score.
