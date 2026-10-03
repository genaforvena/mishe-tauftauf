from pathlib import Path

import pytest

from mishe_tauftauf import access
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed_culture_views import permissions
from mishe_tauftauf.wall import restore


def recover(home, **changes):
    evidence = home / "diagnosis.txt"
    evidence.write_text("Missing fixture reproduced; independent docs work remains ready.")
    args = dict(owner="senses", task="sample", resolver="senses",
                missing="fixture", action="build owned fixture and rerun sample",
                alternatives=["use existing count-only probe"],
                cutoff="2026-10-04T12:00:00Z", evidence=evidence)
    args.update(changes)
    return access.recover(home, "sample-recovery", **args)


def test_local_recovery_has_actions_without_permission_or_wake(tmp_path):
    before = list(Feed(tmp_path).entries())
    row = recover(tmp_path)
    assert row["status"] == "repair"
    assert access.list_requests(tmp_path) == []
    assert list(Feed(tmp_path).entries()) == before
    pane = permissions(tmp_path)
    assert "sample-recovery REPAIR" in pane
    assert "build owned fixture" in pane
    assert "use existing count-only probe" in pane
    assert "2026-10-04T12:00:00+00:00" in pane


def test_permission_is_routed_but_grant_does_not_close_recovery(tmp_path):
    recover(tmp_path, capability="input.count", unblocks=["sample/verify"],
            reason="count probe requires missing device access")
    assert access.state(tmp_path, "sample-recovery") == "pending"
    assert access.recoveries(tmp_path)[0]["status"] == "permission"
    access.decide(tmp_path, "sample-recovery", "granted")
    assert access.recoveries(tmp_path)[0]["status"] == "retry"
    assert "GRANTED; VERIFY RECOVERY" in permissions(tmp_path)
    proof = tmp_path / "result.txt"
    proof.write_text("sample now succeeds")
    access.resolve_recovery(tmp_path, "sample-recovery", proof, checked_action="build owned fixture and rerun sample")
    assert access.recoveries(tmp_path) == []
    assert access.recoveries(tmp_path, include_resolved=True)[0]["status"] == "resolved"
    assert "sample-recovery" not in permissions(tmp_path).split("RECOVERIES:")[-1]


def test_revocation_keeps_alternatives_visible(tmp_path):
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    access.decide(tmp_path, "sample-recovery", "revoked")
    assert access.recoveries(tmp_path)[0]["status"] == "permission"
    assert "use existing count-only probe" in permissions(tmp_path)


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


def test_restore_does_not_turn_recovery_into_a_blocked_mind(tmp_path):
    prompt = restore(tmp_path, "senses", "test")
    assert "does not make the mind blocked" in prompt
    assert "permit recover" in prompt
    assert "grant is not recovery" in prompt
    assert "Do not task claim" in prompt


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


def test_menu_shows_alternatives_before_grant(tmp_path, monkeypatch):
    import curses
    from tests.test_permission_menu import Screen
    from mishe_tauftauf.permission_menu import interactive
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    screen = Screen([curses.KEY_NPAGE, ord("q")], size=(40, 100))
    interactive(screen, tmp_path)
    assert "use existing count-only probe" in " ".join(screen.text)
    assert "success still needs evidence" in " ".join(screen.text)
    assert access.state(tmp_path, "sample-recovery") == "pending"


def test_wall_permission_decision_addresses_only_responsible_mind(tmp_path):
    from mishe_tauftauf.wall import addressed
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    access.decide(tmp_path, "sample-recovery", "granted")
    decision = list(Feed(tmp_path).entries())[-1]
    assert addressed(decision, "senses")
    assert not addressed(decision, "health")
    spoof = Feed(tmp_path).append("discover", decision.body)
    assert not addressed(spoof, "senses")


def test_unresolved_recovery_does_not_gate_independent_progress(tmp_path):
    from tests.test_wall import setup_wall
    from mishe_tauftauf import seed
    setup_wall(tmp_path)
    recover(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake senses observation=1\nChoose useful work.")
    notes = tmp_path / "notes.md"
    notes.write_text("Independent probe verified. Fixture recovery remains on my plate; next: build fixture.")
    assert "yield" in seed.yield_wake(tmp_path, "senses", wake.sequence, notes,
                                      continue_task=True, result="verified")
    assert access.recoveries(tmp_path)[0]["status"] == "repair"


@pytest.mark.parametrize("decision", ["pending", "revoked"])
def test_permission_route_cannot_resolve_without_grant(tmp_path, decision):
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    if decision == "revoked":
        access.decide(tmp_path, "sample-recovery", decision)
    with pytest.raises(ValueError, match="grant"):
        access.resolve_recovery(tmp_path, "sample-recovery", tmp_path / "diagnosis.txt",
                                checked_action="build owned fixture and rerun sample")
    assert access.recoveries(tmp_path)[0]["status"] == "permission"


def test_permitted_fallback_resolves_without_new_permission_and_retires_selector(tmp_path, monkeypatch):
    import curses
    from tests.test_permission_menu import Screen
    from mishe_tauftauf.permission_menu import interactive
    recover(tmp_path, capability="input.count", unblocks=["sample"], reason="device access")
    proof = tmp_path / "fallback-result"
    proof.write_text("existing probe succeeded")
    access.resolve_recovery(tmp_path, "sample-recovery", proof,
                            checked_action="use existing count-only probe", via_alternative=True)
    assert access.recoveries(tmp_path) == []
    assert access.state(tmp_path, "sample-recovery") == "pending"  # Historical decision unchanged.
    with pytest.raises(ValueError, match="retired"):
        access.decide(tmp_path, "sample-recovery", "granted")
    monkeypatch.setattr(curses, "curs_set", lambda value: None)
    screen = Screen([10, ord("q")])
    interactive(screen, tmp_path)
    assert "No requests to grant" in " ".join(screen.text)
    assert "RETIRED" in permissions(tmp_path)


def test_resolution_must_name_recorded_checked_action(tmp_path):
    recover(tmp_path)
    with pytest.raises(ValueError, match="recorded"):
        access.resolve_recovery(tmp_path, "sample-recovery", tmp_path / "diagnosis.txt",
                                checked_action="unrelated action")
