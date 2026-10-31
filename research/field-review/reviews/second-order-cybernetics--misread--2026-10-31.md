---
field: second-order-cybernetics
angle: misread
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Primary source: **Heinz von Foerster, "Ethics and Second-Order Cybernetics"** (address, Paris, 1991; the PDF is a transcript that includes a question-and-answer exchange), PDF <https://uranos.ch/research/references/VonFoerster1992/ethics.pdf>. Text extracted locally with `pdftotext -layout`, 8813 words. Passages read: the opening argument on the separation of observer and observed (the principle of objectivity, and the fear of paradox), the passage "a brain is required to write a theory of a brain ... the cybernetician, by entering his own domain, has to account for his or her own activity", the passage on responsibility and objectivity used to avoid responsibility, the exchange where "one has stepped into the circle that closes upon itself" (the third-order question), and the short definition "first-order cybernetics is the cybernetics of observed systems, while second-order cybernetics is the cybernetics of observing systems."

Secondary context read: Foerster's own aphorism list (<https://archive.cybsoc.org/heinz.htm>), used only to confirm aphorisms 3 and 18 as Foerster's wording.

The misread under review is "second-order = watching the watchers": a checker stacked on a checker, a verdict on a verdict. Foerster's text is about accounting for one's own activity as an observer, not a higher observer above the first.

## What this plant already does

The plant's observers are themselves enumerated and watched. `checks/check-scheduler-coverage.py` names the observer-check runner, its systemd unit and the CI job that runs each check, and it states that a check which runs unobserved is unobserved by the plant (its own docstring, lines 12-22). The doctrine the plant runs under requires every verdict to name its detector and resolver, and it treats missing or stale monitoring as UNKNOWN or RED (`.agents/skills/plant-mishe/SKILL.md:137`, `src/mishe_tauftauf/seed_doctrine.md`). The field-review contract checker reports its own resolver in its output. In Foerster's terms, the observer is already accounted for in the plant's verdicts: the plant does not describe its checks from outside, it names the instrument and its limit.

## What is new here

Nothing that is a repository change. The text's sharpest point is against the stacking the misread would produce: Foerster says the ascent from second to third order "would not create anything new", because the circle closes on itself. A fourth checker above the observer-checks runner would be the first-order move (an independent observer that watches the world go by) relocated upward, the position Foerster calls the one that makes responsibility escape. The reading therefore argues against adding a meta-layer, and the plant's own charter already says the same ("another planning layer" is a failure mode).

The reading does not give a mechanism that is missing here. Its concrete content is responsibility for one's own observation, and the plant already writes that into each verdict (detector, resolver, detection interval).

Limits of the reading: Foerster's address is a philosophical lecture, not an engineering specification, and the ethics framing (increase choices) does not translate into a check. The text is a transcript that includes an edited conversation; wording of quoted lines is from that transcript. Its original publication venue was not verified.

## Discard

Discarded: the primary Foerster text is read. Its misread target (observer-watches-observer stacking) is already answered by the plant's per-verdict accountability and its observer inventory, and the text argues against a further meta-layer, so no application target is named.
