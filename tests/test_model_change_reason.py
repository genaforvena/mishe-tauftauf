"""Top-level model-change reports name the per-role cause.

Controlled failure: the live guard reported UNKNOWN with "no named reason"
while every affected role carried 'desired launcher differs from receipt'.
"""
from __future__ import annotations

from mishe_tauftauf import model_change


def test_role_reason_names_each_role_cause():
    roles = {
        "health": {"state": "conflict", "reason": "desired launcher differs from receipt"},
        "senses": {"state": "conflict", "reason": "desired launcher differs from receipt"},
        "pub": {"state": "verified"},
    }
    assert model_change.role_reason(roles) == (
        "health: desired launcher differs from receipt; "
        "senses: desired launcher differs from receipt"
    )


def test_role_reason_is_none_without_named_causes():
    assert model_change.role_reason({"pub": {"state": "unknown"}}) is None
    assert model_change.role_reason({}) is None


def test_role_reason_is_bounded():
    roles = {f"role{i}": {"reason": "x" * 80} for i in range(5)}
    assert len(model_change.role_reason(roles)) <= 200
