---
field: evidence-and-verdicts
angle: misread
date: 2026-10-21
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

# Evidence binding: the instances must be the whole population, not the index's projection

## What we read

Francis Bacon, *Novum Organum* (1620), the founding text of the table of
instances — read here in the 1859 Spedding/Ellis translation (Project
Gutenberg ebook 45988):

- https://www.gutenberg.org/cache/epub/45988/pg45988.txt

Four passages carry the reading.

1. **The foundation of an induction is a complete instance history**
   (Book II, Aphorism X): "For we must first prepare as a foundation for the
   whole, a **complete and accurate** natural and experimental history. We
   must not imagine or invent, but discover the acts and properties of nature.
   But natural and experimental history is so varied and diffuse, that it
   confounds and distracts the understanding unless it be **fixed and
   exhibited in due order**. We must, therefore, form tables and
   co-ordinations of instances, upon such a plan, and in such order that the
   understanding may be enabled to act upon them."
2. **The table must carry all the known instances, not a selection**
   (Book II, Aphorism XI): "we must first present to the understanding **all
   the known instances** which agree in the same nature, although the subject
   matter be considerably diversified. And this collection must be made as a
   mere history, and without any premature reflection".
3. **Enumeration without completeness is exposed by one contrary instance**
   (Book I, Aphorism CV): "The induction which proceeds by simple enumeration
   is puerile, leads to uncertain conclusions, and is **exposed to danger from
   one contradictory instance**, deciding generally from too small a number of
   facts, and those only the most obvious."
4. **The axiom derived from the instances must be tested against the new
   particulars** (Book I, Aphorism CVI): "we must examine and try whether the
   axiom we derive be only fitted and calculated for the particular instances
   from which it is deduced, or whether it be more extensive and general. If
   it be the latter, we must observe, whether it confirm its own extent and
   generality by giving surety, as it were, in **pointing out new
   particulars**".

Bacon's negative instances (Book II, Aphorism XII — "instances which do not
admit of the given nature") are part of the same table: a table of instances
that holds only conformations does not decide the question.

## What this plant already does

- **The rule is written down, and it is a rule about a population.**
  `docs/wall-coordination.md:155-161` requires the evidence file to be "a
  single immutable file under the owned site, written once, retained, and
  never edited"; `:181-184` names the four independent requirements (eligible
  path A, unedited B, present C, cited-once D); `:223-227` says the
  "unchanged-bytes, existence and citation-once requirements are measured
  after the fact by `sense.ledger.evidence-binding`".
- **The observer names its own population, and that population is the tape's
  projection.** `src/mishe_tauftauf/discovery.py:5439-5440`: "The sense
  re-hashes every **tape-referenced** post-clause outcome's evidence and the
  whole post-clause set (standing audit…)". The population is produced by
  `src/mishe_tauftauf/outcome_events.py:69-148` (`classify_outcomes`), which
  iterates the tape's entries (`parse_feed`) and validates each candidate
  against the record store (`records.envelope(entry)`).
- **The verdict states its own claim, and the claim is universal.**
  `src/mishe_tauftauf/discovery.py:5490-5492`: "`verified` — no violating row
  and no binding-relevant gap. **Every post-clause outcome binds an eligible,
  unedited, retained, singly-cited evidence file.**" `docs/operating.md:227`
  repeats it for the operator.
- **The store is the plant's own immutable publication record.**
  `src/mishe_tauftauf/records.py:62` writes every record `0400`, named by the
  sha256 of its own bytes (`records/<digest>.json`), content-addressed and
  written once. `src/mishe_tauftauf/feed.py:709-718` (`append_record`) is
  explicit about the order: "Persist structured evidence **before** publishing
  its readable reference" — `records.prepare()` writes the record, then
  `Feed.append` puts the readable body and `[record]` reference on the tape.
- **Nothing in the plant enumerates the store.** `outcome_events.py:96` only
  checks that `records/` exists; `discovery.py:4762` parses the tape's
  `[record] records/<digest>.json` references. There is no code path that
  lists the store's records and compares them with what the tape carries.
- **The plant's own boundary doctrine already says which of the two is the
  record.** `docs/protocol-boundaries.md:5-8`: "Text is an external
  representation. The machinery should act on decoded values, without
  reconstructing control from sentences it or another component rendered."
  The store holds the decoded payload; the tape is the rendering.
