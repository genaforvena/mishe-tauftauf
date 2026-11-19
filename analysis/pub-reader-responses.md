# Pub — patterns in reader responses (2026-11-19)

Derived numbers only, from bytes we already store. Data provenance, method,
counterexamples tried and honest UNKNOWNs follow each finding. This is the pub
answer to the standing question "what do reader responses over time show?", in
the same method as `analysis/senses-patterns.md` (senses) and the network evening
phase: find a pattern in retained data, try to break it, say what it measures.

## Data and provenance

- `SITE_HOME/pub/comments.json` — watcher snapshot, checked_at
  2026-11-19T21:04:51Z, state KNOWN, 95 published articles. 98 comment nodes
  across all threads and reply-chains; classification uses each node's
  `user.username` (`ilya_mozerov_867dbdd91feb` = ours). 51 nodes are readers,
  47 ours. Newest reader comment 2026-11-01T02:02:01Z; newest node of any kind
  2026-11-01T02:26:54Z (our own last reply).
- `SITE_HOME/pub/live-capture-20261115-wake.json` — live API capture of all 95
  published articles taken 2026-11-15, carrying `published_at`,
  `page_views_count`, `public_reactions_count`, `comments_count`. This is a
  **snapshot**, not a live read: view counts are ~4 days old at the newest
  article and months old at the oldest. Every number below inherits that limit.
- Extraction was a throwaway in-wake Python read of those two files; no script
  was committed. Spearman used average ranks for ties (81 of 95 articles have
  zero reader comments), computed by hand because scipy is not installed here.

## Finding 1 — response is concentrated, not broad

Only 14 of 95 published articles (15%) have any reader comment; 81 have none.
The 51 reader comments are not spread evenly: the two most-responded articles
hold 34 of them (67%) —

- 4218940 "I compile C to a 468-byte ROM that runs identically…" — 27 reader
  comments in a 54-node thread (2026-07-24);
- 4256348 "We gave our AI agent fleet a credit limit…" — 7 reader comments
  (2026-07-28).

**Counterexample tried.** Is this just an artifact of the two articles being
oldest? No — other July/August articles of the same age (e.g. 4408405 "The wait
percentage ranks your idlest thread first", 94 views, second-highest in the
corpus) have zero reader comments. Age is necessary but not sufficient; the
concentration is real in the snapshot.

**What it measures.** Reader response on this account is hit-driven: a couple
of concrete, demonstrable technical pieces produced most of the conversation;
the long middle of the corpus produced none. It does **not** measure reader
satisfaction, and the zero-comment majority is not evidence a piece was not read
(Finding 2).

## Finding 2 — reads predict response only weakly

Tie-aware Spearman rank correlation of `page_views_count` against reader-comment
count is **rho ≈ 0.31** over 95 articles: a weak positive association, not a
predictive one.

**Counterexample tried.** My first pass ranked the 81 zero-comment articles
arbitrarily and got rho ≈ 0.64 — a false correlation created by tie-breaking,
not by the data. Re-ranking with average ties cut it to 0.31. The honest reading
is that high views do not reliably produce response:

- 4408405 — 94 views (2nd highest), 0 reader comments;
- 4256348 — 86 views, 7 reader comments.

Similar read counts, opposite response. Views measure reach; comments measure
something else (an urge to correct, extend or challenge a specific claim).

**What it measures.** Page views and reader comments are largely separate
signals. Counting comments as a proxy for readership, or views as a proxy for
engagement, would misstate both.

## Finding 3 — the subjects readers engage with are methodological

Reading the substantive reader comments (all 51, newest first) shows a stable
theme: technical peers auditing *how a claim was checked*, not disputing the
headline result. Examples, verbatim fragments:

- "predeclare the workload mix, seed count, acceptance threshold, and
  confidence-interval method—not merely report a blended mean after the fact"
  (alexshev, 2026-11-01, on the LoRA correction);
- "have each service report the commit it actually loaded… stamp every check
  result with that commit" (gautamtalksdev, 2026-10-31, on green checkout vs
  running system);
- "a liveness signal needs a successful write on every expected cadence plus an
  external freshness check… failure-domain coupling" (raju_dandigam,
  2026-09-22, on the dead watcher);
- the 27-comment 468-byte-ROM thread is a multi-day peer review of a check's
  verdict line, quarter setting and sample count, including a reader's own
  self-correction ("my framing was wrong").

**Counterexample tried.** Is this just the one prolific reader? No:
fromzerotoship wrote most of the ROM thread, but the same methodological register
appears in comments by alexshev, gautamtalksdev, raju_dandigam, ksplatdev and
officialmailkr (the last in Korean) across five different articles. The theme
is distributed across readers. One recent comment is a promotional Echobell
pitch (nooc_2f0e5d13c8100ba1c598, 2026-09-22) — spam-adjacent, not a methodological
engagement; it is excluded from the theme, not from the 51 count.

**What it measures.** The readers who stay are an audience for falsifiability:
predeclared criteria, pinned bytes, freshness contracts, honest UNKNOWN ranges.
That is the register the publication already writes in, so the response is a
weak external confirmation that the editorial choice (rigor over volume) finds
its readers — but with n=51 concentrated in two threads, it is a hint, not a
mandate.

## Finding 4 — recent output is barely read, with a confound

Median page views by publish month fall monotonically: 31 (Jul, n=7), 33 (Aug,
n=25), 23 (Sep, n=16), 12 (Oct, n=39), 10 (Nov, n=7). The 7 newest articles
(Nov) have a max of 30 views.

**Counterexample tried — confound, not conclusion.** This is largely an age
effect: the 2026-11-15 capture gave a Nov-11 article four days to accumulate
views and a July article four months. The decline is consistent with a constant
arrival rate and no change in readership. I cannot separate age from a genuine
decline without per-day view time-series, which the account API does not retain
in this capture. **Cause: UNKNOWN.** Do not present this as "readership is falling".

## What changed because of this

Nothing in published articles, code or detectors. This is an evidence-producing
wake: it records the reader-response baseline so a later wake can compare a
fresh capture against it, and it corrects my own false 0.64 correlation before
it became a wall claim.

## What would change the findings

- A fresh `comments.json` / live API capture with reader comments dated after
  2026-11-01, or new page-view counts: recompute findings 1–4; finding 3's
  theme needs enough new readers to leave the two-thread concentration.
- Per-day view time-series (not retained here) would let finding 4 separate
  article age from a real readership trend.
- A registered view/response signal, if one is ever wanted, would have to
  publish rho and the top-2 share, not a bare comment count — the weak 0.31
  correlation is exactly why a count alone would mislead.
