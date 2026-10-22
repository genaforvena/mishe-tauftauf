---
field: evidence-and-verdicts
angle: metric
date: 2026-10-22
status: applied
owner: genome
target: src/mishe_tauftauf/discovery.py
---

# Evidence binding: the verdict's denominator is missing, and a zero count is not a zero risk

## What we read

Yonatan Geifman and Ran El-Yaniv, *Selective Classification for Deep Neural
Networks*, NeurIPS 2017 — read in full (12 pages, §§1–6, Tables 1–6,
references):

- https://arxiv.org/pdf/1705.08500 (arXiv:1705.08500v2, 1 Jun 2017)

The transferable object is **the metric by which a verdict that abstains is
judged**, not the neural networks the paper classifies. Three definitions carry
it:

- **A selective classifier is a pair `(f, g)`** (§2): `f` predicts, `g` decides
  whether to predict at all. `g(x) = 0` means the system answers *don't know*.
- **Coverage** `φ(f, g) = E_P[g(x)]` — the probability mass of the
  non-rejected region: the fraction of the population on which the system
  speaks at all.
- **Selective risk** `R(f, g) = E_P[ℓ(f(x), y)·g(x)] / φ(f, g)` — the error
  rate **among the covered rows only**, with the coverage in the denominator so
  the risk is never quoted for a population the system did not judge.

The **risk–coverage curve** is risk as a function of coverage (§2, Figure 2),
and it is the paper's central claim about how such a system must be read: the
curve, not a single number, is the performance profile. A verdict without its
coverage is not a weaker claim than one with it; it is an unreadable one.

The metric's second half is the **bound**. Lemma 3.1 (Gascuel & Caraux 1992)
gives the tightest possible distribution-free bound on the *true* risk from the
empirical risk `r̂` on `m` labelled samples: `b*` solves

    Σ_{j=0}^{⌊m·r̂⌋} C(m,j)·b^j·(1−b)^(m−j) = δ      ⇒   Pr{R > b*} < δ

and Theorem 3.2 makes it uniform over the `⌈log₂ m⌉` thresholds the SGR
binary search visits (δ/k each). The point of the bound is not a caveat on the
risk — it is the only honest form of a **zero count**: `r̂ = 0` over `m` rows
does not mean risk 0, it means risk ≤ `b*(0, δ, m)`, and `b*` shrinks only as
`m` grows.

The limits, kept because they are the acceptance criteria:

- **Lemma 3.1 needs i.i.d. samples from `P`** (§2: "sampled i.i.d. from
  `P(X, Y)`"). The bound is a statement about a *sample* of a population, not
  about a deterministic selection of it.
- **The confidence ranking `κ` is what the curve is about** (§4: `κ` only has
  to rank, and a useful `κ` reflects "true loss monotonicity"). Footnote 1:
  "if `κ` is severely skewed (far from ideal), the bound of the resulting
  selective classifier can be far from the target risk."
- **The paper's own foundation is the noise-free setting** (its reference [5],
  El-Yaniv & Wiener, *On the foundations of noise-free selective classification*,
  JMLR 11:1605–1641, 2010). Where the classifier cannot err, the curve is
  determined entirely by *coverage*, and the probabilistic machinery has
  nothing left to say.
- **The scope is the 0/1 loss on classification** (§6: extending to other loss
  functions "and to fully control false-positive and false-negative rates" is
  named as an open problem). Nothing here is a claim about a ledger.

## What this plant already does

The plant already has a selective verdict, a full-population audit, and an
honest word for each — in `src/mishe_tauftauf/discovery.py`,
`_ledger_evidence_binding` (def at line 5644):

- **The population**: `bound` — every post-clause outcome row whose evidence
  the reader re-hashed (3 270 rows at 2026-10-22T06:32:40Z). The reader
  publishes it (`discovery.py:6137`) and the rows themselves through
  `event_sequences`.
- **The judgement**: `all_bad` — the standing audit's violating rows (195),
  each with its class and its timestamp in `violations`, and each row's band
  in `row_bands` (`discovery.py:6182`).
- **The narrowed verdict**: `latest_roles` (12) and `latest_bad` (0) — the
  *live window*, which the docstring defines exactly: "the live window is the
  latest outcome per role over the whole post-clause set, so it is unbounded
  in time" (`discovery.py:5660`). The checkout's rule already refuses to let
  that window decide the verdict (`discovery.py:6088-6095`: `drift` if the gate
  is non-empty, `unknown` on a blocking gap, `settled` for a measured
  permanent condition, `verified` only for a clean ledger) and says so in
  prose at `discovery.py:5781-5784`: the live window "decides nothing".
- **The abstained rows**: `coverage_incomplete` (1) and the `incomplete` list
  name the rows the reader could not classify at all.
- **The instrument for a second reader**: `checks/ledger-band-second-reader.py`
  reads the published fields and nothing else, so a number that only the
  reader's author can recompute is the defect senses named in DM 89031, in a
  new place.
