---
field: engine-model-routing-and-usage
angle: transfer
date: 2026-10-31
status: discarded
owner: genome
---

# Power of two choices, and why the fallback herd is latent here

## What we read

- Mitzenmacher, "How Useful is Old Information?", IEEE Transactions on Parallel and Distributed Systems, 2000. Read as the author's abstract page, https://www.eecs.harvard.edu/~michaelm/abstracts/tpds2000.html. Quotes used: tasks that "know when making this choice the processor queue lengths from T seconds ago"; "having tasks go to the apparently least loaded server can significantly hurt performance"; "the importance of using randomness to break symmetry in these systems". The citation volume is from a secondary reference, not the journal page.
- Mitzenmacher, "The Power of Two Choices in Randomized Load Balancing", IEEE TPDS 12(10), 2001. Full text read as https://www.eecs.harvard.edu/~michaelm/postscripts/tpds2001.pdf. Used: the "two choices" rule of thumb (stated in the introduction and restated in Section 5, Conclusions), and the paper's own statement that its model extends to "cases where the available load information may be inexact", citing the 2000 paper above for that case.
- Limit: I read the 2000 paper through its abstract, not its full text. The herd claim rests on the abstract and on the 2001 paper's pointer to it.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` (`decide`, lines ~396-490) ranks candidates in a fixed order. It takes the first healthy alias on the same provider, then the first healthy alias in list order. Given one snapshot and one allowlist, the choice is deterministic.
- The usage snapshot is refreshed on a schedule. `engine_usage.py` already treats UNKNOWN, STALE and UNATTRIBUTED as ineligible, so old data does not drive a route.
- In-flight reservations (`_effective_reserved`) are the plant's own correction for information that lags the route it has just taken.

## What is new here

The transfer predicts a herd. If several roles share one stale snapshot and the same ordered fallback, they all pick the same first healthy alias, and the alias fills before the next refresh. The source's remedy is randomness among healthy candidates, or current information.

Checked against the live state:

1. **The herd precondition is absent.** `engine/` has no `policy.json`, so `_load_allowlist` (`src/mishe_tauftauf/engine_watch.py:129`) falls back to a single `default` role over the configured registry aliases (`go-a`, `codex-a`, `minimax-a`). One role means no shared-snapshot crowd, so the herd count is one.
2. **The one in-flight input is not wired.** The live caller (`engine_watch.py:264`) passes `reservations=None`. `_effective_reserved` therefore returns 0 in production. The policy does not see its own in-flight admissions. That is a real gap. It is also the place where a same-pass correction would sit, so it is the observation to route, not a herd to fix.
3. **The fix is not a bounded change here.** Randomising the fallback order changes which alias a route picks. That is a policy decision with cost and fairness effects, and it has no live herd to justify it yet.

## Discard

Discarded: the herd precondition does not hold live (one `default` role, no `engine/policy.json`), so randomised fallback would change routes without an observed herd. The unwired `reservations=None` in `engine_watch.py:264` is routed to genome as an observation.
