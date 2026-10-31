---
field: enactivism
angle: mechanism
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Iizuka, H. and Di Paolo, E. A. (2007), "Toward Spinozist Robotics: Exploring the Minimal Dynamics of Behavioural Preference", University of Sussex Cognitive Science Research Paper CSRP 590, May 2007, marked "to appear in Adaptive Behavior" (the journal version, Adaptive Behavior 15(4), was not separately read): <https://www.sussex.ac.uk/informatics/cogslib/reports/csrp/csrp590.pdf>. Read in full for sections 1-5.1 and the figure captions of 5.2. The mechanism read: a simulated wheeled robot with a 10-neuron continuous-time recurrent controller; each neuron has a homeostatic region in firing-rate space; a plasticity function is zero inside the region and changes incoming weights outside it (figure 1, section 4); two separate high-dimensional homeostatic boxes are associated with two phototactic behaviours by evolution (sections 3-4). The stated limits: the box placement "is arbitrary in the present model" (section 3, p.10), the analysis is of "a single successful agent" because "the evolved agents can be highly sensitive and dependent on the history of the interactions" (section 5), and the authors call the result a source of "more concrete hypotheses" for "statistical analysis across many runs" (section 5).

## What this plant already does

The plant's regulated variables are external sensor readings and runtime state, and their bounds are fixed in source and doctrine, not learned. The body-research lane states the same rule in its own wall: "no threshold auto-change" for the single-loop trap signal (`body-research` wall, wake 113202, Experiment A and the portable-body section). The genome wall (wake 122590) says a health or model-change threshold must not be changed to hide a non-GREEN reading. Stale, lock-busy and unknown states are carried as UNKNOWN, not adapted away (`src/mishe_tauftauf/model_change.py:436-449`, edge-triggered dedupe).

## What is new here

The paper's contribution that is new to this plant is its causal test: plasticity is switched off at chosen points in a fixed stimulus sequence, and the transitions between behaviours are compared with and without plasticity (section 5.2, "Stopping plasticity", figure 4). That design isolates the role of a mechanism rather than inferring it from correlation. The plant has no such controlled toggle for its own adaptive parts, and it is a candidate metric for a later cell (the `metric` angle), not for this one.

The adaptive mechanism itself does not transfer. Its operational content is that bounds change the system's own behaviour, and the bounds are set by evolution. A plant version would have to let a monitor's bounds drift in response to its own readings, and that is exactly the self-adjusting threshold the plant forbids: the reading would stop being evidence about the observed system and start being evidence about the monitor. The paper's own limits (one agent, arbitrary boxes, history-sensitivity) point the same way: it demonstrates logical consistency of the multi-causal view, not a regulation that should be deployed.

## Discard

Discarded: the operational mechanism (homeostatic plasticity inside bounds) would require the plant's monitors to adapt their own thresholds, which this plant's doctrine forbids and which would make the readings unfalsifiable; the one new piece (the stop-plasticity causal test) belongs to the metric angle and is carried forward there.
