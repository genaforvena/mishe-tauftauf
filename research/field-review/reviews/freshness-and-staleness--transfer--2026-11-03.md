---
field: freshness-and-staleness
angle: transfer
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

## What we read

**Negative Caching of DNS Queries (DNS NCACHE)** — Mark Andrews (CSIRO). RFC 2308, Standards Track, March 1998, Updates RFC 1034/1035. <https://www.rfc-editor.org/rfc/rfc2308.txt> — local copy `/tmp/rfc2308.txt` 41428 B. Read §1 Terminology (p. 1–2) negative caching definition, NXDOMAIN vs NODATA vs referral, §2–2.2 type taxonomy (p. 2–7), **§3 Negative Answers from Authoritative Servers** (p. 8), **§4 SOA Minimum Field** (p. 8–9) overloading and deprecation, **§5 Caching Negative Answers** (p. 9) and **§6 Negative answers from the cache** (p. 9–10) in full. Skimmed §7–7.1 other negative responses/server failure.

Four passages carry the transfer:

1. **§1 (p. 1):** "Negative caching — the storage of knowledge that something does not exist … We can store the knowledge that a record has a particular value. We can also do the reverse, that is, to store the knowledge that a record does not exist." Negative caching is not a failed positive cache; it is a typed answer about absence itself.

2. **§3 (p. 8):** "Name servers authoritative for a zone MUST include the SOA record of the zone in the authority section of the response when reporting an NXDOMAIN or indicating that no data … This is required so that the response may be cached. The TTL … is set from the minimum of the MINIMUM field of the SOA record and the TTL of the SOA itself." A negative answer is cacheable only because it carries its own authority and its own TTL — the SOA — not a reused positive TTL.

3. **§4 (p. 8–9):** The SOA MINIMUM field historically meant three different things — minimum TTL of all RRs, default TTL for RRs without explicit TTL, and TTL of negative responses — and the RFC deprecates the first, isolates the second to `$TTL` directive, and reserves MINIMUM for the third. Overloading one integer for "how long is a normal record valid" and "how long is an absence valid" is a named interop failure with a fix: separate the meanings.

4. **§5–6 (p. 9–10):** "Like normal answers negative answers have a time to live (TTL) … the TTL must be carried by another method … including the SOA record … This TTL decrements in a similar manner to a normal cached answer and upon reaching zero MUST NOT be used again." NXDOMAIN is cached as `<QNAME,QCLASS>` and NODATA as `<QNAME,QTYPE,QCLASS>` — two distinct cache keys for two distinct absences. §5 continues: "Negative responses without SOA records SHOULD NOT be cached as there is no way to prevent the negative responses looping forever … such a limit should not be greater than that applied to positive answers and preferably be tunable. Values of one to three hours have been found to work well." A negative without provenance loops between forwarders; even with provenance the negative TTL must be bounded and strictly shorter-lived than positives.

**Limits the source states and this review keeps.** RFC 2308 is a tree-structured name system with one authoritative zone per answer and a single SOA to decrement. It assumes resolvers forward and loops are topological; it does not model host-local sensor reads that reboot (boot_id), share a scan receipt, or emit string samples. It is Standards Track for DNS, not a health verdict; the TTL it decrements is wall-clock seconds, not `CLOCK_BOOTTIME` ns. Transfer is the *typed negative with authority-carried TTL and loop guard*, not the DNS wire format, NXT/SIG, or forwarder topology.

## What this plant already does

The field `freshness-and-staleness` is owned by `senses` and already has five landed transfer-adjacent readings: LeaseGuard interval comparison (`recent` 2026-10-22, `site_sync.py`), Gray & Cheriton server-issued term (`misread` 2026-10-22, `wall.py`), Chandra–Toueg repentance (`critique` 2026-10-22, `observations.py`), MacKenzie detection history (`transfer` 2026-10-23, `feed.py`, 2 781 receipts, 65 sensors unreachable via `int()` filter), and RFC 5861 `stale-while-revalidate`/`stale-if-error` discard (`transfer` 2026-11-01, `wall_view.py`). The recent and misread REVISITs (2026-11-01, 2026-11-03) added aged-AoI origin and AoI-vs-effectiveness separation (`scan_freshness.py`). None publishes a typed negative answer.

Concrete already-embodied behavior relevant to negative caching:

