---
field: repo-history-and-host-throttle
angle: recent
date: 2026-11-03
status: applied
owner: discover
target: src/mishe_tauftauf/discovery.py
---

# Altered histories at scale: local reflog window vs archival detection

## What we read

- Primary text: Rapaport, Pautet, Tardieu, Zacchiroli, **"Altered Histories in Version Control System Repositories: Evidence from the Trenches"**, arXiv:2509.09294v1 [cs.SE] 11 Sep 2025 (ASE 2025, Seoul, Nov 2025). Abstract page <https://arxiv.org/abs/2509.09294> PDF <https://arxiv.org/pdf/2509.09294> retrieved 2026-11-03 as /tmp/altered-histories-2509.09294.pdf 566775 B sha256 55c2c3702691da786b7b2dfc73477fe189e31bf6b56223e5542543ec9894dd19 (923 lines via pdftotext). Read: Abstract, §I Introduction (§I-A contributions, RQs 1–5), §II Background (Git Merkle DAG, amend §II-b Fig.1 snowball effect, terminology snapshot S1/S2 altered history, altered/root-cause commit definitions), §II-d Software Heritage, §III Methodology (SWH graph dataset 2024-08-23, 310 M origins → 111 M Git origins with ≥2 visits, detection via successive snapshots, Table I branch unification, Fig.3 framework), §IV-A RQ1 prevalence (1.22 M repos, 8.7 M rewritten histories from 12.5 B altered commits), §IV-B RQ2 branch/popularity (Fig.4–5: pull-request 37.57%, main 11.39%, development 0.88%; popularity table 0–1 star 0.3% → 100+ stars ≈3.4%), RQ3 taxonomy (Table II META 13.3% 1 160 725 vs DIR 6 693 247 File Modified/Removed/Split, Different-Branch 866 113; metadata co-occurrence committer-date most frequent), Table III top-20 altered filenames (default.nix 13.4 M, package.json, Makefile, README.md, LICENSE #20), §IV case studies (secrets: 13 M File Removed, 75 k repos, 108 RSA keys; licenses: 796 972 files across 32 169 repos, 79% minor version, 14% full change), §V GIT HISTORIAN design (PostgreSQL + CLI + cache + hooks). Skimmed: §III-C taxonomy depth-10 descendant search detail, Fig.2 Software Heritage DAG preservation diagram, hooks §V-B.

- Core claims, quoted as the paper states them:
  - "We say that a repository R underwent a history alteration if there exist two subsequent repository snapshots S1, S2 of R such that ∃ ci, ci ∈ S1 ∧ ci ∉ S2, i.e., at least one commit present in the first snapshot but missing from the second."
  - "An altered commit is a commit impacted by a history alteration, as detected by the fact that is missing from a subsequent repository snapshot, but present in a previous one."
  - "Due to the snowball effect, potentially many commits will be altered as a consequence of a single history alteration" when a modified commit's descendants get new Merkle identities.
  - "Root cause commits are the oldest commits, in topological order, among all altered commits … that have been directly altered: their identity changes are not a mere consequence of the modification of previous commits."
  - Scale: "We analyze 111 M public Git repositories … We identify 12.5 B altered commits … it impacts 1.22 M repositories" with "8.7 M rewritten histories."
  - Branch: "pull request branches represent the most frequent target … 37.57% … Main branches … 11.39% … ranking as the 2nd-most impacted category."
  - Taxonomy: "13.3% history alterations concern metadata-only changes … whereas 76.8% alterations include changes to files and/or directories" and "The dominance of File Modified is significant … more than 4 times higher than File Removed."
  - Recurrent secrets: "despite developers' attempts to remediate secret exposure through history alteration, such sensitive information remains recoverable through history digging in archives."
  - Licenses: "79% of alterations are relatively minor license updates … 14% represent full license changes … retroactively changing version history can constitute an attempt to make old software versions, under an old license, disappear from public circulation."

