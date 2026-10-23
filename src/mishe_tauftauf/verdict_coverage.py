"""The coverage of a published verdict, and the bound its rows support.

A verdict that speaks for a subset of its population is only readable as a
pair: the fraction of the population it covers, and the risk among the rows
it covers. The rule that picks the covered rows is part of that claim, so it
is published as a name and a definition rather than left in prose: a fraction
whose selection rule is unstated cannot be read at all.

This module owns the vocabulary and the arithmetic. It depends on nothing in
the plant: it takes counts and rows and returns figures, so a reader can
re-derive the published numbers from the published counts without importing
the producer, and the two sides cannot drift apart by computing different
answers.
"""

from __future__ import annotations

import math

#: The rule that picks the rows the live window speaks for.
EVIDENCE_SELECTION_RULE = "latest-outcome-row-per-role"
EVIDENCE_SELECTION_RULE_DEFINITION = (
    "the live window is the most recent post-clause outcome row of each role, "
    "so it speaks for one row per role and for no other row")

#: The name of the bound the reading publishes.
EVIDENCE_BOUND_METHOD = "clopper-pearson-upper-tail"
EVIDENCE_BOUND_METHOD_DEFINITION = (
    "the distribution-free upper confidence limit for a rate: the largest b "
    "with P(X <= k | rows, b) = delta, for X binomial over the rows sampled "
    "and k of them violating; it bounds the rate of a fresh row drawn at "
    "random from the same population, given the sampled rows and their "
    "violations")

#: The assumption a bound rests on, published beside it.
EVIDENCE_BOUND_ASSUMPTION = (
    "the limit holds only if the stratum's audited rows are exchangeable with "
    "the fresh rows it bounds. The audited population is the post-clause "
    "outcome rows the current tape names, so the store records no tape entry "
    "names sit outside every quoted figure, and the coverage pair published "
    "beside the bound says how large that audited population is")

#: The confidence the published bound is quoted at.
EVIDENCE_BOUND_DELTA = 0.05

#: The tighter confidence the bound is quoted at beside it.
EVIDENCE_BOUND_DELTA_TIGHT = 0.001

#: What the published coverage field is about.
EVIDENCE_VERDICT_COVERAGE_DEFINITION = (
    "the fraction of the checked population the published live window speaks "
    "for, and the risk among the rows it covers: the denominator and the "
    "numerator of the same claim")

#: What the published coverage field is not about.
EVIDENCE_COVERAGE_ROUTED_NOTE = (
    "the plant's coverage-gap and coverage-window checks measure a different "
    "thing, whether a broken row is still visible somewhere; this field is "
    "population coverage, not routed visibility")


#: Why a stratum the live window does not reach has no bound.
EVIDENCE_STRATUM_NO_SAMPLE_REASON = (
    "the live window holds no row of this stratum, so no sample of it exists "
    "and no bound is quoted for it")

#: What each policy-version stratum is.
EVIDENCE_STRATUM_DEFINITIONS = {
    "pre_amendment": (
        "the rows published before the allowlist amendment instant, under the "
        "policy in force then"),
    "post_amendment": (
        "the rows published on or after the allowlist amendment instant, under "
        "the policy in force now"),
}

#: Why the population the reading cannot place in a stratum has no bound.
EVIDENCE_UNNAMEABLE_REASON = (
    "the store records the tape does not name carry no publication instant, so "
    "their policy version cannot be named and neither a rate nor a bound is "
    "quoted for them")

#: The state a stratum's bound carries when it is quoted.
EVIDENCE_BOUND_STATE_VERIFIED = "verified"

#: The state a stratum's bound carries when it cannot be quoted.
EVIDENCE_BOUND_STATE_UNKNOWN = "unknown"

#: The confidences every published bound is quoted at.
EVIDENCE_BOUND_DELTAS = (EVIDENCE_BOUND_DELTA, EVIDENCE_BOUND_DELTA_TIGHT)


def clopper_pearson_upper(errors: int, rows: int, delta: float) -> float:
    """The distribution-free upper confidence limit for a rate.

    ``errors`` violations among ``rows`` sampled rows bound the rate of a
    fresh row from the same population by the largest ``b`` for which the
    binomial tail ``P(X <= errors)`` still equals ``delta``. The limit is
    computed in log space, so a population of thousands of rows does not
    overflow the sum.

    The limit is a statement about a rate measured on a sample; it says
    nothing about which rows the sample drew, so it holds only under
    exchangeability between the sampled rows and the row it bounds.
    """
    if rows < 1:
        raise ValueError("a bound needs at least one sampled row")
    if not 0 <= errors <= rows:
        raise ValueError("the violation count must lie within the row count")
    if not 0.0 < delta < 1.0:
        raise ValueError("the confidence level must lie strictly between 0 and 1")
    if errors == rows:
        return 1.0

    def tail(probability: float) -> float:
        if probability <= 0.0:
            return 1.0
        if probability >= 1.0:
            return 0.0
        terms = []
        for j in range(errors + 1):
            log_term = (math.lgamma(rows + 1) - math.lgamma(j + 1)
                        - math.lgamma(rows - j + 1)
                        + j * math.log(probability)
                        + (rows - j) * math.log1p(-probability))
            terms.append(math.exp(log_term))
        return math.fsum(terms)

    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if tail(middle) > delta:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def coverage_pair(*, population: int, judged: int, judged_violating: int,
                  population_violating: int) -> dict:
    """The coverage pair of a published figure over its population.

    ``population`` is every row the reading checked, ``judged`` the rows the
    published figure speaks for, and the two violation counts the risks are
    measured over. The pair is what makes the figure readable: how much of
    the population it covers, and what fraction of the covered rows violate.
    """
    return {
        "population": population,
        "population_source": "the post-clause outcome rows the reading checked",
        "judged": judged,
        "judged_source": ("the live window: the most recent post-clause outcome "
                           "row of each role"),
        "judged_violating": judged_violating,
        "population_violating": population_violating,
        "abstained": population - judged,
        "coverage": (judged / population) if population else None,
        "selection_rule": EVIDENCE_SELECTION_RULE,
        "selection_rule_definition": EVIDENCE_SELECTION_RULE_DEFINITION,
        "definition": EVIDENCE_VERDICT_COVERAGE_DEFINITION,
        "scope_note": EVIDENCE_COVERAGE_ROUTED_NOTE,
        "risk_among_judged": (judged_violating / judged) if judged else None,
        "risk_over_population": ((population_violating / population)
                                  if population else None),
    }
