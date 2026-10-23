"""The coverage arithmetic must hold on its own terms.

The module owns the vocabulary and the arithmetic of a published verdict's
coverage: the fraction of the population the live window speaks for, the
risk among the rows it covers, and the distribution-free upper bound each
policy-version stratum supports. The producer imports these answers, so the
tests check the properties a reader relies on rather than re-deriving the
formulas.
"""
from __future__ import annotations

import math

from mishe_tauftauf.verdict_coverage import (
    EVIDENCE_BOUND_DELTA, EVIDENCE_BOUND_DELTA_TIGHT, EVIDENCE_BOUND_DELTAS,
    EVIDENCE_STRATUM_DEFINITIONS, clopper_pearson_upper, coverage_pair)


def test_a_bound_dominates_the_observed_rate():
    rate = 3 / 10
    for delta in EVIDENCE_BOUND_DELTAS:
        assert clopper_pearson_upper(3, 10, delta) >= rate


def test_a_tighter_confidence_quotes_a_larger_bound():
    loose = clopper_pearson_upper(3, 10, EVIDENCE_BOUND_DELTA)
    tight = clopper_pearson_upper(3, 10, EVIDENCE_BOUND_DELTA_TIGHT)
    assert tight >= loose


def test_all_violating_rows_quote_the_degenerate_bound():
    assert clopper_pearson_upper(10, 10, EVIDENCE_BOUND_DELTA) == 1.0


def test_no_violating_rows_quote_a_nonzero_bound():
    """Zero observed violations still bound a nonzero rate above zero."""
    bound = clopper_pearson_upper(0, 100, EVIDENCE_BOUND_DELTA)
    assert bound > 0.0
    # The closed form for the zero-error limit: 1 - delta^(1/rows), since
    # the binomial tail with no violation is (1 - b)^rows.
    assert math.isclose(bound, 1 - EVIDENCE_BOUND_DELTA ** (1 / 100),
                        rel_tol=1e-9)


def test_the_bound_shrinks_as_the_sample_grows_at_a_fixed_rate():
    small = clopper_pearson_upper(3, 30, EVIDENCE_BOUND_DELTA)
    large = clopper_pearson_upper(30, 300, EVIDENCE_BOUND_DELTA)
    assert large < small


def test_impossible_counts_are_refused():
    for errors, rows, delta in ((0, 0, 0.05), (11, 10, 0.05), (3, 10, 0.0),
                                (3, 10, 1.0)):
        try:
            clopper_pearson_upper(errors, rows, delta)
        except ValueError:
            continue
        raise AssertionError("accepted errors=%d rows=%d delta=%s"
                             % (errors, rows, delta))


def test_the_coverage_pair_publishes_its_own_counts():
    pair = coverage_pair(population=200, judged=10, judged_violating=2,
                        population_violating=30)
    assert pair["population"] == 200
    assert pair["judged"] == 10
    assert pair["judged_violating"] == 2
    assert pair["population_violating"] == 30
    assert pair["abstained"] == 190
    assert math.isclose(pair["coverage"], 10 / 200)
    assert math.isclose(pair["risk_among_judged"], 2 / 10)
    assert math.isclose(pair["risk_over_population"], 30 / 200)
    # The selection rule is named beside the fraction, so the fraction
    # cannot be read as a claim about the whole population.
    assert pair["selection_rule"]
    assert pair["selection_rule_definition"]
    assert pair["definition"]
    assert pair["scope_note"]


def test_the_pair_names_its_nulls_rather_than_dividing_by_zero():
    pair = coverage_pair(population=0, judged=0, judged_violating=0,
                        population_violating=0)
    assert pair["coverage"] is None
    assert pair["risk_among_judged"] is None
    assert pair["risk_over_population"] is None


def test_the_strata_are_named_and_defined():
    assert set(EVIDENCE_STRATUM_DEFINITIONS) == {"pre_amendment",
                                                 "post_amendment"}
    for definition in EVIDENCE_STRATUM_DEFINITIONS.values():
        assert definition
