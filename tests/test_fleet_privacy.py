"""Reconciled acceptance for the two old fleet privacy drafts."""
from pathlib import Path
from unittest.mock import patch
import pytest
from mishe_tauftauf.external_view import safe_fleet_view, FLEET_ROLES, FLEET_SEMANTICS

VPN = "STATE: RED\nOBSERVATION: source=top-pane/vpn freshness=fresh value-coverage=unknown peers-online=2 peers-offline=1\n"
WITNESS = "STATE: RED\nOBSERVATION: source=top-pane/witness freshness=fresh journal=fresh tasks-total=5 tasks-unfinished=3 tasks-unowned=1 mishe-issues=2 issue-digest=abcdef0123456789\n"

@pytest.mark.parametrize("role", sorted(FLEET_ROLES))
def test_every_fleet_role_sanitized_record_round_trips(role):
    fields = {"cleaner": "cleaner=fresh value-coverage=partial review=pass",
              "witness": "journal=fresh tasks-total=5 tasks-unfinished=3 tasks-unowned=1",
              "vpn": "value-coverage=unknown peers-online=2 peers-offline=1",
              "tg": "value-coverage=unknown rx=up textin=down",
              "tg-roz": "value-coverage=unknown send=blocked",
              "job": "value-coverage=unknown pipeline=present",
              "pub": "comments=fresh value-coverage=partial"}.get(role, "value-coverage=unknown")
    if role in FLEET_SEMANTICS:
        fields += " semantic=" + FLEET_SEMANTICS[role] + ":fresh"
    raw = f"STATE: RED\nOBSERVATION: source=top-pane/{role} freshness=fresh {fields}\n"
    safe = safe_fleet_view(raw, role)
    assert safe is not None
    assert safe_fleet_view(safe, role) == safe
    assert safe_fleet_view(safe, "vpn" if role != "vpn" else "witness") is None

@pytest.mark.parametrize("raw,role,expected", [(VPN,"vpn",("peers=offline-present",)),
    (WITNESS,"witness",("tasks=unowned","issues=present"))])
def test_private_counts_become_actionable_enums(raw,role,expected):
    safe = safe_fleet_view(raw,role)
    for field in expected: assert field in safe
    assert not any(field in safe for field in ("tasks-total", "tasks-unfinished", "tasks-unowned", "peers-online", "peers-offline", "issue-digest", "mishe-issues", "abcdef0123456789"))

@pytest.mark.parametrize("raw", [WITNESS.replace("tasks-unowned=1","tasks-unowned=4"),
    WITNESS.replace("tasks-unfinished=3","tasks-unfinished=6"),
    WITNESS.replace("journal=fresh", "journal=fresh tasks=unowned"),
    VPN.replace("peers-online=2", "peers=online peers-online=2")])
def test_inconsistent_or_mixed_raw_and_canonical_counts_are_refused(raw):
    assert safe_fleet_view(raw, "vpn" if "source=top-pane/vpn " in raw else "witness") is None


def adapter(home):
    path=home/"spy-judge"
    path.write_text("#!/usr/bin/env python3\nprint('probability 0.9')\n")
    path.chmod(0o755)
    return path