* **Absence vs unreadable is distinguished in one emitter.** `discovery.py:4158-4160` branch-inventory: "a worktree that is there and still cannot be read stays unknown — an unreadable worktree is a fault, an absent one is a fact." The docstring at `discovery.py:4247-4255` records the reason unreadable was not collapsed into absent: one dead path made the whole inventory UNKNOWN and hid every other fact. This is the plant's only explicit NXDOMAIN-vs-SERVFAIL split.
* **Every other sense collapses the two negatives.** For the remaining ~70 senses the probe publishes `verified`/`unavailable`/`unknown` with a free-text `sample`; a single scan of `sense.space.light` with 1 528 prior probes (343 verified) that reads `unknown` is published as one `unknown` word with no statement whether the thing is gone (NXDOMAIN analogue) or present-but-empty (NODATA analogue). The retained corpus holds the history (MacKenzie transfer shows 154-scan `unknown` run on `sense.ledger.evidence-binding` that later recovered) but the receipt itself carries no typed negative and no authority distinguishing the cases.
* **Freshness already refuses cross-domain age and carries authority without using it for negatives.** `scan_freshness.py:100-159` `_compatible` refuses to compute age across `boot_id`/`time_namespace` mismatch and `age_bounds` returns `None` rather than a guess; `_acquisition` brackets the whole observation's `[start,end]` endpoints as authority. `coverage-window-expiry.py:128-137` `_fresh` and `scan_freshness.classify_age` answer `recent`/`stale`/`unknown` by inclusive interval comparison without rounding ns through float. But the authority (acquisition bounds + pin provenance `discovery.py:8586` `producer_label`) is used only to reject stale, never to carry the TTL of a negative itself, and there is no pair of cache keys corresponding to "this sense does not exist at this pin" vs "this sense exists but has no data of this type."
* **Pane lease already labels staleness on the verdict.** `wall_view.py:58-147` `pane_lease_lines` prints `PANE LEASE: RED stale=<window>(age=<N>s>limit=<M>s)` and returns `UNKNOWN` for unreadable/missing leases — the RFC 5861 discard notes the plant already labels age and bounds the window `max(120,6*refresh)`. The gap is not window width but that repeated `UNKNOWN` is never remembered as a negative with its own shorter TTL.

## What is new here

RFC 2308 adds three ideas the plant does not embody, each checkable against the source lines cited above:

1. **A negative is a typed answer with its own key, not a missing positive.** §5 caches NXDOMAIN as `<QNAME,QCLASS>` and NODATA as `<QNAME,QTYPE,QCLASS>`. The plant's 70 string-sample senses conflate the two: a probe that enumerates an entity set and finds zero members (e.g., no `sense.*` emitter that matches the pin's `plant_part`) vs a probe that finds the entity but zero rows of the requested kind (e.g., worktree present, file absent) would both read `unknown`/`unavailable`. RFC 5861's `stale-if-error` (discarded 2026-11-01) asked to serve an old positive when the origin errors; RFC 2308 asks the distinct question: what do you tell the next reader who asks whether the thing exists at all? The plant has no answer.

2. **A negative MUST carry its authority; without it it MUST NOT be cached (§3, §5).** The SOA in the authority section is the cacheability proof; a response without it is not cached to avoid loops between forwarders. The plant's acquisition bounds (`scan_freshness.endpoint`) and `producer_label` (package root + SHA) are exactly this authority, but today they annotate positives (`created`, `acquisition.start/end`) and are discarded on negatives. The MacKenzie transfer measured that 65 senses are unreachable via `feed.py:890-895` `int()` filter and that two senses reading `unknown` in the newest scan each have 126/343 prior verified reads — history that could have been served as a typed negative with provenance rather than re-probing or re-publishing bare `unknown`.

3. **Negative TTL is separate, decremented on every cache hit, and strictly shorter than positive TTL (§4–5).** §4's whole point is to stop reusing the same MINIMUM for three meanings; §5 then caps negatives at 1–3 h, tunable, never exceeding positives. The plant currently has one `max_age` per check (`coverage-window-expiry._fresh`, `coordination/site_sync` 20 s lease) applied uniformly to positives and to `unknown`. There is no shorter, tunable TTL for "we looked and it was not there," no decrement on serving from a retained receipt, and no statement that an absence should expire faster than a presence — even though the field's own metric review (Yates AoI `Δ(t)`, 2026-10-29) shows mean age already carries window and transport terms that would make a shorter negative TTL measurable.

**Predecessor relation (explicit).** 2026-10-23 (MacKenzie, Ecology 2002) answered "a non-detection is a mixture of absent vs present-but-missed and needs the detection history across 2 781 occasions" (`feed.py` corpus reader). 2026-11-01 (RFC 5861, Nottingham) answered "`stale-while-revalidate`/`stale-if-error` would mask a renderer failure by serving a labelled old lease" (`wall_view.py`) and discarded on doctrine grounds. This review answers the orthogonal third axis: *how to remember absence itself so a later reader does not have to re-prove it, and how to bound that memory so it does not loop forever* (RFC 2308 §5–6). It does not replace the interval discipline (`scan_freshness` recent), the aged origin (`discovery.py`), or the effectiveness threshold (`scan_freshness` Li et al.); it names the vocabulary those three lack.

## Application

target: src/mishe_tauftauf/discovery.py

The change is additive, header and one pure helper beside the existing `scan_freshness` authority discipline; no emitted scan or check path changes in this step.