- Limit of this reading: single 10-page conference paper; SWH graph internals [5][30] and the 10-generation descendant cutoff for File Removed were read as stated, not re-derived. Detection rests on archival visits (at most two snapshots compared at a time); local reflog analysis was not the paper's method — the authors observe the *post-rewrite* graph vs the *pre-rewrite* snapshot preserved outside the repository. Popularity analysis uses GitHub stars, acknowledged as gameable. No kernel or cgroup text is cited by the paper; it is orthogonal to the earlier cpu.stat throttle readings. The 30-day `gc.reflogExpireUnreachable` horizon cited in the plant's 2026-11-01 misread is not the paper's bound — SWH's bound is crawler cadence, not local expiry.

- Local primary text: `src/mishe_tauftauf/discovery.py` `_head_rewinds_reading` (ll. 3783–4057, 275 lines) and `src/mishe_tauftauf/reflog_boundary.py` (88 lines, three decoders). Live reading uses HEAD reflog window `_HEAD_REWIND_REFLOG_LIMIT=200` + `rev-list HEAD` reachability, then per-path `git apply --check` against an extracted HEAD tree, with buckets `lossless`/`absent`/`needs-review` plus `preserved-untracked`/`detracked-local-only`/`reverted-at-head` and splits `review_basis`/`rewrite_basis`/`superseded_content`.

## What this plant already does

- Counts dropped commits as every distinct sha in the last 200 HEAD reflog entries not reachable from HEAD (general-case detector per 2026-11-01 misread revision; replaces the old "reset drops" docstring narrow reading). Labels, not loss: `_rewrite_basis` (parent-reachable vs chain-unreachable vs root), `reset_origin` (correlation-only stash labels that "never feed its state"), `_reverted_at_head` for pre-amend twins. Window named in sample (`last-200-reflog-entries`) with documented 30-day unreachable expiry caveat.
- Decides loss per path, never from the count: reverse-apply (lossless when already present), forward-apply (absent candidate), else `needs-review` with `review_basis` (`superseded`/`absent-at-head`/`unexplained`) and `superseded_content` intact/rewritten line counts. Counts, not taxonomy: no bucket named META vs DIR, no file-name recurrence table, no distinction between metadata-only rewrites and content rewrites.
- Treats the detection window as local and expiring, which is precisely the gap Software Heritage fills: the plant can only see what the retained reflog still holds; a commit dropped before the 200-entry window or pruned after 30 days leaves no trace locally, while SWH would retain the pre-rewrite snapshot.
- Prior five cells in this field (2026-11-01) covered: recent (LKML `cpu.stat.local` scope doc patch vs merged cgroup-v2.rst 6.8 docs — own vs inherited throttling, docstring fix in `slice_throttle.py`), misread (dropped = reflog fact not reset fact, branch-switch latent misread), critique (not applicable), transfer/ mechanism/ metric (time-equivalent CPUs, `cpu.stat.local` inherited counter). None cited an empirical prevalence study of history rewrites at scale nor its branch/metadata taxonomy. Predecessor recent was throttle-counter semantics; this is history-alteration prevalence and categorization.

## What is new here

1. **Scale and main-branch prevalence as a prior, not an anecdote.** 1.22 M / 111 M repos (≈1.1%) with an altered history, 11.39% of alterations on main/master (unified) despite the "main is stable" expectation, and rising with popularity to ~3.4% at 100+ stars — distinct from `pull request` 37.57% which is expected. The plant's current sample (`dropped-commits=10 of last-200`, skew amend/cherry-pick in this checkout) has no base rate to read against. The paper supplies one: a non-zero main-branch rewrite rate is normal at scale, and the monitoring question is not "was anything dropped?" but "what was dropped, and where?".

