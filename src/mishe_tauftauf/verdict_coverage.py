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
from collections.abc import Mapping, Sequence
from typing import Final

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

#: The state a stratum's bound carries when it is quoted. "verified" here means
#: only that a bound was computed for the stratum; it does not prove the content
#: of any outcome in it is true (the ledger's clean-audit "verified" is a separate
#: word with a separate meaning).
EVIDENCE_BOUND_STATE_VERIFIED = "verified"

#: The state a stratum's bound carries when it cannot be quoted.
EVIDENCE_BOUND_STATE_UNKNOWN = "unknown"

#: The confidences every published bound is quoted at.
EVIDENCE_BOUND_DELTAS = (EVIDENCE_BOUND_DELTA, EVIDENCE_BOUND_DELTA_TIGHT)


# --- Calibration (Guo et al. ICML 2017 §2–3, ECE/MCE) ---
# Guo, Pleiss, Sun, Weinberger, On Calibration of Modern Neural Networks,
# ICML 2017, https://arxiv.org/pdf/1706.04599.pdf — eq.1 perfect calibration
# P(Ŷ=Y|P̂=p)=p, reliability Δ via acc(B_m)/conf(B_m) bins, eq.3
# ECE=Σ|B_m|/n|acc−conf|, eq.5 MCE=max|acc−conf|, reliability diagram
# diagonal falsifier (any deviation from diagonal is miscalibration).
# Mapping to verdict coverage: coverage φ (Geifman) is judged/population
# mass; ECE/MCE (Guo) is confidence-vs-accuracy deviation per bin —
# orthogonal, both publishable. acc(B_m) is empirical non-violation rate in
# bin m; conf(B_m) is mean stated confidence in that bin. At p̂≈1.0 implied
# by latest_bad=0 the standing audit acc≈0.94 (or abstained acc≈0.78 at
# bound) is the measured gap. Binning: M equally-spaced intervals
# ((m-1)/M, m/M] (paper default) or named quantile; M published beside
# ECE/MCE so variance is auditable. Limits: finite-sample estimate with
# binning variance (perfect calibration unattainable with finite n, p.2);
# i.i.d. within stratum required — across EVIDENCE_POLICY_AMENDMENT_TIME
# (2026-10-16) report unknown per stratum rule; calibration ≠ truth
# (SCITT/VC wording at EVIDENCE_BOUND_STATE_VERIFIED stays receipt-only).
EVIDENCE_CALIBRATION_METHOD: Final[str] = "ece-mce-reliability"
EVIDENCE_CALIBRATION_BINS_DEFAULT: Final[int] = 10
EVIDENCE_CALIBRATION_DEFINITION: Final[str] = (
    "ECE = Σ |B_m|/n |acc(B_m)-conf(B_m)| (eq.3), "
    "MCE = max |acc-conf| (eq.5); reliability diagram acc vs conf "
    "should be diagonal")


def _calibration_bins(
    *,
    confidences,
    correct,
    bins: int,
) -> list[dict]:
    """Bin confidences into M equally-spaced intervals ((m-1)/M, m/M]."""
    if bins < 1:
        raise ValueError("bins must be >= 1")
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must have the same length")
    n = len(confidences)
    if n == 0:
        return []
    for pp in confidences:
        if not isinstance(pp, (int, float)) or not 0.0 <= float(pp) <= 1.0:
            raise ValueError("each confidence p̂ must lie in [0, 1]")
    for c in correct:
        if c not in (0, 1, True, False):
            raise ValueError("each correct must be 0 or 1")
    bin_counts: list[int] = [0] * bins
    bin_conf_sum: list[float] = [0.0] * bins
    bin_correct_sum: list[int] = [0] * bins
    for pp, c in zip(confidences, correct):
        pf = float(pp)
        ci = int(c)
        m = __import__("math").ceil(pf * bins)
        if m <= 0:
            m = 1
        if m > bins:
            m = bins
        idx = m - 1
        bin_counts[idx] += 1
        bin_conf_sum[idx] += pf
        bin_correct_sum[idx] += ci
    out: list[dict] = []
    for idx in range(bins):
        cnt = bin_counts[idx]
        if cnt == 0:
            continue
        acc = bin_correct_sum[idx] / cnt
        conf = bin_conf_sum[idx] / cnt
        low = idx / bins
        high = (idx + 1) / bins
        out.append(
            {
                "bin": idx + 1,
                "interval": (low, high),
                "count": cnt,
                "acc": acc,
                "conf": conf,
                "gap": abs(acc - conf),
            }
        )
    return out


