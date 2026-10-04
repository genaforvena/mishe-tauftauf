"""Canonical defaults exercise fresh plants without a trial installer."""
import json
import subprocess
import pytest
from mishe_tauftauf import seed, wall, observations, ci_watch, post_check
from mishe_tauftauf.feed import Feed


def test_missing_configuration_means_open_wall_workflow(tmp_path):
    assert wall.settings(tmp_path).get("mode") == "wall"
    assert not wall.settings(tmp_path).get("until")


def test_mode_is_not_a_feature_switch(tmp_path):
    (tmp_path / "coordination-mode.json").write_text('{"mode":"ledger"}')
    with pytest.raises(ValueError, match="retired"):
        wall.settings(tmp_path)


def test_configuration_can_omit_historical_mode_key(tmp_path):
    (tmp_path / "coordination-mode.json").write_text('{"silence_seconds":900}')
    assert wall.settings(tmp_path)["mode"] == "wall"


def test_default_planning_settlement_without_ledger_claim(tmp_path):
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nChoose useful work.")
    note = tmp_path / "note.md"
    note.write_text("Investigated the mechanism; next bounded step is a live experiment.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=True, result="verified")
    assert (tmp_path / "walls/genome.md").read_text() == note.read_text()
    assert seed._state(tmp_path, "genome")[1] is None


def test_default_feed_never_runs_semantic_publication_gate(tmp_path, monkeypatch):
    (tmp_path / "publication-check.json").write_text('{"command":["missing-checker"]}')
    def reject(*a, **k):
        raise AssertionError("legacy semantic publication gate")
    monkeypatch.setattr(post_check, "require", reject)
    assert Feed(tmp_path).append("genome", "Planning an authorized experiment; checks follow.").sequence == 1


def test_default_frame_shows_wall_and_sensor_failure(tmp_path, monkeypatch):
    wall.write(tmp_path, "health", "Diagnosing the failed reading.")
    monkeypatch.setattr(observations, "run_renderer", lambda *a: observations.RenderedPain("health", "STATE: RED failure\n", False))
    frame = observations.compose_frame(tmp_path, "health")
    assert not frame.ok
    assert "Diagnosing the failed reading." in frame.body and "RENDERER: RED" in frame.body


@pytest.mark.parametrize("role", ["genome", "health", "witness", "docs", "research-methods"])
def test_fresh_seed_renders_canonical_view(tmp_path, role):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    home = tmp_path / "site"
    seed.init(home, role, "true")
    script = (home / "top-pains" / role).read_text()
    assert "mishe_tauftauf.wall_view" in script
    assert "landing_debt" not in script
    assert wall.settings(home)["mode"] == "wall"


def test_default_ci_routes_a_message_without_delivery_scheduler(tmp_path, monkeypatch):
    from mishe_tauftauf import delivery
    monkeypatch.setattr(ci_watch, "read", lambda h: {"state":"fail", "sha":"a"*40, "run":"1", "url":"https://example.invalid", "detail":"failed"})
    monkeypatch.setattr(delivery, "check_all", lambda h: (_ for _ in ()).throw(AssertionError("retired delivery scheduler")))
    ci_watch.tick(tmp_path)
    bodies = [e.body for e in Feed(tmp_path).entries()]
    assert any(b.startswith("[dm] to=genome") for b in bodies)
    assert not any(b.startswith("[task]") for b in bodies)


def test_chat_keeps_structured_state_in_referenced_records(tmp_path):
    from mishe_tauftauf.post_check import CorrectionRequired
    with pytest.raises(CorrectionRequired):
        Feed(tmp_path).append("genome", '{"claim":"delivered"}')
    assert Feed(tmp_path).tail_sequence() == 0


@pytest.mark.parametrize("command", [["task", "add", "obsolete", "--owner", "genome", "--next-step", "work", "--reason", "reason", "--evidence", "missing"], ["delivery", "check", "obsolete"]])
def test_retired_cli_cannot_restart_alternate_workflow(tmp_path, command, capsys):
    from mishe_tauftauf.cli import main
    assert main(["--home", str(tmp_path), *command]) != 0
    assert "retired" in capsys.readouterr().err
    assert Feed(tmp_path).tail_sequence() == 0


def test_settlement_retry_cannot_claim_different_notes(tmp_path):
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; no delivery claim.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    note.write_text("Different effect falsely attributed to the settled turn.")
    with pytest.raises(ValueError, match="differ"):
        seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")


def test_clear_retry_records_rotated_process_without_rotating_twice(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; next trigger is changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    pid = ["101"]
    rotations = []
    def command(*args, **kwargs):
        if args[0] == "respawn-pane":
            rotations.append(args)
            pid[0] = "102"
        value = pid[0] if args[-1] == "#{pane_pid}" else "0"
        return CompletedProcess(args, 0, value.encode(), b"")
    monkeypatch.setattr(seed, "_tmux", command)
    original = Feed.append
    def fail_receipt(self, source, body, **kwargs):
        if body.startswith("seed clear "):
            raise RuntimeError("receipt unavailable after rotation")
        return original(self, source, body, **kwargs)
    monkeypatch.setattr(Feed, "append", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        seed.clear(tmp_path, "session", "genome")
    monkeypatch.setattr(Feed, "append", original)
    assert seed.clear(tmp_path, "session", "genome").startswith("clear seed")
    assert len(rotations) == 1
    assert seed._state(tmp_path, "genome")[3] == wake.sequence


def test_fresh_seed_records_a_stable_activity_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    home = tmp_path / "site"
    seed.init(home, "genome", "true")
    first = wall.settings(home)
    assert first.get("started")
    seed.init(home, "health", "true")
    assert wall.settings(home)["started"] == first["started"]


def test_actual_plant_contract_replaces_once_and_preserves_local_instructions():
    from pathlib import Path
    from mishe_tauftauf.plant import refresh_contract
    import mishe_tauftauf.seed as seed_module
    contract = (Path(seed_module.__file__).parent / "seed_agent_contract.md").read_text()
    first = refresh_contract("Local project instructions.\n", contract)
    assert refresh_contract(first, contract) == first
    assert first.count("# Working in Mishe") == 1
    assert "Local project instructions." in first


def test_clear_recovers_crash_after_respawn_before_rotation_journal(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux, post_check
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"; note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_launch_argv", lambda *a: ("-c", str(tmp_path.parent), "env", "PYTHONPATH=/reviewed/src", "/site/minds/genome"))
    pid=["101"]; rotations=[]
    def command(*args, **kwargs):
        if args[0]=="respawn-pane": rotations.append(args); pid[0]="102"
        value = pid[0] if args[-1]=="#{pane_pid}" else "env PYTHONPATH=/reviewed/src /site/minds/genome" if args[-1]=="#{pane_start_command}" else "0"
        return CompletedProcess(args,0,value.encode(),b"")
    monkeypatch.setattr(seed,"_tmux",command)
    save=post_check._save
    def interrupted(path,value):
        if "wall-clear" in path.name and value.get("phase")=="rotated": raise RuntimeError("crash before rotated journal")
        return save(path,value)
    monkeypatch.setattr(post_check,"_save",interrupted)
    with pytest.raises(RuntimeError,match="crash before"):
        seed.clear(tmp_path,"session","genome")
    monkeypatch.setattr(post_check,"_save",save)
    assert seed.clear(tmp_path,"session","genome").startswith("clear seed")
    assert len(rotations)==1
    assert seed._state(tmp_path,"genome")[3]==wake.sequence


def test_clear_interruption_recovers_with_real_tmux_launcher(tmp_path, monkeypatch):
    import shutil, uuid
    from mishe_tauftauf import post_check, tmux
    if not shutil.which("tmux"):
        pytest.skip("tmux unavailable")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    home=tmp_path/"site"; seed.init(home,"genome","sleep 30")
    session="mishe-canonical-recovery-"+uuid.uuid4().hex[:10]
    def run(*args):
        return subprocess.run(["tmux",*args],check=True,capture_output=True,text=True)
    run("new-session","-d","-s",session,"-n","genome","-c",str(tmp_path),"sleep 30")
    try:
        run("set-option","-t",session,"@mishe-tauftauf-home",str(home))
        run("split-window","-d","-v","-t",session+":genome","-c",str(tmp_path),"sleep 30")
        assert tmux.owns_session(home,session)
        wake=Feed(home).append("seed","seed wake genome observation=1\nInvestigate.")
        note=home/"note.md";note.write_text("Checked an isolated rotation recovery.")
        seed.yield_wake(home,"genome",wake.sequence,note,result="verified")
        monkeypatch.setattr(seed,"_mind_idle",lambda *a:True)
        save=post_check._save
        def interrupted(path,value):
            if "wall-clear" in path.name and value.get("phase")=="rotated": raise RuntimeError("interrupt after actual respawn")
            return save(path,value)
        monkeypatch.setattr(post_check,"_save",interrupted)
        with pytest.raises(RuntimeError,match="actual respawn"):
            seed.clear(home,session,"genome")
        after=run("display-message","-p","-t",session+":genome.1","#{pane_pid}").stdout.strip()
        monkeypatch.setattr(post_check,"_save",save)
        assert seed.clear(home,session,"genome").startswith("clear seed")
        assert run("display-message","-p","-t",session+":genome.1","#{pane_pid}").stdout.strip()==after
        journal=json.loads((home/"checks"/f"wall-clear-genome-{wake.sequence}.json").read_text())
        assert journal["reconciled_intent"] and journal["phase"]=="committed"
    finally:
        run("kill-session","-t",session)
