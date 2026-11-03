---
field: repo-history-and-host-throttle
angle: transfer
date: 2026-11-03
status: applied
owner: discover
target: src/mishe_tauftauf/discovery.py
---

# Certificate Transparency's append-only audit as a checkpoint for HEAD history: STH, Merkle consistency, and monitor vs auditor

## What we read

- Primary text: B. Laurie, E. Messeri, R. Stradling — **Certificate Transparency Version 2.0**, RFC 9162 (Experimental, obsoletes RFC 6962), December 2021. Canonical URL https://www.rfc-editor.org/info/rfc9162 plain text https://www.rfc-editor.org/rfc/rfc9162.txt — retrieved 2026-11-03 as `/tmp/rfc9162.txt` 128266 B sha256 `a0e432ce7580c99fcc8faf0cf3f6191f9ce6b4864f949158e020d4ecb295081f` (2961 lines). Read in this pass: §1 Introduction (append-only log claim), §2.1 Merkle Trees (§2.1.1 Definition, §2.1.3 Inclusion, §2.1.4 Consistency), §4 Log Format and Operation (append-only Merkle Tree, SCT promise within MMD, STH signing), §4.9–4.10 Merkle Tree Head / Signed Tree Head, §5.3 consistency-proof API, §8–§8.3 Clients/Monitor/Auditing, §11.3 Misbehaving Logs / split-view limit. Quoted as the RFC states it:

  - "The append-only property of each log is achieved using Merkle Trees, which can be used to efficiently prove that any particular instance of the log is a superset of any particular previous instance and to efficiently detect various misbehaviors of the log (e.g., issuing a signed timestamp for a certificate that is not subsequently logged)."
  - "A log is a single, append-only Merkle Tree of submitted certificate and precertificate entries."
  - "The hash of a list with one entry (also known as a leaf hash) is: MTH({d[0]}) = HASH(0x00 || d[0])."
  - "MTH(D_n) = HASH(0x01 || MTH(D[0:k]) || MTH(D[k:n])), where: || denotes concatenation" with largest power-of-two split, and "Note that the hash calculations for leaves and nodes differ; this domain separation is required to give second preimage resistance."
  - "Merkle consistency proofs prove the append-only property of the tree."
  - "An SCT is the log's promise to append to its Merkle Tree an entry for the accepted submission. Upon producing an SCT, the log MUST fulfill this promise by performing the following actions within a fixed amount of time known as the Maximum Merge Delay (MMD): Allocate a tree index to the entry … Calculate the root of the tree. Sign the root of the tree (see Section 4.10)."
  - "The log auditing mechanisms described in this document can be circumvented by a misbehaving log that shows different, inconsistent views of itself to different clients. Therefore, it is necessary to treat each log as a trusted third party."
  - §8 role split: Monitor (§8.2) "asking them regularly for all new entries" to watch named domains; Auditing (§8.3) demanding inclusion/consistency proofs against an STH; inclusion (§5.4) and `get-all-by-hash` (§5.5) return STH + inclusion + consistency together.

- Local primary text (the plant part being transferred onto):

  - `src/mishe_tauftauf/discovery.py` `_head_rewinds_reading` ll.3783–4057: dropped-commits = distinct SHA in last `_HEAD_REWIND_REFLOG_LIMIT=200` HEAD reflog entries not in `git rev-list HEAD` reachability; per-path `git apply --check -R` (lossless) / forward-apply (absent) / neither (needs-review) with `review_basis`/`reverted_at_head`/`superseded_content` splits; `dropped_ancestry = rev-list dropped ^HEAD` count for coverage gap; sample names window `last-200-reflog-entries` + `gc.reflogExpireUnreachable` 30 d; state drift/verified/unknown, never from count.
  - `src/mishe_tauftauf/reflog_boundary.py` 88 lines: three decoders (`reflog --date=unix`, `reset: moving to HEAD`, stash) as the only split site for `reset_origin`.
  - `src/mishe_tauftauf/slice_throttle.py` + `cpu_stat_text.py` + `host_containment.py` throttle side (non-hierarchical 200-entry scope, `cpu.stat.local` inherited, count-vs-time) already covered by prior 2026-11-01 mechanism/metric/transfer — not re-argued here; this review isolates the history half of the field.
  - Live receipt 2026-11-03T03:33:06Z `.mishe-tauftauf/discovery/latest.json`: `created` + `acquisition.{start,end}.utc_ns` + `bounds_ns` + `node` + `observations[]` with `producer_label`; head-rewinds observation at that receipt `dropped=1` class window still single-digit (critique 2026-11-03 reported `10/198 distinct` shas prior window).
  - Not re-read: RFC 6962/CT 1.0 diff details in §1.3, CMS precertificate profile §3.2, TransItem encodings §4.3–4.8, IANA registries §10, Appendix history-tree vs Merkle differences beyond §2.1.1 note.

