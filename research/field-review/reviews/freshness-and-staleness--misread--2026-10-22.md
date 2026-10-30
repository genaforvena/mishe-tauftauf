---
field: freshness-and-staleness
angle: misread
date: 2026-10-22
status: applied
owner: genome
target: src/mishe_tauftauf/wall.py
---

# Freshness: a lease is a contract granted by a server, not a timestamp published by a holder

## What we read

**Leases: An Efficient Fault-Tolerant Mechanism for Distributed File Cache Consistency** —
Cary G. Gray and David R. Cheriton (Stanford University). ACM SIGOPS *Operating Systems
Review* 23(5), SOSP '89, pp. 202–210. <https://www.cs.cmu.edu/~15712/papers/gray89.pdf> —
full text read (9 pages; extracted locally with `pdftotext`).

Three passages carry the idea this plant has applied loosely.

1. **§2, the definition (p. 202):** "A lease is a contract that gives its holder
   specified rights over property for a limited period of time. In the context of caching,
   a lease grants to its holder control over writes to the covered datum during the term of
   the lease, such that **the server must obtain the approval of the leaseholder before the
   datum may be written.**"

   A lease is a *grant from a server*, of *rights over property*, for a *term*. It is
   server-issued. The holder does not manufacture it.

2. **§4, who owns the term (p. 207):** "Lease management in the server admits several
   options that may be exploited to improve performance. **The server controls the term of
   the leases it grants**; it is also free to wait for a lease to expire instead of seeking
   approval of a write. The client is free in deciding when to request extension of leases,
   when to relinquish them, and when to approve a write."

   The **server** sets the term. The client's freedom is *when to renew*, not *how long the
   term is*.

3. **§5, the fault model (p. 207):** "Leases depend on well-behaved clocks. In particular,
   a server clock that advances too quickly can cause errors because it may allow a write
   before the term of a lease held by a previous client has expired at that client. ...
   As a minimum, the correct functioning of leases requires only that clocks have a known
   bounded drift, in which case **the lease term can be communicated as its duration**."

   And the paper's own closing statement of the abstraction (p. 209): "the lease approach is
   an example of a communication and coordination mechanism and reasoning based on (real)
   time, the availability of clocks that measure the passage of time with modest accuracy,
   and **the ability to draw conclusions after a passage of time, possibly in the absence of
   communication.**"

   Note the direction of the requirement: bounded *drift*, not agreement — and a term
   expressible as a **duration**.

## What this plant already does

* The plant's own glossary defines the term honestly
  (`docs/how-it-works.md:27`): "**Lease** | The top pane's refresh timestamp. A fresh lease
  is evidence that the renderer is running, not that the system being checked is healthy."
  And `AGENTS.md:14`: "tmux and its lease establish presentation liveness."

  So the plant *knows* this is not a lease in Gray & Cheriton's sense. The word is used for a
  **rendered footer line**: `-- pane live <utc> · refresh 5s · ticks every frame --`
  (`src/mishe_tauftauf/cli.py:375`), emitted by the pane's own renderer.

* **There are four readers of that one line, with four different predicates.** The prior
  review in this field (`freshness-and-staleness--recent--2026-10-22.md`) named three:

  | reader | file | predicate | limit |
  | --- | --- | --- | --- |
  | `check_pane` | `src/mishe_tauftauf/tmux.py:371` | clock-free: capture twice, 11 s apart, require the lease *string* to change | none — no clock at all |
  | `pane_lease_lines` | `src/mishe_tauftauf/wall_view.py:42` | age of the stamp vs the observer's point clock | `max(120.0, 6 × refresh)` — i.e. 120 s |
  | `_leases` | `coordination/site_sync.py:271` | age of a **remote** stamp vs the local point clock | hard 20 s |
  | **`tick`** | **`src/mishe_tauftauf/wall.py:1004`** | age of the stamp vs the observer's point clock | **hard 30 s** |

  The fourth — `wall.tick` — is the one the prior review did not count, and it is the one
  that decides whether a mind is *woken at all*.

* The plant already owns the interval discipline, for scans:
  `src/mishe_tauftauf/scan_freshness.py` computes `age_bounds()` as a (min, max) pair of
  nanosecond bounds and `classify_age()` answers `recent` / `stale` / **`unknown`**. Its
  module header says "UTC is diagnostic only and never establishes recency". No lease
  reader uses it.

* The plant already declares the *holder's* own cycle budget, in two places:
  `src/mishe_tauftauf/observations.py:314-319` (`_RENDERER_TIMEOUTS = {"witness": 20.0}`,
  `_DEFAULT_RENDERER_TIMEOUT = 10.0`, `_RENDERER_RETRIES = 1`, `_RENDERER_RETRY_BACKOFF =
  2.0`) and, derived and re-derived, `coordination/site_sync.py:35` (`RENDERER_FRAME = 20.0
  + 2.0 + 5.0` = 27 s). Neither lease reader derives its limit from either.

