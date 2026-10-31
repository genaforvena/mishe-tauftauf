---
field: wall-patches-and-operator-views
angle: metric
date: 2026-10-31
status: discarded
owner: senses
---

# Wall patches and operator views — metric: patch-leaving rate (discarded, source unreachable)

## What we read

- Intended primary source: Peter Pirolli and Stuart Card, "Information Foraging", Psychological Review 106(4), 643–675, 1999, DOI 10.1037/0033-295X.106.4.643, which states the patch-leaving rule (leave a patch when its instantaneous rate of gain falls to the environment's average rate; Charnov's marginal value theorem, 1976).
- Attempts, all failed to reach the text:
  - https://doi.org/10.1037/0033-295X.106.4.643 redirects to https://doi.apa.org/doi/10.1037/0033-295X.106.4.643, then to https://psycnet.apa.org/doi/10.1037/0033-295X.106.4.643. The page returned only "APA PsycNet / Loading...": no abstract, no full text.
  - https://doi.org/10.1016/0040-5809(76)90040-X (Charnov 1976, "Optimal foraging, the marginal value theorem") redirects to https://linkinghub.elsevier.com/retrieve/pii/004058097690040X, which returned only "Redirecting".
  - http://www2.parc.com/istl/projects/ia/papers/ir-theory/ir-theory.pdf: the host does not resolve from this machine.
- A web search returned the bibliographic record and secondary explainers (UX and lecture summaries) that restate the marginal-value rule. Those are summaries of a summary. They are not a reading, so no passage from them is cited as the source.

## What this plant already does

- Nothing here is measured as a rate of gain per patch. `wall_view.py` and `wall_review.py` render and review wall text; neither computes a per-wall gain rate or compares it with an environment average. This is checked by reading the function lists of both files, not by a run.
- The wall-patch gate and reviewer are in `wall_patch.py` and `wall_review.py`. The metric would have targeted `wall_view.py` (`render()`), which is senses' surface.

## What is new here

Nothing that this review can claim. The idea (a wall section whose new-evidence rate falls below the tape's average is a candidate to leave, so a stale section is flagged) is a hypothesis built from secondary summaries. It has no primary citation here. Under the lane's rule, a summary of a summary is not a reading, so the mechanism is not proposed as a change.

## Discard

Source unreachable: the Pirolli & Card (1999) and Charnov (1976) full texts did not load from this host (APA PsycNet and Elsevier returned loading or redirect pages only; the PARC PDF host does not resolve). The cell is discarded for that reason, not for a judgement about the metric. To reopen it, a reachable copy of Pirolli & Card 1999 or Charnov 1976 is needed; the metric would then be scored against a deterministic count of new tape entries per wall section.