def expected_calibration_error(
    *,
    confidences,
    correct,
    bins: int = EVIDENCE_CALIBRATION_BINS_DEFAULT,
) -> float:
    """Expected Calibration Error \u2014 Guo et al. 2017 eq.3.

    ECE = Σ_m |B_m|/n |acc(B_m) - conf(B_m)|, the weighted mean of per-bin
    gaps between empirical accuracy acc(B_m)=mean(correct in bin m) and
    stated confidence conf(B_m)=mean(confidences in bin m). Bins are
    M=bins equally-spaced intervals ((m-1)/M, m/M] (paper default); M is
    published beside ECE so variance is auditable.
    Perfect calibration (eq.1) P(Ŷ=Y|P̂=p)=p would give ECE=0; any
    deviation from the reliability-diagram diagonal is miscalibration.
    Source: https://arxiv.org/pdf/1706.04599.pdf eq.1/eq.3 § 2–3.
    Returns 0.0 on empty input; validates 0<=p̂<=1, correct∈{0,1},
    len match, bins>=1.
    """
    if len(confidences) == 0 and len(correct) == 0:
        if bins < 1:
            raise ValueError("bins must be >= 1")
        return 0.0
    import math
    table = _calibration_bins(confidences=confidences, correct=correct, bins=bins)
    if not table:
        return 0.0
    n = len(confidences)
    return math.fsum(row["count"] / n * row["gap"] for row in table)


def maximum_calibration_error(
    *,
    confidences,
    correct,
    bins: int = EVIDENCE_CALIBRATION_BINS_DEFAULT,
) -> float:
    """Maximum Calibration Error \u2014 Guo et al. 2017 eq.5.

    MCE = max_m |acc(B_m) - conf(B_m)|, the worst per-bin gap. High-risk
    companion to ECE: ECE weights by |B_m|/n, MCE takes the largest deviation.
    Same binning/validation as expected_calibration_error.
    Source: https://arxiv.org/pdf/1706.04599.pdf eq.5 § 2–3.
    Returns 0.0 on empty input.
    """
    if len(confidences) == 0 and len(correct) == 0:
        if bins < 1:
            raise ValueError("bins must be >= 1")
        return 0.0
    table = _calibration_bins(confidences=confidences, correct=correct, bins=bins)
    if not table:
        return 0.0
    return max(row["gap"] for row in table)


def reliability_diagram(
    *,
    confidences,
    correct,
    bins: int = EVIDENCE_CALIBRATION_BINS_DEFAULT,
) -> list[dict]:
    """Per-bin reliability table \u2014 Guo et al. 2017 reliability diagrams § 2.

    Returns ordered list of {bin, interval, count, acc, conf, gap} per
    non-empty bin, where interval=(low, high) with (low,high]=((m-1)/M,m/M],
    acc=mean(correct), conf=mean(confidences), gap=|acc-conf|. Skips empty
    bins so a second reader can reproduce Fig.1 bottom without an image.
    Source: https://arxiv.org/pdf/1706.04599.pdf eq.1/eq.3/eq.5.
    """
    if len(confidences) == 0 and len(correct) == 0:
        if bins < 1:
            raise ValueError("bins must be >= 1")
        return []
    return _calibration_bins(confidences=confidences, correct=correct, bins=bins)



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


def stratum_coverage_gap(coverages_by_stratum: Mapping[str, float],
                         nominal: float) -> float | None:
    """The CovGap of a set of strata, in percentage points.

    Class-conditional coverage (Ding, Angelopoulos, Jordan, Bates, Tibshirani,
    "Class-Conditional Conformal Prediction with Many Classes", NeurIPS 2023,
    arXiv:2306.09335, section 3.3) asks each stratum to reach the nominal
    coverage, and the gap is the mean absolute deviation ``100 * mean |c - nominal|``
    over the supplied stratum fractions. A marginal figure can sit near nominal
    while strata split far above and below it; this gap reports that unevenness.

    The gap is a descriptive figure about the strata supplied. It holds only
    under exchangeability between the sampled rows and the rows it describes,
    and the reporting distinction between clustered, class-wise and standard
    strata is a presentation choice; this function does not read it.
    The source document is recorded at /tmp/ding-2023.pdf,
    sha256 f4fac9d8415bdbfeca97a76257d9fb7526c835f65699c951bf4f2e157f6c0092.

    Returns None when no stratum is supplied, so an empty reading is not
    quoted as a zero gap.
    """
    if not 0.0 < nominal < 1.0:
        raise ValueError("the nominal coverage must lie strictly between 0 and 1")
    values = list(coverages_by_stratum.values())
    if not values:
        return None
    for value in values:
        if not 0.0 <= value <= 1.0:
            raise ValueError("a stratum coverage must lie within [0, 1]")
    return 100.0 * math.fsum(abs(value - nominal) for value in values) / len(values)


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
