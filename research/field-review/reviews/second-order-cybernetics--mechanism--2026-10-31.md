---
field: second-order-cybernetics
angle: mechanism
date: 2026-10-31
status: applied
owner: genome
target: checks/observer-verdict-agreement.py
---

## What we read

Primary source: **Gordon Pask, "An Essay on the Kinetics of Language, Behaviour and Thought"** (System Research Ltd, 1979), the account of Conversation Theory (CT) that Pask built on the THOUGHTSTICKER epistemological laboratory. Full text fetched from <https://www.pangaro.com/pask/pask%20essay%20on%20kinetics%20of%20language.pdf> and extracted locally with `pdftotext -layout`. The scan is OCR-quality and garbled in places, so the reading relies on the passages that extract cleanly: section 2 (Preliminaries), where "agreement over an understanding" is defined as the basic event of CT, and the passages that define agreement as organisational closure.

Clean secondary reading of the same mechanism, checked against the Pask text: Tilak, Manning, Glassman, Pangaro & Scott (2024), "Gordon Pask's Conversation Theory and Interaction of Actors Theory: Research to Practice", *Enacting Cybernetics* 2(1), DOI <https://doi.org/10.58695/ec.11>, PDF <https://enacting-cybernetics.org/articles/11/files/68c14b07280a5.pdf>. Used only to confirm the reading of the OCR text: "making observership different for each agent" (p. 7).

What the reading gives, in Pask's terms: agreement is an event between participants A and B that constructs an analogy between them. To the participants the agreement has a consensual truth. To an outside observer the statement "A and B agree" is true or false, but what the observer actually sees is an analogy: A and B are kept as distinct perspectives, and the content of their agreement is a similarity between those perspectives. Disagreement is handled by constructing a further distinction (an analogy or abduction), not by declaring one party wrong. Agreement is thus relative to the distinctions the observer chose to compare.

Limits of the reading: the source is a conversational-theory text about human and machine participants constructing concepts. It does not define a test for two deterministic readers producing equal output, and the 1979 text is an essay, not a specification. The transfer below uses only the part that survives that gap: an agreement verdict is relative to the fields compared.

Original Pask 1970s journal papers ("A Theory of Conversations and Individuals", *IJMMS* 1973) were not reached: no free full text.

## What this plant already does

The plant already reads agreement between observers as a deterministic check and already prints the disagreement in detail. `checks/observer-verdict-agreement.py` computes the same reading twice (the observer's import root and a clean export of reviewed HEAD) and compares them field by field. On disagreement it prints each differing field verbatim, `field[key]: observed=… reference=…` (`checks/observer-verdict-agreement.py:159-170`, output at `:262-274`). `checks/observer-view-agreement.py` does the same for the published evidence block and names the projections the plant does not print, verbatim. The checks are already two-sided: the observer and the published reading are each compared with the reviewed tree, and a disagreement is a finding, not a verdict.

Run live on this wake, the check printed `observer : DISAGREE` with `state`, `services_state` and `renderers_state` observed as UNKNOWN against KNOWN in the reference, plus a list of observer-only problems (the live service and renderer process problems). The published reading was AGREE, age 10 s. That is the detector working on the plant as it is now, and it is not what this review is about.

## What is new here

The plant's AGREE line does not say what it agreed about. `VERDICT_FIELDS` (`checks/observer-verdict-agreement.py:50-52`) is a fixed set of ten fields: `state`, `selection_state`, `source`, `services_state`, `renderers_state`, `problems`, `service_states`, `renderer_states`, `service_roots`, `renderer_roots`. When the check prints `observer : AGREE — the running observer's reading equals the reviewed tree's reading`, a reader cannot tell from that line which fields were compared, nor which parts of the reading (the dashboard lines, the wall text, the evidence block) lie outside the comparison. This is the Pask point applied to a deterministic check: the agreement is an analogy relative to the distinctions compared, so the verdict should carry those distinctions with it.

Concretely, the transfer is a disclosure, not a new test. An AGREE verdict that names its basis (the compared field set and its size, and the reading parts it does not cover) lets a reader see that "the observer agrees" means "on these ten fields", and makes a later reader's question ("does agreement cover the dashboard?") answerable from the output. The same disclosure on DISAGREE is already present through the field lists.

This is not an operational mechanism the plant lacks in substance. It is one missing line of scope on the AGREE path, and it does not change exit codes or the comparison.

## Application

target: checks/observer-verdict-agreement.py

Proposal to genome (owner of checks/). Change, to be tested before any running-code activation:

1. On the AGREE path (both `observer : AGREE` and `published : AGREE`), print one line naming the compared basis: `compared: <N> fields: <VERDICT_FIELDS joined>`. The line is derived from `VERDICT_FIELDS` at print time, not hard-coded, so a future field addition appears automatically.
2. Print one line that names what the reading contains but the comparison does not, as a fixed string drawn from the check's docstring scope (the dashboard text, the wall text and the evidence block are outside `VERDICT_FIELDS`; the evidence block is covered by `checks/observer-view-agreement.py`). This is a pointer, not a comparison. If it cannot be derived mechanically, leave it out and say so in the review, not in the output.
3. Test: add a case to `tests/test_observer_verdict_agreement.py` that runs the check on a fixture where the two readings are equal and asserts that the output contains the compared-basis line with the count equal to `len(VERDICT_FIELDS)`. Without the test, change 1 is unverified.
4. Keep exit codes and DISAGREE output unchanged. The change adds lines on AGREE only.

Detector: the check's own output, read on each scheduled run. Resolver: genome. Detection interval: the scheduled observer-verdict check.

Separately observed, not part of this application and not mine to act on: the live run above shows the observer's reading UNKNOWN while the reviewed tree reads KNOWN, with the observer running an overlay root (`artifacts/operator-overload-repair-20261023T183526Z/runtime-overlays/…`). The dashboard already reports SUBSTRATE UNKNOWN. Routed to genome as the same finding, not as a proposal here.