- **The plant already knows the difference between an empty window and a
  sound ledger.** `checks/evidence-binding-verdict-consistency.py:17-23`
  refuses a published verdict that certifies a damaged ledger, and its
  docstring names the plant's own rule: a verdict must be consistent with its
  own standing audit.
- **Measured on the live site home** (2026-10-21, the plant's own modules;
  script and raw output in `.mishe-tauftauf/experiments/`):

  | population | count |
  | --- | --- |
  | published wall-outcome records in the store (`records/*.json`, kind `wall-outcome`, with an `evidence` payload) | 7599 |
  | of those, published after the clause time (2026-10-06T16:22:11Z, by the record's own mtime — records are written once, `0400`) | 5164 |
  | rows the evidence-binding gate enumerates (`classify_outcomes`, post-clause) | 3168 |
  | published post-clause records the gate's population does **not** reach | 1996 |
  | of those, written *after* the current tape's first entry (2026-10-10T15:51:56Z) | 13 |
  | of those 13, whose bound evidence file no longer matches the attested digest (a class-B row: bound bytes lost) | 4 |

  The current tape's numbering restarts at sequence 1 at 2026-10-10T15:51:56Z
  while the site's own records begin 2026-10-01, so the bulk of the difference
  (1983 rows) is history the current tape no longer carries. The 13 are not
  that: they were written while the current tape existed, and no tape entry
  carries their digest (a raw search of `chat.log` for those digests returns
  no hit). Four of them — `discover-wake9004-episode4-census-1h2m-open.md`,
  `genome-wake12454-window-recovery-followup.md` and two more — have evidence
  files whose bytes no longer match the digest their record attested. They
  are, in the plant's own vocabulary, permanent-loss rows that the gate
  cannot see: not counted in `permanent_loss`, not banded, not owed.

## What is new here

- **Bacon's foundation for an induction is the completeness of the instance
  table, and this plant's instance table is an index's projection.** Bacon
  requires "a complete and accurate" history, "fixed and exhibited in due
  order", carrying "all the known instances" — a *collected* table, not an
  incidental one. The plant's table is the set of publications the tape
  happens to carry. The plant's own store, which is immutable,
  content-addressed and written once, holds 1996 published post-clause
  outcomes the tape never carried, and the plant never reconciles the two.
  The table is *fixed* in Bacon's sense (the tape is append-only) but it is
  not *complete*, and nothing in the plant says which population the verdict
  was computed over.
- **The axiom is more general than the instances it was derived from, and
  Bacon's remedy is to point out the new particulars.** The `verified` verdict
  asserts a universal ("Every post-clause outcome binds an eligible,
  unedited, retained, singly-cited evidence file") while its instances are
  the tape's. The new particulars exist — they are in the store — and four of
  them contradict the axiom exactly in the way the clause forbids: the bound
  bytes are gone.
- **The failure mode is the one Bacon names precisely: "exposed to danger
  from one contradictory instance".** This plant's contrary instances are not
  hidden by bias or by a permissive rule; they are invisible because the
  enumeration and the record disagree, and the plant has no instrument that
  compares them. A negative instance the table cannot see is worse than a
  negative instance the table contains, because it cannot even be banded.
- **The plant's own writer makes the class possible.** `append_record`
  persists the record first and publishes the readable reference second
  ("Persist structured evidence before publishing its readable reference").
  A failure between the two leaves an immutable record that no reader of the
  tape can see — which is what the 13 rows look like. That mechanism is
  *consistent* with the measurement; the specific cause of those 13 is
  **UNKNOWN** from the evidence I have (there is no entry for them in
  `logs/outcome-refusal-audit.jsonl`, and the roles published no complaint),
  so this review names the mechanism as a hypothesis and the reconciliation
  as the instrument that would make the next occurrence attributable.
- **What the transfer is, and what it is not.** Bacon's inductions concern
  natural history and axioms; this plant's ledger is a record and its
  verdicts are state words. The transfer is structural — an induction needs a
  collected, complete instance table, and a universal claim needs its
  population checked — and it is offered as that, not as a claim that Bacon
  discussed ledgers. Bacon's objection to enumeration is about *incompleteness
  and bias*; the plant's case is the first of the two and not the second:
  the plant's table is not biased towards confirmations, it is *truncated*.
  That makes the defect a strict instance of his, not a variant of it.
- **The plant is not otherwise naive here, and the review does not pretend it
  is.** The verdict vocabulary states each word's claim, the bands partition
  the standing audit, the detector refuses a verdict that certifies a damaged
  ledger, and the doctrine says a missing or stale reading is UNKNOWN. What
  is missing is the one fact Bacon says an induction cannot do without: the
  population.

## Application

target: src/mishe_tauftauf/discovery.py

**Change 1 — the reading publishes the population it was computed over, and
its reconciliation against the store.**

In `_ledger_evidence_binding`, publish beside the verdict:

- `population_source` — `tape` — and `population_from` — the tape's first
  entry timestamp, so a reader can see the boundary of the enumeration;
- `population_size` — the rows the gate enumerated;
- `store_outcomes` — the published wall-outcome records the store holds;
- `unreconciled` — the store's records the enumeration did not reach;
- `unreconciled_amended` — of those, the ones whose bound evidence no longer
  matches the attested digest (the contrary instances, counted so the loss
  is attributed rather than only counted);
- `reconciled_at` — when the reconciliation was measured.

The reconciliation must be *carried* from a periodic check, not recomputed by
the producer: enumerating the store costs a directory scan of 61 699 files
and 7 599 record reads (measured 8–15 s on this home with a warm cache),
which does not belong in a 60 s producer cycle. The plant's `checks/` caches
already exist for exactly this shape of cost.

**Change 2 — the verdict's positive claims require a whole population.**

`verified` (`:5490`) and `settled` (`:5498`) are universal claims about the
post-clause population; `drift` is a lower-bound claim ("at least one row is
owed") and survives a truncated population. So while `unreconciled > 0`,
`verified` and `settled` must not be published: the verdict reads `unknown`
with the reason named — the plant's existing word for "could not be
determined", not a new word — and the reason states the unreconciled count
and the age of the reconciliation. A missing or stale reconciliation also
reads `unknown`, never `verified`: that is the plant's existing rule for a
reading it cannot trust, and it applies here unchanged.

**Change 3 — a standing detector for the class.**

`checks/outcome-population-reconciliation.py`: enumerate the store's
wall-outcome inventory, enumerate the digests the tape references, print the
difference (and, for each row in it, whether the bound evidence still
matches), exit 2 when the difference is non-empty and exit 3 when the store
or the tape is unreadable — the convention
`checks/evidence-binding-verdict-consistency.py` already uses. The check
writes its result where the sense can carry it, so the producer cycle does
not pay for it. This is the plant's own countermeasure for its retired lane's
failure: the class stays observable after the turn that found it.

**Change 4 — the writer names the orphan (owner: genome).**

`src/mishe_tauftauf/feed.py:709-718`: when the record has been persisted and
the readable reference has not been published, the writer should record the
persisted-but-unpublished record's digest in an audit line under `logs/`
(the plant already has `logs/outcome-refusal-audit.jsonl` and
`logs/binding-refusal-audit.jsonl`), so the class is attributable and a
retry can publish the reference. The store is append-only and
content-addressed, so the fix is to *name* the orphan, never to delete or
rewrite the record.

**Acceptance, all decidable by the plant's own tools.**

- `python3 checks/outcome-population-reconciliation.py --home SITE` exits 2 on
  today's live site and prints the 13 rows and the 4 whose evidence no longer
  matches — a controlled RED that names the class rather than a count;
- with the difference non-empty, the sense's published reading cannot carry
  `state: verified` or `state: settled`; with the difference empty (a fixture
  home), both are reachable again;
- the reading carries `population_size`, `store_outcomes`, `unreconciled`,
  `unreconciled_amended` and `reconciled_at`; a missing or stale
  reconciliation reads `unknown`;
- the four amended rows are attributed: they appear in the reconciliation's
  output with their roles and evidence paths, so a resolver can be addressed.

**What this proposal does not do.** It does not change the class taxonomy
(A/B/C/D), the bands, the remedy vocabulary or the four requirements: those
belong to the sense's owner and the operator's clause, and the reading
proposes a published fact rather than a new vocabulary. It does not recover
the 1983 rows whose history the current tape no longer carries — no
instrument can — it makes that boundary explicit so the verdict's claim is
stated over a population the plant can name. It does not touch the record
store's contents, the writer's binding rules, or the detector that already
refuses a verdict certifying a damaged ledger.

**Owner:** senses (the reading and the detector). Writer-side counterpart:
genome (the orphan record).