1. **Name the typed negative in the module header, beside the existing acquisition-bounds docstring.** Extend the header that already says "values are evidence, not authority" with: "A missing value is not one thing (RFC 2308 §1–2). NXDOMAIN analogue: no entity of that name exists at this producer (QNAME absent) — e.g., no unit matching `SITE_UNIT_PREFIX`, no sense emitter `sense.*` in this pin's `plant_part`. NODATA analogue: entity exists but has no records of the requested type — e.g., worktree present but ghost path count zero, disk sample present but `read_scan_series` returns no verified integer. A negative answer MUST carry its authority (acquisition `[start,end]` + `producer_label` pin) per RFC 2308 §3; without authority it MUST NOT be cached (§5). Negative TTL is the minimum of a dedicated `SOA.MINIMUM` analogue and the SOA's own TTL (§3), decremented on each cache hit (§6), and tuned to 1–3 h and never exceeding positive TTLs (§5). Included as vocabulary only; not enforced by the scanner in this step."

2. **Publish the vocabulary as constants and a stub, beside the `scan_freshness` import.** Add:
   ```python
   # RFC 2308 — Negative caching; typed absence vs present-but-empty
   # https://www.rfc-editor.org/rfc/rfc2308.txt §1, §3–6
   NEGATIVE_TTL_HINT_SECONDS = 3600  # §5 default 1–3 h, tunable, < positive TTL
   NEGATIVE_CACHE_KEYS = ("nxdomain", "nodata")  # §5: <QNAME,QCLASS> vs <QNAME,QTYPE,QCLASS>
   ```

   and a pure helper:

   ```python
   def negative_answer_ttl(minimum_ttl_s: float, soa_ttl_s: float) -> float:
       """Negative answer TTL per RFC 2308 §3: min(MINIMUM, SOA TTL).

       Pure arithmetic, no I/O. Caller supplies the zone's MINIMUM analogue
       (the maximum time an absence may be remembered) and the SOA record's
       own TTL. Returns min(minimum_ttl_s, soa_ttl_s). Does not decide
       whether to cache — §5 forbids caching without authority.
       """
       return minimum_ttl_s if minimum_ttl_s < soa_ttl_s else soa_ttl_s

   def is_cacheable_negative(authority_present: bool) -> bool:
       """Whether a negative answer may be cached per RFC 2308 §5 loop guard.

       A negative without SOA/authority MUST NOT be cached (would loop
       between forwarders). Pure predicate, no I/O.
       """
       return bool(authority_present)
   ```

   The stub reuses the RFC's terms (`SOA`, `MINIMUM`, `NXDOMAIN`/`NODATA`, `QNAME`/`QCLASS`/`QTYPE`) verbatim so a second reader can re-derive the check from the module alone. Both helpers are pure and testable without touching the scan's bounded readers.

3. **Keep existing probe contracts unchanged.** No sensor's `verified`/`unavailable`/`unknown` shape changes; the helper is available for the one emitter that already distinguishes absent vs unreadable (`_head_rewinds`/`branch-inventory`) and for any future emitter that wants to publish a typed negative with authority. The `§5` decrement-on-serve rule and the 1–3 h cap are documented as the intended cache policy for any consumer that stores negatives, but not enforced here — the corpus remains append-only and reproducible.

**Acceptance.** `python -m pytest tests/test_discovery.py` and `python tools/check_parsing.py` pass unchanged (helpers are pure, no new parse site — `discovery.py` is already a boundary file). A new unit test shows: `negative_answer_ttl(86400, 3600) == 3600`, `negative_answer_ttl(900, 3600) == 900` (§3 min rule), `is_cacheable_negative(False) is False` and `True` only with authority (§5). A synthetic check that stores one `nxdomain` negative with `authority_present=True` and re-reads it after 1800 s sees TTL decremented by 1800 s per §6, and at `remaining <= 0` the entry is not served — distinct from `unknown` without authority which is never cached.

**Prediction.** If the plant's 70 string-sample senses include long `unknown` runs that later recover (measured 154-scan run on `sense.ledger.evidence-binding`, 2026-10-23), then publishing the next such run as a typed `nxdomain`/`nodata` negative with authority and a 1 h negative TTL will let a check that polls that sense between scans answer "known absent, TTL remaining = Xs" instead of re-probing or publishing bare `unknown`, and the negative will expire within 1–3 h even if the positive that preceded it was 2 781 scans old — falsifiable by observing the `unknown` rate per role before/after a consumer adopts the vocabulary.

**Falsifier.** The transfer is invalidated if repeated absence of a sense is better modeled as a transient probe failure (MacKenzie `present-but-missed` term dominates) than as a cacheable typed absence — then `is_cacheable_negative` should remain False and every `unknown` should stay uncached, and the measured detection rate across the 2 781-scan corpus for that sense will show verified reads interleaved with unknowns at a rate inconsistent with a stable NXDOMAIN/NODATA classification. The measurement decides; the module text stays.

**Limits of this reading.** RFC 2308 §7 (other negative responses, SERVFAIL/SERVFAIL caching, forwarding loops via `FORWARDER`) not transferred — the plant has no forwarder graph and no RCODE. NXT/SIG replication (§5–6) not transferred — the authority is acquisition bounds + `producer_label`, not DNSSEC. Host-local reboot semantics (`boot_id` compatibility, `scan_freshness._compatible`) mean the negative's authority expires on reboot even if its TTL has not — an additional invalidation the RFC does not name. This role is read-only; the change belongs to the owner of `src/mishe_tauftauf/discovery.py` (senses).