## What this plant already does

- The head-rewinds sense is a bounded, general-case dropped-commit detector. It counts every SHA that left HEAD in the last-200 reflog window (not just `reset: moving to HEAD`), distinguishes resets/branch-switches/amends/rebases only via the count's definition, and decides loss per path via `apply --check` against an extracted HEAD tree. `reset_origin` labels are correlation-only and "never feed its state". Dispositions are author claims beside the reading (`head_rewind_dispositions.py` blob-id bound), not a clearing of drift.
- The window and its expiry are already named after the 2026-11-03 critique: "Counting rule" paragraph + "Threats to validity (Rapaport et al. 2025, arXiv:2509.09294 §VII)" block note window-invisible / merged-episodes / metadata-masked / horizon-debatable — both detectors (SWH S1-vs-S2 archival visits and local reflog) share them, local tighter.
- Discovery already publishes a receipt (`latest.json`) with wall-clock acquisition bounds and a `producer_label` (package root @ SHA) but it is not a history checkpoint: no `tree_size` (HEAD commit count), no `root_hash` covering history, no signature, no consistency link to the previous receipt's HEAD. A quiet `dropped=0` window is therefore observation-only and cannot be audited against a prior STH, and two successive quiet windows have no `PROOF(m,D_n)`-like link proving the second is a superset of the first.
- The throttle half of this field already has a second reading proposal (PSI `some` stall time vs `nr_throttled/nr_periods` count, `cpu.stat.local` inherited `throttled_usec`). Those transfers are observation-only and deliberately add no new verdict; they remain in scope but are not reused here.

## What is new here

RFC 9162 formalises the exact property the rewind detector is informally checking — append-only history — and names three mechanisms the plant does not have:

1. **A hash that covers history with domain separation, and a Signed Tree Head that binds size + root + time.** CT: `MTH({d[0]}) = HASH(0x00 || d[0])`, interior `HASH(0x01 || left || right)`, root signed in an STH (§4.10) with `tree_size`, `timestamp`, `sha256_root_hash`, `signature`. Git's commit hash is the plant's already-existing analogue: `commit SHA = hash(commit object || tree || parents || author/committer)` already covers ancestry (a SHA-1/SHA-256 history-tree hash, not the RFC's balanced Merkle shape, but still a root committing to history). The plant today stores no `(tree_size, root_hash, timestamp)` STH for HEAD — not even an unsigned checkpoint — so a third party cannot ask "is this HEAD consistent with the HEAD I saw yesterday?" without re-reading reflog.

2. **A consistency proof that a new tree is a superset of an old one, distinct from an inclusion proof for one entry.** CT: `PROOF(m, D_n)` proves `MTH(D[0:m])` is the prefix of `MTH(D_n)` (§2.1.4); inclusion `PATH(m,D_n)` proves leaf `d[m]` is in `MTH(D_n)` (§2.1.3). Git's direct analogue is `git merge-base --is-ancestor prev_HEAD curr_HEAD` (or `rev-list prev..curr`) — the consistency check is "is every commit reachable from STH_m reachable from STH_n?" The rewind detector's `dropped-commits` is closer to a consistency *failure* signal (a SHA in reflog not in HEAD) than to a proof; it answers "what left HEAD inside this window?" not "is HEAD_n an extension of HEAD_{n-1}?" Altered Histories' S1-vs-S2 snapshot diff and CT's `get-entries` + consistency API are the external equivalent.

3. **Monitor vs auditor, and the split-view limit that makes gossip necessary.** CT deliberately splits Monitor (§8.2 — downloads all new entries, watches named domains) from Auditor (§8.1/§8.3 — given an SCT or STH, fetches inclusion + consistency proofs, compares monitors' copies). §1 then concedes: without gossip, "a misbehaving log that shows different, inconsistent views of itself to different clients" circumvents auditing and "it is necessary to treat each log as a trusted third party." The plant's analogue: `discovery.scan()` is the monitor (bounded 200-entry download of reflog + HEAD reachability, external `discovery/latest.json` distribution); there is no auditor that, given yesterday's STH (`prev HEAD SHA + tree_size`), verifies consistency against today's HEAD via an inclusion/consistency proof. The same split-view exists: origin/main or SWH archive vs local checkout can show different histories; the critiques' "window-invisible" and "multiple alterations merge" are CT's "MMD / archival frequency" in another form — CT mandates detecting a broken append-only log is the point, not the interval length.

The 2026-11-01 transfer added a *second* CPU reading with a different definition (PSI some wall-time stalled vs throttled-period count) to check the ratio. This transfer adds the *history* analogue: a second history reading that checks the reflog window — a checkpoint-consistency reading that pairs with the dropped-commit count. Both keep verdict observation-only.

Honest limits of this reading: single RFC read (2961 lines plain text + fetches above, not the [RFC8391]/[CrosbyWallach] history-tree references the RFC cites); Git commit hash covering ancestry is noted as analogous, not claimed to equal CT's `HASH(0x00||leaf)` / `HASH(0x01||left||right)` construction — Git's tree is a DAG with file-tree hashes, CT's is a binary Merkle over linear entries, shapes differ and the RFC's domain separation does not exist in Git; `rev-list --count HEAD` as `tree_size` analogue is linearizable on `main` only — merges make linear entry index debatable; gossip (§8 gossip/ gossip still "outside scope" in RFC 9162) is not re-derived; MMD is a log-operator promise, not a git property — mapping `MMD -> gc.reflogExpireUnreachable / scan interval` is an analogy for window length, not a normative bound.

## Application

target: src/mishe_tauftauf/discovery.py

Proposal for discover (owner of `discovery.py` per field registry `plant_part`). Not edited here — read-only lane.

1. **Carry the CT analogue into the `_head_rewinds_reading` docstring.** After the existing "Counting rule" + "Threats to validity (Rapaport et al. 2025, arXiv:2509.09294 §VII)" paragraphs, add a "Certificate Transparency analogue (RFC 9162)" paragraph quoting the RFC's own limits verbatim and mapping them locally:

   > Certificate Transparency analogue (RFC 9162, https://www.rfc-editor.org/rfc/rfc9162.txt §1, §2.1.1, §2.1.4, §4–4.10, §8): CT proves append-only via Merkle Tree Hash MTH(D_n) with leaf HASH(0x00||d[0]) and interior HASH(0x01||MTH(D[0:k])||MTH(D[k:n])) (domain separation for second-preimage resistance), Signed Tree Head STH {tree_size, timestamp, sha256_root_hash, signature}, inclusion PATH(m,D_n) and consistency PROOF(m,D_n) (§2.1.3–§2.1.4). "A log is a single, append-only Merkle Tree" (§4); STH is the signed root after "within Maximum Merge Delay (MMD): Allocate a tree index … Calculate the root … Sign the root" (§4). Git analogue here: HEAD SHA is the history root (commit object hashes tree+parents, not CT's binary Merkle shape — analogous coverage, different construction); tree_size ≈ `git rev-list --count HEAD` on linear `main`; consistency is `git merge-base --is-ancestor STH_m STH_n` (Git's PROOF(m,D_n)). Local head-rewinds counts "a commit left HEAD inside last-200 window", CT/STH proves "HEAD_n is a superset of HEAD_m" — complementary checks. Without gossip, "a misbehaving log that shows different, inconsistent views of itself to different clients" circumvents auditing and "it is necessary to treat each log as a trusted third party" (§1) — same for origin/SWH vs local checkout: a rewind can be split-view across mirrors. Cite RFC URLs + § numbers verbatim so a second reader can re-derive without this review.

2. **Publish the audit vocabulary as inert constants beside `ALTERATION_CATEGORIES` / `DETECTION_VALIDITY_LIMITS`.** Add, with no behavior change:

   ```python
   # RFC 9162 — append-only audit vocabulary (analogue; Git history is DAG, CT is linear Merkle)
   # MTH leaf = HASH(0x00 || leaf), interior = HASH(0x01 || left || right) — domain separation §2.1.1.
   # STH = {tree_size, timestamp, sha256_root_hash, signature} §4.10; MMD is promise window §4.
   CT_MERKLE_LEAF_PREFIX = 0x00  # §2.1.1 leaf domain
   CT_MERKLE_NODE_PREFIX = 0x01  # §2.1.1 interior domain
   CT_CHECKPOINT_KIND = "sth-analogue"  # unsigned checkpoint: {tree_size, head_sha, timestamp}
   CT_AUDIT_ROLES = ("monitor", "auditor")  # §8.2 vs §8.1/§8.3 split; gossip outside RFC scope
   ```

   Keep the Altered Histories `ALTERATION_CATEGORIES` / `DETECTION_VALIDITY_LIMITS` / `alteration_category_hint` stub UNACTIVATED — this vocabulary is a companion, not a replacement.

3. **Add a pure, UNACTIVATED checkpoint helper — no scan, state, or verdict wiring.** Beside `_head_rewinds_reading`, add:

   ```python
   def head_consistency_hint(prev_head: str, curr_head: str, *, run) -> str:
       """CT consistency analogue: is curr a superset of prev? No I/O beyond git.
       Returns 'consistent' if merge-base --is-ancestor prev curr, 'inconsistent' if not,
       'unknown' on unreadable git output. No verdict, no state. Pure helper.
       """
   ```

   Implemented as `run("merge-base", "--is-ancestor", prev_head, curr_head)` with `unknown` on None/non-zero-undecidable (exactly as `_head_rewinds_reading` returns `unknown` on unreadable ancestry). No invocation added to `sample()` or `_head_rewinds_reading`; the helper is exercised only by unit tests with fixture SHAs. A follow-up measurement can thread it through `discovery/latest.json` as `prev_head` → `head_sha` consistency (`get-sth` / `get-consistency` analogue §5.2–5.3) without changing `sense.repo.head-rewinds` state.

**Acceptance.** `python -m pytest tests/test_discovery.py` (or current discovery test module) passes unchanged — warmup/unknown, reflog-unreachable, per-path apply, `dropped_ancestry` paths unchanged. New unit tests: `prev is_ancestor curr` → `consistent`; non-ancestor → `inconsistent`; git error → `unknown`; `tree_size` absent stays absent (no STH emitted). Sample line unchanged (`last-200-reflog-entries`); follow-up can emit `sth-analogue head=<sha> tree_size=<count>` as additive sample suffix only.

**Prediction.** Over 30 days on this `main`-only checkout, `head_consistency_hint` between successive `latest.json` heads is `consistent` at every scan; an out-of-window rewind that reflog no longer shows but that is provably inconsistent with a stored STH (e.g., forced `main` rewrite mirrored on origin but expired locally) is the case this helper would catch and the reflog window would miss — confirming the critiques' "window-invisible" as a detectable inconsistency rather than an unobservable. If `tree_size` via `rev-list --count` diverges from linear entry expectation under merges, the definition is revised to count first-parent history only, matching CT's linear entry sequence.

**Falsifier.** If repeated `head_consistency_hint` on this checkout with stored STHs never yields `inconsistent` even after injected `git reset --hard` to an ancestor and a new commit (simulated rewrites), the `merge-base --is-ancestor` analogue does not model Git's consistency as claimed and should be revised to a `rev-list prev..curr` diff shape. If Git's commit SHA covering ancestry proves insufficient to detect a data-only re-commit (same SHA collision / same-tree different-commit), the "root covers history" analogy is narrowed to commit identity, not content equality — still the RFC's `MTH` vs leaf distinction.

**Limits of this transfer.** SWH/Altered Histories S1-vs-S2 is snapshot comparison; CT is Merkle proof over signed heads — this maps the proof idea onto `is-ancestor`, not the hash construction. Git history on `main` is linear enough for `tree_size` but DAG merges break strict linear `tree_size` ↔ `MTH` correspondence. No gossip/ external-signed-STH implemented here; without it a misbehaving origin can still show split views per RFC §1. Pure helper is advisory; it does not change drift/verified.

## Disposition

Route to discover with this review and its commit as evidence. Disposition trigger is discover's reply, or the next commit touching `_head_rewinds_reading` in `discovery.py`. Complements 2026-11-01 transfer (PSI `some` stall share vs throttle count, host_containment) — same field, opposite half (CPU throttle vs history); complements 2026-11-03 critique/recent (Altered Histories §VII window/valency) — same history half, distinct source (§VII threats vs RFC 9162 append-only audit) and target vocabulary (STH/consistency vs detection limits).