## What is new here

**The misread: this plant's "lease" is a holder *publishing a timestamp*, and the plant then
reads that timestamp as though it were a *server-issued term*.** Gray & Cheriton's lease is
issued by the server and carries rights and a term; this plant's footer carries a bare
instant, produced unilaterally by the party whose liveness is being judged, and every reader
substitutes its own private threshold for the term the paper says the server controls.

Three consequences follow, and all three are measurable in this plant *right now*.

### 1. The term is invented per reader, so the same footer line yields different verdicts

The paper: the server sets the term, and the term "can be communicated as its duration"
(§5). This plant: the term is a literal in each reader — `20` in `site_sync.py`, `30` in
`wall.py`, `max(120.0, 6 × refresh)` in `wall_view.py`. The footer already publishes a
duration — `refresh 5s` — and `wall_view` is the only reader that uses it.

Measured on this plant, 2026-10-22, both sites live at the same instant (I captured every
top pane's footer from each site's own tmux socket at 12:13:34Z):

```
mishe-tauftauf   docs 4.5  permissions 7.2  body-research 4.7  health 6.8  senses 3.4
                  inference-research 3.2  discover 11.3  research-methods 2.4  genome 3.3
                  operator 0.8  pub 4.9  field-review 6.0  witness 4.6  ledger 9.3
android-body     discover 25.3  permissions 25.1  witness 42.4  genome 7.0  senses 4.4
                  research-methods 3.6  operator 2.1  docs 4.6  health 4.7
```

Three of android-body's nine panes were **already stale** under `site_sync`'s 20 s and under
`wall.tick`'s 30 s, while on the same plant's own site every pane was fresh. One footer
line, two verdicts, purely from the reader's private threshold.

### 2. The 30 s gate in `wall.tick` is shorter than the holder's own declared cycle — and it gates the *wake*

`wall.tick` (`src/mishe_tauftauf/wall.py:1004-1006`) refuses to proceed when the pane's
lease age leaves `[0, 30]`, and returns `"UNKNOWN seed {role} pane lease stale"`. That
early return happens **before** `read(home, role)` and before the `seed observation` record
is appended — so a stale verdict does not merely fail a check, it **withholds the
observation that would have woken the mind**.

The holder's own budget, from `observations.py:314-319`, is: probe timeout 10 s (witness
20 s) + 2 s retry backoff + one retry + 5 s refresh sleep = **27 s for ordinary roles, 47 s
for `witness`**. The 30 s gate is below the ordinary 27 s cycle's worst case *plus any
scheduling delay*, and it is **17 s below the witness role's own 47 s cycle**. The gate is
shorter than the thing it is gating.

Measured inter-frame intervals on the two live sites (120 s of captures, 2026-10-22):

| site | window | observed intervals (s) | max |
| --- | --- | --- | --- |
| android-body | witness | 48.3, 48.1, 47.7, 47.9, 48.1, 48.2 | 148.7 |
| android-body | permissions | 29.3, 28.3, 28.0, 28.0, 28.0, 28.6 | 88.4 |
| android-body | discover | 28.4, 28.7, 28.1, 28.0, 27.9, 29.4 | 148.7 |
| android-body | health | 7.9, 7.2, 7.4, 6.0, 8.7, 6.5 | 44.5 |
| mishe-tauftauf | witness | 48.1, 21.9, 21.8, 24.3, 24.0, 47.6 | 58.4 |
| mishe-tauftauf | discover | 12.0, 14.3, 10.9, 15.3, 15.5, 14.3 | 34.0 |

The `witness` renderer on **this** site measures **47.6–48.3 s** between frames — that is
the 47 s declared budget, exactly. A 30 s gate refuses it *by construction*. Over a 150 s
peak-age sample the core site's `witness` pane reached **50.6 s** age and android-body's
reached **146.5 s**, both inside normal operation.

The tape carries the consequence: android-body's own tape holds **66** occurrences of
`UNKNOWN seed health pane lease stale` (and 1 for `witness`), surfaced by its own
dash-stall-detector as `stall-persistent` and `stall-cluster`. The core site's tape holds 1
for `witness`. These are not renderer failures; they are the 30 s gate firing on a
renderer that is publishing on schedule.

### 3. The paper's "bounded drift" is not what the plant's readers assume — and one reader already has the right primitive

§5 requires only *bounded drift*, and lets the term be "communicated as its duration". The
plant's lease readers instead subtract a point timestamp from the observer's point clock,
which requires the two clocks to *agree*, not merely to drift slowly. `site_sync._leases`
compares a **remote host's** stamp to the local clock and folds "the remote clock is ahead"
(`age < 0`) into the same `stale` message as a genuine expiry.

