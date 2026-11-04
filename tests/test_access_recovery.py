from pathlib import Path

import pytest

from mishe_tauftauf import access
from mishe_tauftauf.feed import Feed


def recover(home, **changes):
    evidence = home / "diagnosis.txt"
    evidence.write_text("Missing fixture reproduced; independent docs work remains ready.")
    args = dict(owner="senses", task="sample", resolver="senses",
                missing="fixture", action="build owned fixture and rerun sample",
                alternatives=["use existing count-only probe"],
                cutoff="2026-10-04T12:00:00Z", evidence=evidence)
    args.update(changes)
    return access.recover(home, "sample-recovery", **args)








@pytest.mark.parametrize("changes", [dict(alternatives=[]), dict(cutoff="tomorrow"),
    dict(cutoff="2026-10-04T12:00:00"), dict(evidence=Path("/etc/hosts")),
    dict(capability="input.count"), dict(action=""), dict(resolver="")])
def test_incomplete_or_unowned_recovery_is_rejected_without_request(tmp_path, changes):
    with pytest.raises(ValueError):
        recover(tmp_path, **changes)
    assert access.list_requests(tmp_path) == []
    assert access.recoveries(tmp_path) == []


def test_recovery_scope_cannot_silently_change(tmp_path):
    first = recover(tmp_path)
    assert recover(tmp_path) == first
    with pytest.raises(ValueError, match="different scope"):
        recover(tmp_path, action="do something else")


def test_resolution_requires_owned_nonempty_evidence(tmp_path):
    recover(tmp_path)
    empty = tmp_path / "empty"
    empty.touch()
    with pytest.raises(ValueError):
        access.resolve_recovery(tmp_path, "sample-recovery", empty, checked_action="build owned fixture and rerun sample")
    assert access.recoveries(tmp_path)[0]["status"] == "repair"




def test_cli_local_repair_and_evidenced_resolution(tmp_path):
    from tests.test_access import run
    proof = tmp_path / "proof"
    proof.write_text("missing fixture reproduced")
    result = run(tmp_path, "recover", "cli-repair", "--owner", "senses", "--task", "sample",
                 "--resolver", "senses", "--missing", "fixture", "--action", "create fixture",
                 "--alternative", "use fallback probe", "--cutoff", "2026-10-04T12:00:00Z",
                 "--evidence", str(proof))
    assert result.returncode == 0, result.stderr
    assert "continue other useful work" in result.stdout
    assert "use fallback probe" in run(tmp_path, "recoveries").stdout
    assert access.list_requests(tmp_path) == []
    assert run(tmp_path, "resolve", "cli-repair", "--evidence", str(proof), "--checked-action", "create fixture").returncode == 0
    assert not run(tmp_path, "recoveries").stdout




def test_wall_permission_decision_addresses_only_responsible_mind(tmp_path):
    from mishe_tauftauf.wall import addressed
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    access.decide(tmp_path, "sample-recovery", "granted")
    decision = list(Feed(tmp_path).entries())[-1]
    assert addressed(decision, "senses")
    assert not addressed(decision, "health")
    spoof = Feed(tmp_path).append("discover", decision.body)
    assert not addressed(spoof, "senses")




@pytest.mark.parametrize("decision", ["pending", "revoked"])
def test_permission_route_cannot_resolve_without_grant(tmp_path, decision):
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    if decision == "revoked":
        access.decide(tmp_path, "sample-recovery", decision)
    with pytest.raises(ValueError, match="grant"):
        access.resolve_recovery(tmp_path, "sample-recovery", tmp_path / "diagnosis.txt",
                                checked_action="build owned fixture and rerun sample")
    assert access.recoveries(tmp_path)[0]["status"] == "permission"




def test_resolution_must_name_recorded_checked_action(tmp_path):
    recover(tmp_path)
    with pytest.raises(ValueError, match="recorded"):
        access.resolve_recovery(tmp_path, "sample-recovery", tmp_path / "diagnosis.txt",
                                checked_action="unrelated action")
