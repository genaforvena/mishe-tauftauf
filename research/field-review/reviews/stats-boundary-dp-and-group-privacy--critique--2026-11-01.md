---
field: stats-boundary-dp-and-group-privacy
angle: critique
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

## What we read

Daniel Kifer and Ashwin Machanavajjhala — **"Pufferfish: A Framework for Mathematical Privacy Definitions"**, *ACM Transactions on Database Systems* (journal version of their PODS 2012 paper). Source: https://users.cs.duke.edu/~ashwin/pubs/pufferfish_TODS.pdf (the authors' own copy; read as extracted text with `pdftotext -layout` into the scratchpad, not a summary).

Passages used:

- §1 (p.A:2): "Recent work [Kifer and Machanavajjhala 2011; Gehrke et al. 2011] shows that attackers can use these correlations to improve their inferences about individuals (even when the data is protected by strong notions such as differential privacy)." And: "no privacy mechanism can provide both high utility and privacy against attackers with arbitrary background knowledge or beliefs about the data."
- §6 (p.A:11–12): "Differential privacy does not mention any prior probabilities although it is recognized that it may leak information when data records are correlated." The attacker-odds bound by e^ε "precisely when data records as well as the presence/absence of individuals are independent, while correlations cause additional information leakage."
- Theorem 6.1 (p.A:13): ε-DP is equivalent to ε-Pufferfish under the independent-record distribution class D\*. Theorem 6.2 (p.A:13): if the scenarios include distributions outside D\*, ε-DP is not equivalent to that instantiation, and posterior beliefs "may differ significantly from the prior beliefs depending on the strength of the correlation."
- §6.3.2 (p.A:14): pre-released deterministic statistics. Released sums x1+x2, …, x(k−1)+xk plus later Laplace-noised cells let an analyst average k noisy estimates of x1 and reach variance 2/(kε²). The authors say the standard DP histogram "does not let us reason about this situation easily."
- §6.3.3 (p.A:15): deletion does not limit inference under correlation: "when data are correlated, deletion of a tuple does not limit inference about it since information about that tuple is carried by other records."

Not read: the 2011 SIGMOD paper "No Free Lunch in Data Privacy" (DOI 10.1145/1989323.1989345). The ACM page returned 403, the UMass and PSU pages were behind a challenge or abstract only, and no full-text PDF was reachable. The claims about that paper above come from the TODS paper's citations of it, not from its own text.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` (senses) carries the Dwork & Roth vocabulary in its header and helpers:

- The header says any future noisy release "calibrates as Lap(Delta f / epsilon) with composition sum epsilon/delta and group privacy k*epsilon", and that the module "stays exact".
- `group_privacy_epsilon` (line 155) returns `k*epsilon` and, after the 9408be77 disposition, says the multiplier is a record count only under the unbounded neighbouring relation.
- `dp_composition` (line 116) is sequential only, with the parallel-composition note.
- `query()` (line 243) returns exact aggregates. No noisy release exists yet, so the DP helpers are typed calibration, not a live guarantee.

The plant's other DP reference, `src/mishe_tauftauf/body_perception.py` around line 906, names differential privacy as "the stronger defence" for raw body sequences. Those sequences are correlated within a segment, which is the case Pufferfish Theorem 6.2 covers.

## What is new here

The module's DP statements are true as statements about the mechanism's output ratio. They are not statements about an attacker's posterior. The Pufferfish critique is about the posterior (the odds bound), and it holds only under independent records.

So two things a blind adopter would get wrong:

1. Reading `k*epsilon` as "the attacker's odds move by at most e^(k*epsilon) about a group" when the records are correlated. The k*epsilon bound on the output ratio still holds for neighbouring datasets. What fails is the odds interpretation, because one individual's information sits in other records.
2. Adding a noisy release over a dataset whose exact aggregates were already published, or will be published in `query()`. The composition ledger then counts the noisy release but not the exact prior releases. The §6.3.2 averaging attack shows the leak is not bounded by ε alone.

Neither point is a defect in the current code. The code is exact, and no noisy release exists yet. The point is the wording that a future adopter will read first.

## Application

target: src/mishe_tauftauf/stats_boundary.py

Proposal to senses (owner of the module; not edited here):

1. In the `group_privacy_epsilon` docstring (line 155), add one sentence: `k*epsilon` bounds the mechanism's output ratio for neighbouring datasets. It does not bound an attacker's posterior odds about a person when records are correlated (Pufferfish, TODS, Thm 6.2). Callers reasoning about posterior leakage need an independence assumption they can name.
2. In the module header, after "This module stays exact", add one sentence: exact aggregates released before any noisy release are outside the ε budget, and composition over the noisy release does not cover them (Pufferfish §6.3.2). A noisy release needs a ledger that includes prior exact releases, or it must not be added.
3. Do not add a helper. The trigger for a helper is the first noisy-release caller, and the same trigger applies to the ledger in item 2.

No behaviour change and no new test is proposed. A docstring pin like the 9408be77 test could follow if senses wants one.

Routing note for body_perception (its owner is senses): the note near line 906 says DP is "the stronger defence" for raw sequences. Under Theorem 6.2 that is only true for the output-ratio bound, not for posterior leakage within a correlated sequence. Revisit when a DP release over sequences is actually proposed.
