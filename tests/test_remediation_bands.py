"""The band vocabulary and the one band decision, tested apart from the publisher.

``mishe_tauftauf.remediation_bands`` exists so a reader that classifies
published coverage-gap rows gets the same answer the publisher wrote. These
fixtures pin that answer for each way a row can be decided, and for the byte
relation published beside a rebound row.
"""

import pytest

from mishe_tauftauf.remediation_bands import (EVIDENCE_BAND_NAMES,
                                              EVIDENCE_BAND_RELATIONS,
                                              EVIDENCE_BYTE_RELATIONS,
                                              byte_relation,
                                              classify_remediation_band)


def test_every_band_has_a_relation_and_a_name():
    """A band a reader cannot look up is a name without a claim, so every band
    the publisher counts carries its relation, and the byte vocabulary is the
    four relations a row can carry."""
    assert set(EVIDENCE_BAND_RELATIONS) == set(EVIDENCE_BAND_NAMES)
    assert all(EVIDENCE_BAND_RELATIONS[name] for name in EVIDENCE_BAND_NAMES)
    assert EVIDENCE_BYTE_RELATIONS == ("restored", "copy-of-current", "unrelated",
                                       "unknown")


@pytest.mark.parametrize("link_kind, expected", [
    ("supersedes", "disposed"),
    ("remedy-name", "rebound"),
    ("digest-cited", "cited"),
])
def test_a_lost_row_is_banded_by_the_link_that_re_evidenced_it(link_kind, expected):
    """The link kind decides the band: what re-evidenced the lost work."""
    band = classify_remediation_band(
        link_kind=link_kind, classes="B", versioned_row=False, amendment="unknown")

    assert band == expected


def test_a_lost_row_with_no_link_is_owed():
    """Nothing re-evidenced the work, so the plant still owes it."""
    band = classify_remediation_band(
        link_kind=None, classes="B", versioned_row=False, amendment="unknown")

    assert band == "owed"


def test_surviving_bytes_make_a_lost_row_recoverable():
    """The bytes survive verbatim, so only the binding is broken and nothing is
    owed -- but a deleted file never reaches this case."""
    band = classify_remediation_band(
        link_kind=None, classes="B", versioned_row=False, amendment="append")

    assert band == "recoverable"


def test_absence_outranks_a_binding_defect():
    """A deleted file is a distinct and more severe class: an ineligible or
    doubly bound path does not excuse a file that is gone."""
    band = classify_remediation_band(
        link_kind=None, classes="CAD", versioned_row=False, amendment="unknown")

    assert band == "owed"


def test_a_doubly_bound_path_is_permanent():
    """The store is append-only, so a second binding can never be undone."""
    band = classify_remediation_band(
        link_kind=None, classes="D", versioned_row=False, amendment="unknown")

    assert band == "permanent"


def test_an_ineligible_path_bound_under_the_old_rule_is_a_policy_version():
    """The row was compliant when it was bound, so no later rule repairs it."""
    band = classify_remediation_band(
        link_kind=None, classes="A", versioned_row=True, amendment="unknown")

    assert band == "versioned"


def test_an_ineligible_path_bound_after_the_rule_is_permanent():
    """No later rule can make an ineligible path eligible."""
    band = classify_remediation_band(
        link_kind=None, classes="A", versioned_row=False, amendment="unknown")

    assert band == "permanent"


def test_byte_relation_reads_the_successor_against_the_lost_row():
    """The relation is decidable from digests alone: the published bytes came
    back, or the successor holds the file as it now stands, or neither."""
    assert byte_relation("aaa", "bbb", "aaa") == "restored"
    assert byte_relation("aaa", "bbb", "bbb") == "copy-of-current"
    assert byte_relation("aaa", "bbb", "ccc") == "unrelated"


def test_byte_relation_is_unknown_without_the_digests_to_decide_it():
    """A missing digest is unknown, never a relation the row does not have."""
    assert byte_relation(None, "bbb", "bbb") == "unknown"
    assert byte_relation("aaa", "bbb", None) == "unknown"