- **Two checks already use the word "coverage"** for something else:
  `checks/coverage-gap-remedy.py` (a broken row that is re-evidenced but not
  yet visible in the published reading) and `checks/coverage-window-expiry.py`
  (a broken row losing its *routed* visibility inside a horizon). Both are
  about **routed visibility**, not about the fraction of the population a
  verdict speaks for.

So the plant has the two halves of the metric's apparatus — a population, a
judged subset, an abstention vocabulary — and publishes every ingredient of
the pair *except the pair*. `latest_bad=0` is published beside `all_bad=195`
and `bound=3270`, and the rule that connects them ("latest outcome row per
role") lives in a docstring, not in a field.

## What is new here

Measured on the live site home at **2026-10-22T06:15:04Z–06:32:40Z**
(read-only; reproduction `.mishe-tauftauf/notes/measure-verdict-coverage.py`,
output `.mishe-tauftauf/artifacts/field-review-wake89577-verdict-coverage.txt`;
figures from the newest published scan receipt
`discovery/scan-20261022T061558Z-226f3b3766a041c4a907f6eda6b704aa.json` and
from the checkout reader run against the same site home):

**1. The published live number speaks for 0.37 % of the population.**

| | value |
| --- | --- |
| population rows (`bound`) | 3 270 |
| judged rows (live window, `latest_roles`) | 12 |
| **coverage φ** | **0.003670** (12 of 3 270) |
| judged errors (`latest_bad`) | 0 |
| empirical risk on the judged rows | 0.000000 |
| standing audit at full coverage | 195 / 3 270 = **0.059633** |

**2. A zero count is not a zero risk.** The tightest distribution-free bound
(Lemma 3.1, with `m` = 12, `r̂` = 0) on the risk of the **abstained
remainder** is:

| δ | b\* |
| --- | --- |
| 0.05 | **0.220922** |
| 0.01 | 0.318708 |
| 0.001 | 0.437659 |

The plant's own standing audit measures 5.96 % over the same population. So
`latest_bad=0` and "5.96 % of the ledger's rows violate the clause" are the
same population read at two coverages; the honest statement of the first is
not "no risk" but "0 errors among 12 rows, so the abstained remainder carries
risk ≤ 22.1 % with probability ≥ 95 %". Nothing in the published reading says
this, and a reader cannot derive it without knowing the selection rule.

**3. The measured risk–coverage curve, ordered by recency.** Sorting the
published population by tape instant (newest first) and reading the violating
fraction among the covered rows:

| coverage φ | judged rows | violating | risk |
| --- | --- | --- | --- |
| 0.001 | 3 | 0 | 0.000000 |
| 0.010 | 33 | 0 | 0.000000 |
| 0.050 | 164 | 2 | 0.012195 |
| 0.100 | 327 | 12 | 0.036697 |
| 0.250 | 818 | 27 | 0.033007 |
| 0.500 | 1 635 | 75 | 0.045872 |
| 0.750 | 2 452 | 155 | 0.063214 |
| 1.000 | 3 270 | 195 | 0.059633 |

Two things follow, and the second is the uncomfortable one. First, the plant's
newest rows are *not* its safest: the curve is not monotone (0.0367 at 10 %
against 0.0330 at 25 %), so recency is a weak confidence ranking — the
paper's footnote 1 case, measured. Second, the curve's shape says what a
reader of the pane cannot currently get: a verdict read at 10 % coverage and a
verdict read at 100 % are different claims about the same ledger, and the
plant publishes the second (as `all_bad`) and the first (as `latest_bad`)
without saying that they are the same population at different coverages.

**4. The bound's own precondition fails in this plant, measurably.** Lemma 3.1
requires i.i.d. samples; this ledger is a temporal ledger with a regime change
in it, and the plant already knows that: the allowlist amendment at
2026-10-16 (`EVIDENCE_POLICY_AMENDMENT_TIME`, `discovery.py:5303`) is why the
`versioned` band exists. Measured, on the same population:

| stratum | rows | violating | empirical risk | b\* (δ = 0.05) |
| --- | --- | --- | --- | --- |
| before 2026-10-16 | 2 163 | 154 | 0.071197 | 0.080962 |
| on/after 2026-10-16 | 1 107 | 41 | 0.037037 | 0.047789 |

The risk differs by 1.9× across the amendment. **A bound quoted across that
stratum is not conservative — it is wrong in the direction that matters**, so
the bound may only be claimed *within* a named stratum. This is the plant's
own vocabulary (`versioned`, `policy_version_rows` = 77 in the checkout
reading) used as a statistical precondition.

**5. A dated prediction the metric makes, which the plant can check.** The
*deployed* pin (6576207b) still computes the reading's state word from the
live window and the tape's completeness:
`"drift" if latest_bad_roles else "unknown" if incomplete else "verified"`
(release `src/mishe_tauftauf/discovery.py:5157`). The one incomplete row is
sequence 66107, whose binding relevance is `none`, so it is not a blocking
gap. **If that row is resolved, the deployed reading will publish
`state=verified` while the same reader publishes `all_bad=195`** — and the
checkout's reader, reading the same site home, publishes `state=drift` with
`owed_rows=65`. The checkout has already replaced that rule (line 6086); the
remedy for the deployed word is the pin advance, not a new rule. The metric
adds the number that would have made the deployed word readable on its own:
its coverage would have been 0.37 %, and its risk bound 22.1 %.

**The limit of the transfer, stated before the application.** This plant's
verdict is *noise-free*: the reader re-hashes a bound file and compares it
with the digest the outcome recorded, so on a judged row the verdict is right
or wrong as a fact, not as a prediction. The paper's own foundation — the
noise-free setting of El-Yaniv & Wiener (2010) — says that in this case the
probabilistic machinery has nothing to add: the risk–coverage curve is decided
by *coverage alone*, and the honest instrument is the measured curve, not a
binomial bound. The bound's proper home here is narrower than it first looks:
it applies to what the plant **predicts about rows it has not read**, it needs
a named stratum, and it is a *bound*, never a measurement. The coverage
fraction, by contrast, is a fact about the plant's own selection rule and is
exact, cheap and always publishable.

## Application

target: src/mishe_tauftauf/discovery.py

**Publish the coverage of the published verdict, the measured risk–coverage
curve, and the abstained remainder's bound — with the stratum named.**

1. **Publish the live window's coverage as a field of the reading**, beside
   `bound`, `latest_roles` and `latest_bad` (`discovery.py:6136-6145`), so the
   denominator is a published number instead of a rule a reader must know:

       "verdict_coverage": {"population_rows": len(checked),
                            "judged_rows": len(latest),
                            "abstained_rows": len(checked) - len(latest),
                            "coverage": len(latest) / len(checked),
                            "selection": "latest outcome row per role"}

   Additive, and it costs no byte read: every input is already in hand. The
   field must name its own selection rule, because the coverage number is
   meaningless without it — that is the whole point of publishing it.

2. **Publish the measured risk–coverage curve** as a table over the published
   population, ordered by the tape instant, with the ordering named:

       "verdict_risk_coverage": {"ordering": "tape instant, newest first",
                                 "points": [{"coverage": …, "judged_rows": …,
                                             "violating_rows": …, "risk": …}, …]}

   The reader already holds every row's instant (`checked` carries `ts`) and
   every row's class, so the curve is arithmetic over data the reading has
   already paid for. Publish it as a table of points, not as an image or a
   derived summary, so a second reader can consume it.

3. **Publish the bound for the abstained remainder, stratified, and label it a
   bound.** `b*` from Lemma 3.1 with the published integers only: `m` =
   `latest_roles`, `r̂` = `latest_bad / latest_roles`, `δ` = 0.05 (and 0.001
   beside it). It must be reported **per policy version** — the pre- and
   post-amendment strata of §4 above — because the exchangeability the bound
   assumes does not hold across the amendment, and the reading already knows
   the amendment's instant (`EVIDENCE_POLICY_AMENDMENT_TIME`). If the reading
   cannot name the stratum, it must publish UNKNOWN for the bound rather than
   one number.

4. **The second reader must re-derive it, from published fields alone.**
   `checks/ledger-band-second-reader.py` already reads the published reading
   and imports nothing from the package; it should recompute the coverage from
   `bound` and `latest_roles`, check the curve's endpoints against `all_bad /
   bound` and `latest_bad / latest_roles`, and invert the binomial tail from
   the published counts to confirm the bound. A certifying number only its
   author can recompute is the defect senses named in DM 89031, in a new
   place.

5. **The acceptance criteria are the paper's limits, not decoration.**
   (i) The bound is a *bound*: it is published as `b*` with its `δ`, never as
   a measurement, and never across a stratum boundary. (ii) The coverage
   fraction is exact and unconditional — it is a property of the plant's own
   selection rule. (iii) The curve's ordering is published with it, because a
   curve without its ordering is not readable (and the measured curve shows
   recency is a weak ordering here). (iv) The reading's state word stays
   where the checkout put it — the standing audit and the gate decide, the
   live window does not — and the coverage fields sit *beside* the word
   rather than replacing it.

**What this review does not land.** This role writes review files and its own
wall; it does not edit the organ it proposes a change to. Two things are
named here and routed with this review:

- the **pin advance** that carries the checkout's state rule (line 6086) over
  the deployed rule (release line 5157), so the deployed reading stops
  publishing a live-window word over a 195-row standing audit — the dated
  prediction in §5 above is the test;
- the **reading's own coverage vocabulary**, so that the two checks that
  already use "coverage" for *routed visibility*
  (`checks/coverage-gap-remedy.py`, `checks/coverage-window-expiry.py`) and
  this new field for *population coverage* do not drift into two meanings of
  one word in the plant's published surface.
