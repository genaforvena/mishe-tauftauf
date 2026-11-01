---
field: engine-policy-route-decision
angle: misread
date: 2026-11-01
status: discarded
owner: field-review
---

# engine-policy-route-decision × misread — discarded

## What we read

- Mitzenmacher, "How Useful is Old Information?" (journal-submission draft; the abstract is listed as IEEE TPDS 2000 at https://www.eecs.harvard.edu/~michaelm/abstracts/tpds2000.html), primary PDF: https://www.eecs.harvard.edu/~michaelm/NEWWORK/postscripts/oldinfo-jver-submit.pdf (fetched 2026-11-01 and read as text with pdftotext).
- Points used, quoted from the paper: the abstract's "using global information can actually degrade performance unless used carefully; for example, unlike most settings where the load information is current, having tasks go to the apparently least loaded server can significantly hurt performance." The periodic-update discussion: "herd behavior: herds of tasks all move together to the same locations." A later passage explains the herd is mitigated when customers "have different views of the system, and thus make different choices", and the conclusion stresses "the importance of using some randomness in order to prevent customers from adopting the same behavior".
- Scope limit of the paper: it models many tasks choosing from a shared bulletin board with independent arrivals. It says nothing about a single decision stream that holds its route with hysteresis.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` `decide()` (about lines 352-500) does not pick the apparently least-loaded alias. It keeps the current route while it is healthy ("stay", with `min_hold_ms` hysteresis). When it must switch, it takes the first healthy candidate in allowlist order, same provider first. The rule is deterministic and does not read headroom to rank candidates.
- Stale or unknown evidence is held, not used as a load signal: `engine_usage` marks non-FRESH windows UNKNOWN/STALE/UNATTRIBUTED, and `engine_policy` returns `hold` for any alias that is not FRESH (`_classify_account`, `engine_policy.py` about line 123-128).
- `_load_allowlist` in `src/mishe_tauftauf/engine_watch.py` (about line 129) falls back to one `default` role when `engine/policy.json` is absent. The live `.mishe-tauftauf/engine/` directory has no `policy.json` (checked 2026-11-01: `accounts.json`, `go-b-store`, `last-good.json`, `usage-errors.jsonl`). So one decision stream is made for one role.

## What is new here

The paper's warning maps onto one real mechanism, and the plant already avoids its trigger:

1. **The herd needs several decision makers on one stale snapshot.** The paper's herd is many tasks reading the same old board and all choosing the same server. Here the only live role is `default`. It makes one decision at a time and then holds with hysteresis, so there is no population of simultaneous choosers to herd.
2. **The rule is not "least loaded".** The plant chooses first-healthy in a fixed order, not the best of a stale ranking. That is a deterministic symmetry, which the paper says needs randomness to break. It would herd once several roles share a candidate list and read the same watch cache. It is latent, not active.
3. **Staleness is handled by eligibility, not by age.** A FRESH window can still be up to the report TTL old. Its lag only delays a switch, and the reserve threshold and hold are the brakes. No observation in the tree shows the lag producing a switch storm.

The latent herd is real only under a configuration the plant does not have. The fix, if that configuration arrives, is to break ties among healthy candidates with a seeded random choice (or power-of-two sampling), not to add a test now. Adding such a test today would pin behaviour for a population of one.

## Discard

No live herd precondition: one role, one decision stream, hysteresis, and a FRESH gate. The paper's herd needs many simultaneous choosers on a shared stale snapshot, which `engine/policy.json` would introduce.

Re-open trigger: `engine/policy.json` (or any second role) that shares a candidate list with `default` and reads the same `engine_watch` cache. Then apply the paper's randomised tie-break among healthy candidates and add a test that two roles on one snapshot do not select the same alias.
