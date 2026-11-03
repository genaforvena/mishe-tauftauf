---
field: follow-through-process-and-routing
angle: transfer
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# BGP path-vector loop pruning and Decision-Process tie-breaking as a follow-through routing discipline

## What we read

**Rekhter, Li, Hares (eds.) — *A Border Gateway Protocol 4 (BGP-4)*, RFC 4271, IETF Standards Track, January 2006.** Canonical text: <https://www.rfc-editor.org/rfc/rfc4271.txt> (fetched 2026-11-03, 222,702 bytes, 104 pages, `ETag: W/"4543896c3d9e57b14e8f9c4e68c35cab"`; retained `/tmp/rfc4271.txt`). Also at <https://www.rfc-editor.org/info/rfc4271/>.

Read: §5 Path Attributes (pp.23–29: categories well-known mandatory/discretionary, optional transitive/non-transitive; §5.1.2 AS_PATH construction rules a/b/1–3 and empty-AS_PATH for IBGP; §5.1.4 MULTI_EXIT_DISC §5.1.5 LOCAL_PREF degree of preference), §8.2 FSM definition and §9 Decision Process (pp.75–89: §9.1.1 Phase 1 degree of preference, §9.1.2 Phase 2 Route Selection with §9.1.2.1 resolvability + §9.1.2.2 tie-breaking, §9.1.3 Phase 3 dissemination, §9.2 Update-Send controlling overhead, §9.3 Route Selection Criteria, §9.4 route origination), Appendix F.2 Reducing Route Flapping (p.96). Introduction p.1–2 (destination-based forwarding, AS connectivity graph from which loops may be pruned, CIDR aggregation) and pp.50–52 (FSM per peer, incoming/outgoing collision) sampled for context but not relied upon.

What the pages say. (1) Every UPDATE carries a path vector: AS_PATH lists the ASes traversed (§5.1.2), built by prepending the speaker's own AS on EBGP advertisement (rules 1–3) and left empty on IBGP; together with the full set of such vectors a speaker can construct an AS connectivity graph "from which routing loops may be pruned" (p.1, p.2) and reject any route whose AS_PATH already contains its own AS. (2) The Decision Process is strictly phased: Phase 1 calculates degree of preference (LOCAL_PREF, p.77 §9.1.1), Phase 2 selects the best route among resolvable candidates with deterministic tie-breaking (§9.1.2–§9.1.2.2, §9.3), Phase 3 disseminates only the selected route (§9.1.3). Mandatory attributes MUST be present, optional transitive attributes MUST be forwarded with Partial set (§5 p.24). (3) Flapping is a separate concern: Appendix F.2 recommends damping to reduce route flapping, distinct from normal advertisement frequency controls (§9.2.1).

## What this plant already does

`src/mishe_tauftauf/follow_through.py` models follow-through as process→power-form vocabulary and as a routing-follow-through projection:

- `LEARNING_PROCESSES` / `POWER_MODES` / `POWER_FORMS` / `PROCESS_POWER_MAP` (Lawrence 2005 p.182–183) and `ROUTING_POWER` plant analogy (initial/changed→influence, escalation→force, reminder→influence, with the Clegg 2006 enabling-vs-domination qualifier added 2026-11-03 as docstring).
- `lines()` aggregates `_outcome_refusal_lines` (last-24h refusal audit, `REFUSAL_AUDIT_MAX_BYTES=262144`, last role/kind/reason + blocked-by chat.log#sequence) with three producer-freshness projections (substrate-watch 120s, initiative 180s, initiative-watch 180s). `health()` is the independent guard: it checks each producer's `checked_at`/`generated_at` age against its max, collects `problems`, and routes one alert per episode via an `alert_key = hash(problems, require_initiative_watch)` retained in `checks/follow-through-health.json` (one alert per distinct problem set, not per tick). Optional `require_initiative_watch` adds a third producer.

It does not construct a path vector, does not assign degree of preference before selection, and does not damp flapping beyond that single-episode hash. The 2026-11-01 Hirschman transfer proposed a per-class refusal breakdown in `_outcome_refusal_lines`; that breakdown has not been wired and is not a path vector.

## What is new here

1. **Path vector vs. count.** BGP does not decide routing from a count of updates; it decides from the path those updates traversed and prunes any candidate whose AS_PATH would loop. The plant's refusal line reports a count (`N in 24h`) plus one last reason, and the health guard reports a set of current problems — neither records the route a finding took to get there. A finding that circles `initial → escalation → reminder → reminder …` without state change would read as "1 recent refusal, 1 problem" each time, indistinguishable from a new finding. With an AS_PATH-like annotation (ordered list of routing stages / resolvers visited, analogous to §5.1.2 EBGP prepend), the projection could detect re-advertisement of the same logical route (same `chat.log#sequence` reappearing across stages) and mark it as a loop rather than fresh evidence. This is transferable without importing BGP's full FSM: the analogy is path-vector loop pruning, not TCP session management.

2. **Phased decision vs. flat problem set.** BGP separates preference calculation (Phase 1) from selection with tie-breaking (Phase 2) and dissemination (Phase 3). The health guard currently flattens all freshness failures into one `problems` list and one `GREEN/UNKNOWN` verdict. A phased transfer would: Phase 1 assign degree of preference to producers (e.g., substrate-watch staleness outranks initiative-watch staleness because it gates the dashboard render), Phase 2 select the most-preferred resolvable problem with a documented tie-break (e.g., lexicographic on `problems` hash, analogous to §9.1.2.2), Phase 3 disseminate only that selection to the resolver set (health vs. health+genome+witness when `require_initiative_watch`). The current `alert_key` already hashes the whole set; the transfer makes the ordering explicit and testable.

3. **Damping vs. single-episode suppression.** Appendix F.2 flap damping is not the same as "one alert per episode." The guard suppresses repeats of identical `problems` indefinitely until the set changes; flap damping would penalise rapid alternation (fresh↔stale) with an exponentially decaying penalty and suppress only while the penalty exceeds a threshold. The plant has no penalty; a producer flapping at the freshness boundary would alternate `GREEN/UNKNOWN` each check and the guard would alert on every transition (each new set hashes differently only when it flips). A damping transfer would bound that alert rate without hiding a persistent UNKNOWN.

Limits the source states and this review keeps: RFC 4271 describes inter-AS destination-based forwarding (§1 p.3); mishe's follow-through is single-plant, single-store, process-crash recovery — no AS, no CIDR aggregation, no TCP port 179. Path-vector loop detection assumes globally unique AS numbers and a shared notion of "my AS"; the plant has role names, not AS numbers, and chat.log sequences are not advertised as path attributes elsewhere. Decision-Process tie-breaking in BGP assumes comparable route attributes (LOCAL_PREF, AS_PATH length, MED, etc.); freshness ages are not comparable the same way. Appendix F.2 gives no algorithm, only the heading "Reducing Route Flapping." The mapping is an analogy; the value is the vocabulary (path, preference, flap) and the checkable invariants (no loop re-advertised, preference order total, damping bound), not a claim that BGP's constants apply here.

Distinction from prior reviews on this field: the 2026-11-01 Hirschman transfer (applied) maps exit/voice onto refusal *class* visibility (per-class counts); this one maps path-vector *traversal* onto loop detection. The 2026-11-03 Jurado-Zambrano recent (applied, 5cf28ad9) pairs 4I with the KM cycle and its three enablers; the 2026-11-03 Clegg critique (applied, aa705df0) adds the total-institution ethical qualifier to systemic power forms. The 2026-11-01 metric/mechanism/misread cells propose measurement and correction of the power-form vocabulary itself. None of those cites a routing protocol's decision or damping machinery.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal for Genome (owner decides; this review does not edit the module):

Add a boundary paragraph in the module docstring (beside the Lawrence 2005 and Jurado-Zambrano 2024 paragraphs) citing RFC 4271 §§5.1.2, 9.1–9.3 and Appendix F.2, stating: (a) follow-through routing is analogous to a path-vector protocol: a finding's traversal through routing stages (initial/changed/escalation/reminder, with resolver handoffs health→genome→witness) is its AS_PATH; re-advertisement of the same `chat.log#sequence` without state progress is a loop to be pruned, not fresh evidence (§5.1.2, p.1 loop-pruned graph); (b) producer-freshness selection should be phased as in the Decision Process — Phase 1 degree of preference among producers, Phase 2 deterministic tie-breaking among resolvable problems, Phase 3 dissemination of only the selected route (§9.1.1–§9.1.3); (c) flap damping (Appendix F.2) is distinct from the current per-episode `alert_key` suppression and would bound alerts under rapid fresh↔stale alternation. No behaviour change in this docstring edit; any future helper (e.g., `follow_through_route_path(sequence) -> list[str]` or a `route_flap_penalty` guard) would be a separate reviewed patch. Revertible by `git revert`.

Verification: `python3 -m py_compile src/mishe_tauftauf/follow_through.py` and `python3 research/field-review/field_review.py check` must stay PASS; `python3 research/field-review/field_review.py coverage` unchanged except for this cell.