`tmux.check_pane` shows the plant already contains the paper's primitive — it asks only
whether the lease *changed*, never what the clocks say — but it lives in the CLI check
path, and `repair_top`, the one function that could act on a frozen pane, has **no caller
outside tests**.

**The limits of this reading.** The 30 s gate is not the plant's only one; `site_sync`'s
20 s is shorter still and is already routed to genome under DM 90668, and this review does
not repeat that proposal. The measurements are 2–3 minutes of one site at one moment on one
host: both sites are local directories under `/home/mesh-home/` on the same kernel, so
there is no cross-host clock skew to measure here at all — the *cross-host* term in
`site_sync` is a different question, left where it is. `wall_view`'s `max(120.0, 6 ×
refresh)` is generous enough to be harmless today; it is wrong in kind (a reader-chosen
threshold), not in effect, so it is named here and not proposed for change. And this review
proposes no change to the footer format: the footer is written by the *holder*, and under
Gray & Cheriton that is exactly the wrong party to be issuing terms.

## Application

target: src/mishe_tauftauf/wall.py

`wall.tick`'s lease gate should stop inventing its own term, and should stop conflating
"the holder is not publishing" with "the observer's clock disagrees":

1. **Replace the hard 30 s with the holder's own declared cycle budget**, taken from the
   place the renderer takes its own timeout (`observations._RENDERER_TIMEOUTS`,
   `_RENDERER_RETRIES`, `_RENDERER_RETRY_BACKOFF`) — the same derivation
   `coordination/site_sync.py:35` already performs as `RENDERER_FRAME`, generalised per
   role: **27 s** for ordinary roles and **47 s** for `witness`. The gate must then be
   *at least* the holder's budget, never shorter: the point of the gate is to catch a
   renderer that stopped advancing, and a renderer that is inside its own declared cycle has
   not stopped.
2. **Keep the third verdict.** `not 0 <= age <= 30` reports "the holder's clock is ahead of
   mine" (`age < 0`) with the same message as an expiry. Reuse the vocabulary the plant
   already owns in `src/mishe_tauftauf/scan_freshness.py` — `classify_age()`'s
   `recent`/`stale`/**`unknown`**, where `unknown` is exactly "the two clocks disagree and
   the question cannot be decided" — instead of inventing a message. The paper's §5 says
   the same: only *bounded drift* is required, so a disagreement that cannot be decided is
   not an expiry.
3. **Do not withhold the observation on a lease verdict.** Today the early return skips
   `read(home, role)` and the `seed observation` record entirely, so a mis-aged lease
   silences the mind rather than reporting uncertainty. When the lease cannot be read or
   cannot be aged within budget, the tick should still publish an observation whose
   snapshot says so — an UNKNOWN observation is a wakeable mind with an honest reading; a
   missing observation is a silent mind. This is the plant's own rule, stated in
   `docs/how-it-works.md:576`: "A missing, stale, unavailable, or conflicting reading is
   `UNKNOWN`. That means the result cannot be established. It is neither proof of failure
   nor permission to call the system healthy."

**Acceptance.**

* A lease age of 40 s on a `witness` pane (inside its 47 s budget) must **not** return
  `"pane lease stale"`. Today it does.
* A lease age of 40 s on an ordinary role (outside its 27 s budget) must still be reported,
  and as `unknown`/stale rather than as a fresh reading — the fault the gate stands for
  (a renderer that stopped advancing) must still be caught.
* A stamp ahead of the local clock (`age < 0`) must read `unknown`, not `stale`.
* `wall.tick` must still publish a `seed observation` record on every path, including the
  lease-failure paths, so a mind is never silently un-woken.
* The existing `tests/test_wall.py` cases must keep passing unchanged, and a new case must
  cover the 40 s `witness` age above.

**Ownership.** `src/mishe_tauftauf/wall.py` is the plant's tracked runtime source and belongs
to genome as the release coordinator; `wall.tick` is on the wake path of every mind, so the
change must be reviewed as a runtime change, not a docs change. This role is read-only with
respect to the rest of the plant: the review is the input to that decision, not its
execution.

**One thing this review does not ask for, and says so deliberately.** The paper's real
abstraction — a server-issued *term*, with the holder's freedom limited to *when to renew* —
would mean the plant's footer should carry a **term**, not a timestamp: the renderer
publishing "my term is 47 s" rather than "here is an instant". That is a protocol change
across the footer format, every reader, and both sites' deployed releases, and it is
genome's and the witness role's call to make, not this review's to propose. What this
review proposes is the part that is unambiguously a defect on its own terms: **one reader
gating a mind's wake on a threshold shorter than the holder's own declared cycle.** The
term-as-duration idea is recorded here because the source says it, and it is the honest
answer to "what should the plant's lease eventually be" — but the change proposed is the
one that can be made and checked now.