2. **A taxonomy the plant does not yet name.** Table II META (author/message/date/committer fields) vs DIR (File Modified / Removed / Content Split) plus Different-Branch. The plant's per-path buckets approximate DIR (lossless ≈ content already present, absent ≈ removed, needs-review ≈ modified/split) but collapse META-only rewrites into "0 dropped" or into DIR noise, because a metadata-only amend changes the commit id without touching trees — the `diff-tree --name-only` list can be empty, yet the sha is still dropped. The paper's 13.3% META-only frequency means a dropped sha with zero changed paths is not "nothing happened"; it is the most common metadata class (committer-date) and its most frequent co-occurrence is author-date+committer-date or message+dates.

3. **Recurrent file patterns as supply-chain signal.** Top-20 list dominated by build/config/doc files; LICENSE at #20 (2.7 M occurrences) with 79% minor version vs 14% full license swaps — retroactively disappearing old license rights; secrets (key/secret YAML, RSA keys) as 13 M File Removed across 75 k repos, with the paper's security observation that "removing it from history represents only partial remediation" because the bytes remain in archives. The plant's reading today reports `absent-paths` and `needs-review-paths` as opaque paths; it does not surface LICENSE or secret-adjacent patterns as higher-scrutiny classes.

4. **Detection method gap: local window vs external snapshot preservation.** The paper detects alterations by comparing S1 vs S2 preserved in SWH's global Merkle DAG; the plant detects them by comparing the live HEAD reflog vs live reachability inside one checkout. The latter is bounded by "last-200 entries" and ~30-day retention; the former is bounded by crawler cadence and succeeds after local expiry. GIT HISTORIAN's design (PostgreSQL of pre/post snapshots, hook integration) is the tooling analogue: the detector is outside the repository being measured.

5. **Predecessor relation (explicit).** 2026-11-01 recent answered counter scope for cpu-throttle (what `cpu.stat` vs `cpu.stat.local` measures, docstring scope note). This paper answers history-alteration prevalence and categorization (how often, where, and what kind). Orthogonal: one is kernel cgroup semantics, the other is repository-history empirical behavior. It complements the 2026-11-01 misread (dropped = reflog fact, not reset fact) by supplying the empirical distribution of that fact (which branch, which part of the commit).

## Application

target: src/mishe_tauftauf/discovery.py

Proposal for the discover owner — additive, pure documentation plus one pure helper beside the existing `_head_rewinds_reading` classification; no emitted scan or check path changes in this step.

1. **Name the prevalence and taxonomy in the `_head_rewinds_reading` docstring.** After the existing "Counting rule" paragraph, add:
   > Altered history at scale (Rapaport et al., arXiv:2509.09294 ASE 2025 §IV): ~1% of public repos show a history alteration (1.22 M / 111 M, SWH 2024-08-23; 8.7 M rewritten histories); main/master is the second most altered branch class at 11.39% behind pull-request 37.57%. Taxonomy: 13.3% of root-cause commits are metadata-only (META), 76.8% touch files/dirs (DIR: File Modified ≫ File Removed, Content Split), plus Different-Branch. A dropped sha with zero changed paths is not "no change" — it matches the META-only class (committer-date most frequent). Local detection here is reflog-window bounded (last-200 entries, gc.reflogExpireUnreachable default 30 days); Software Heritage detects the same phenomenon by comparing archived snapshots S1 vs S2 outside the repository, so a locally quiet window does not imply no alteration occurred before retention. This paragraph is descriptive, not a verdict change.
   Cite the arXiv URL and the §IV/Tables I–III figure numbers verbatim so a second reader can re-derive the taxonomy from the module alone.

