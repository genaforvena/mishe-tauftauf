"""Provenance predicates test C2PA 2.2 status-code strings, not invented ones.

Revocation is carried by ``signingCredential.ocsp.revoked`` /
``signingCredential.ocsp.notRevoked``; timestamp binding is ``timeStamp.validated``
(an interpretive mapping, see the docstring). The earlier invented names must
no longer satisfy any predicate.
"""
from __future__ import annotations

import pytest

from mishe_tauftauf.outcome_publication import (
    provenance_revocation_status,
    provenance_timestamp_binding_holds,
    provenance_validators_agree,
)


@pytest.mark.parametrize(
    "codes, expected",
    [
        (["signingCredential.ocsp.revoked"], "revoked"),
        (["signingCredential.ocsp.notRevoked"], "not-revoked"),
        (["signingCredential.ocsp.notRevoked", "signingCredential.ocsp.revoked"], "revoked"),
        (["claimSignature.validated", "signingCredential.ocsp.notRevoked"], "not-revoked"),
        (["signingCredential.ocsp.skipped"], "unchecked"),
        ([], "unchecked"),
        (["claimSignature.validated"], "unchecked"),
    ],
)
def test_revocation_maps_spec_ocsp_codes(codes, expected):
    assert provenance_revocation_status(codes) == expected


@pytest.mark.parametrize(
    "codes",
    [
        ["signingCredential.revoked"],
        ["signingCredential.notRevoked"],
        ["signingCredential.trusted", "ocsp.good"],
    ],
)
def test_revocation_ignores_invented_codes(codes):
    assert provenance_revocation_status(codes) == "unchecked"


def test_revocation_malformed_input_is_unchecked():
    assert provenance_revocation_status(None) == "unchecked"
    assert provenance_revocation_status(["signingCredential.ocsp.notRevoked", ""]) == "unchecked"
    assert provenance_revocation_status(["signingCredential.ocsp.notRevoked", 3]) == "unchecked"


def test_timestamp_binding_holds_on_timestamp_validated():
    assert provenance_timestamp_binding_holds(["timeStamp.validated"]) is True
    assert provenance_timestamp_binding_holds(["claimSignature.validated", "timeStamp.validated"]) is True


@pytest.mark.parametrize(
    "codes",
    [
        ["timeStamp.trusted"],
        ["timestamp.boundToClaim"],
        ["timestampAgreement.validated"],
        ["claimSignature.coversTimestamp"],
        [],
    ],
)
def test_timestamp_binding_does_not_hold_without_timestamp_validated(codes):
    assert provenance_timestamp_binding_holds(codes) is False


def test_timestamp_binding_malformed_input_is_false():
    assert provenance_timestamp_binding_holds(None) is False
    assert provenance_timestamp_binding_holds(["timeStamp.validated", None]) is False


def test_validators_agree_separates_revocation_on_spec_codes():
    revoked = ["claimSignature.validated", "assertion.dataHash.match", "signingCredential.ocsp.revoked"]
    fine = ["claimSignature.validated", "assertion.dataHash.match", "signingCredential.ocsp.notRevoked"]
    assert provenance_validators_agree(revoked, fine) is False
    assert provenance_validators_agree(fine, list(fine)) is True
