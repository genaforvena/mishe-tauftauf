"""Reconciled acceptance for the two old fleet privacy drafts."""
from pathlib import Path
from unittest.mock import patch
import pytest
from mishe_tauftauf.external_view import safe_fleet_view, FLEET_ROLES, FLEET_SEMANTICS
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig
from mishe_tauftauf.cli import initialize

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


def test_always_yes_judge_fails_specialized_fleet_controls(tmp_path):
    initialize(tmp_path)
    judge=adapter(tmp_path)
    runner=Coordinator(RuntimeConfig(tmp_path, judge=judge, launcher="headless", dispatch=False,
                                     external_fleet_view_slugs=("vpn",)))
    assert "fleet-publish" in runner.control_failures
    assert "fleet-desired-state-met" in runner.control_failures


def test_cached_failed_fleet_control_cannot_become_a_pass(tmp_path):
    initialize(tmp_path)
    judge=adapter(tmp_path)
    args=dict(judge=judge, launcher="headless", dispatch=False, external_fleet_view_slugs=("vpn",), control_cache_ttl=3600)
    with patch.object(Coordinator,"_run_controls",return_value={"fleet-publish":"paired control failed"}):
        Coordinator(RuntimeConfig(tmp_path,refresh_controls=True,**args))
    cached=Coordinator(RuntimeConfig(tmp_path,**args))
    with patch.object(cached,"_raw_judge",wraps=cached._raw_judge) as spy:
        verdict=cached._judge("publish","vpn","PRIVATE-PANE",VPN,"PRIVATE-PREDICTION")
    assert verdict.outcome == "unknown"
    spy.assert_not_called()


def test_observe_persists_only_sanitized_record_and_deduplicates_its_meaning(tmp_path):
    initialize(tmp_path)
    judge=adapter(tmp_path)
    top=tmp_path/"top-pains/vpn"
    projector=tmp_path/"projectors/vpn"
    def executable(path,body):
        path.write_text("#!/usr/bin/env python3\n"+body)
        path.chmod(0o755)
    executable(top,"print('PRIVATE-PANE-1')\n")
    executable(projector,"print("+repr(VPN)+",end='')\n")
    runner=Coordinator(RuntimeConfig(tmp_path,judge=judge,launcher="headless",dispatch=False,
                                     external_fleet_view_slugs=("vpn",),slug="vpn"))
    runner.control_failures={}
    with patch.object(runner,"_raw_judge",wraps=runner._raw_judge) as spy:
        first=runner.observe()
        observations=[e.body for e in first if e.source=="observation/vpn"]
        assert observations==[safe_fleet_view(VPN,"vpn")]
        executable(top,"print('PRIVATE-PANE-2')\n")
        executable(projector,"print("+repr(VPN.replace("peers-online=2","peers-online=3"))+",end='')\n")
        assert not any(e.source=="observation/vpn" for e in runner.observe())
        assert spy.call_count==1