2. **Publish the taxonomy vocabulary as constants and a stub, beside `_rewrite_basis` / `_detracked_disk_state`.** Add:
   ```python
   # Rapaport et al. 2025 — altered-histories taxonomy (ASE 2025, arXiv:2509.09294 §IV Table II)
   ALTERATION_CATEGORIES = ("meta", "dir-modified", "dir-removed", "dir-split", "different-branch")
   ALTERATION_TAXONOMY_DEFINITION = "META 13.3% (committer-date most frequent) vs DIR 76.8% (File Modified >4x File Removed) vs Different-Branch; main 11.39% of alterations, PR 37.57% (SWH 111 M Git origins, 2024-08-23)"
   # No behavior change: documents prevalence and categories; caller supplies pre/post file sets when available.
   ```
   and a pure helper:
   ```python
   def alteration_category_hint(changed_paths: list[str] | None, meta_only: bool = False) -> dict:
       """Map a dropped commit's observable shape to the paper's taxonomy bucket.

       Rapaport et al. Table II: META-only vs DIR (Modified/Removed/Split) vs
       Different-Branch. This stub publishes the mapping without computing
       post-rewrite file presence (which the paper does via 10-generation
       descendant search). Returns {category: str|None, meta_only_hint: bool}.

       Pure classification, no I/O. UNACTIVATED until discover wires caller-supplied
       diff-tree/path sets; NULL (None) when neither signal is present.
       """
   ```
   The stub reuses the paper's symbols (META/DIR/Split) verbatim and is intentionally UNACTIVATED — it does not read `git diff-tree` itself in this step, so no new git invocation and no verdict change.

3. **Keep the existing per-path classification contract unchanged.** `lossless`/`absent`/`needs-review` plus `preserved-untracked`/`detracked-local-only`/`reverted-at-head` and `review_basis`/`rewrite_basis` stay identical; the new helper is independent and additive. The `sample` retains `last-200-reflog-entries` as before; a follow-up measurement can route `different-branch` or LICENSE/secret-adjacent path names to higher-scrutiny display without changing `state`.

**Acceptance.** `python -m pytest tests/test_discovery.py` (or the repo's existing discovery test module) passes unchanged — warmup/unknown, reflog-unreachable, and per-path apply paths unchanged. A new unit test shows: with `meta_only=True` and `changed_paths=[]`, `alteration_category_hint` returns `meta`; with non-empty `changed_paths` it returns `dir-modified` hint; with `None` it returns `category=None`. The `sense.repo.head-rewinds` sample line still prints with `last-200-reflog-entries` and does not depend on the helper. No change to `reflog_boundary.py` or `head_rewind_dispositions.py` in this step.

**Prediction.** Over the next 30 days of scans on this checkout, `dropped-commits` rate across 200-entry windows stays in the single digits (as observed: 10/198 distinct shas), and ≥1 dropped sha with zero `diff-tree` paths appears when an amend touches only message/date — confirming the META-only class is not hypothetical. If the helper were activated, LICENSE or secret-adjacent `absent-paths` would be the highest-scrutiny subset of `needs-review`/`absent`, matching the paper's case studies.

**Falsifier.** If repeated `discovery.scan()` on a checkout with forced metadata-only amends yields zero dropped shas, or if no dropped sha ever has an empty path list, the META-only mapping would be untriggered and should be revised to a display-only note. If SWH ingestion of this plant's GitHub mirror shows a history alteration outside the local 200-entry/30-day window that the local reading reports as 0 dropped, that confirms the external-vs-local detection gap rather than refuting the taxonomy.

**Limits of this reading.** 10-page paper not re-read line-by-line beyond cited sections; SWH dedup/compression graph [5][30] and ScanCode license heuristics not re-verified. The 10-generation cutoff for File Removed and the branch unification in Table I are the authors' methodological choices reproduced here, not endorsed. Transfer is a hypothesis: the paper's branches are GitHub/GitLab public forges, not this plant's `main`-only single-checkout discipline — the branch distribution is a prior for where to look, not a claim this plant's `main` will show PR-branch patterns. The stub publishes the comparison without fitting the plant's own alteration rate; fitting it from reflog+archive diffs is the follow-up measurement the review enables. This role is read-only; the change belongs to the owner of `src/mishe_tauftauf/discovery.py` (discover).

