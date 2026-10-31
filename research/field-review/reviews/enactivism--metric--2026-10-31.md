---
field: enactivism
angle: metric
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/model_change.py
---

## What we read

Iizuka, H. and Di Paolo, E. A. (2007), "Toward Spinozist Robotics: Exploring the Minimal Dynamics of Behavioural Preference", University of Sussex Cognitive Science Research Paper CSRP 590: <https://www.sussex.ac.uk/informatics/cogslib/reports/csrp/csrp590.pdf>. Read section 5.2 "Stopping plasticity" (pp. 17-18) and the Figure 4 caption, extracted from the PDF with pdftotext. The metric read: the experiment is started from the Figure 2 initial configuration; plasticity is turned off at the 20th or 50th of 100 light presentations, with weights frozen at their current values; the measured quantities are the distance to each light source and the proportion of homeostatic neurons, compared with the unchanged run. The stated result: "the agent's behaviour does not switch in the period tested", and "After stopping the plastic mechanism, neither the behaviours nor the internal dynamics seem to change." The stated limits, verbatim: "it is possible for a CTRNN to switch the internal dynamics into another region without changing the network weights", so "the agent's behaviour transition might be happening without explicit synaptic plasticity", and "at least in the examples explored, non-plastic transitions are not common." The paper's section 5 also limits the evidence to "a single successful agent", with only two stop points.

## What this plant already does

The plant has stateful, edge-triggered logic whose state changes what gets sent. In `src/mishe_tauftauf/model_change.py:436-449` the dedupe state is held in `alerts` through `dispositions.edge(...)`, and an age ladder widens the resolver from health to genome. Repeat messages are suppressed by the append-once guard, so the same reading can produce different routing depending on what was already recorded. The plant has no test that holds that state fixed at a chosen point and replays the same sequence, so nothing shows whether a later routing transition depends on the stored state or on the readings.

## What is new here

The paper's metric is a counterfactual cut: freeze the adaptive component at a chosen point in a fixed input sequence, then check whether the later behaviour transitions still happen. If they do not change, the component's accumulated state is not what drives the transition. The plant can run the same cut on its edge-triggered state. Replay a recorded reading sequence through `model_change` twice, once normally and once with the `alerts` dedupe state frozen after event k, and compare the routing messages after k. A transition that appears in both runs is carried by the readings; one that appears only in the unfrozen run is carried by the stored state. The paper's own limit applies here too: a result for one sequence and two cut points is a single observation, not a rate, and a "no change" outcome does not rule out other routes to the same transition.

## Application

target: src/mishe_tauftauf/model_change.py

The proposal for the owner is a deterministic replay test, not a change to the routing rule. Add a test that feeds a fixed sequence of model-change readings through the edge-triggered path, freezes the dedupe state after k events, and asserts which routing messages appear after k with and without the freeze. Record the result as a metric row with the cut point, the message counts, and the two-run comparison. Do not alter the thresholds or the age ladder to make the test pass. Owner decides whether to build it; the review does not edit the organ.

Owner is marked genome as the escalation resolver in the code path. I did not verify which role owns this file's day-to-day changes, so the routing message asks genome to redirect it if health owns it.
