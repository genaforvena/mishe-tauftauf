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
        Feed(tmp_path).append("genome", '{"status":"done","counts":[1,2,3],"nested":{"a":1}}')
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


def test_a_clear_rotation_records_the_model_the_new_mind_runs(tmp_path, monkeypatch):
    """The wall-clear respawn is the only moment the running model can change."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    pid = ["101"]
    def command(*args, **kwargs):
        if args[0] == "respawn-pane":
            pid[0] = "102"
        value = pid[0] if args[-1] == "#{pane_pid}" else "0"
        return CompletedProcess(args, 0, value.encode(), b"")
    monkeypatch.setattr(seed, "_tmux", command)
    assert seed.clear(tmp_path, "session", "genome").startswith("clear seed")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [f"mind model top-pain genome launcher={launcher} reason=clear model=vendor/model-a"]


def test_a_seed_start_records_the_model_at_the_first_respawn(tmp_path, monkeypatch):
    """A first start respawns the mind pane, so it records identity too."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    probe = home / "top-pains" / "genome"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    launcher = home / "minds" / "genome"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_new_session", lambda *a: None)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_state", lambda home, slug, **kw: (None, None, None, None, None, None, None, None))
    pids = iter(["101\n", "202\n"])
    def command(*args, **kwargs):
        if args[0] == "has-session":
            return CompletedProcess(args, 1, b"", b"")
        if args[-1] == "#{session_id}":
            return CompletedProcess(args, 0, b"$1\n", b"")
        if args[-1] == "#{pane_pid}":
            return CompletedProcess(args, 0, next(pids).encode(), b"")
        return CompletedProcess(args, 0, b"0\n", b"")
    monkeypatch.setattr(seed, "_tmux", command)
    assert seed.start(home, "session", "genome", 5).startswith("seed genome ready")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [f"mind model top-pain genome launcher={launcher} reason=start model=vendor/model-a"]


def test_seed_start_waits_for_a_concurrent_raise_to_record_its_owner(tmp_path, monkeypatch):
    """Boot race: a peer creates the session, then records the owning home.

    A second supervisor that observes the session between those two steps must
    wait for the owner, not report the still-unowned session as foreign. On the
    host's default.target start this window crash-looped permissions,
    research-methods and senses once each before their systemd restart.
    """
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    probe = home / "top-pains" / "genome"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    launcher = home / "minds" / "genome"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    launcher.chmod(0o755)
    owners = iter(["", str(home.resolve())])

    def owner_tmux(*args, **kwargs):
        if args[0] == "show-option":
            return CompletedProcess(args, 0, (next(owners, str(home.resolve())) + "\n").encode())
        return CompletedProcess(args, 0, b"")

    monkeypatch.setattr(tmux, "_tmux", owner_tmux)
    monkeypatch.setattr(tmux.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_state", lambda home, slug, **kw: (None, None, None, None, None, None, None, None))
    pids = iter(["101\n", "202\n"])

    def command(*args, **kwargs):
        if args[0] == "has-session":
            return CompletedProcess(args, 0, b"$1\n", b"")
        if args[-1] == "#{pane_pid}":
            return CompletedProcess(args, 0, next(pids).encode(), b"")
        return CompletedProcess(args, 0, b"0\n", b"")

    monkeypatch.setattr(seed, "_tmux", command)
    assert seed.start(home, "session", "genome", 5).startswith("seed genome ready")


def test_a_failed_respawn_records_no_model_identity(tmp_path, monkeypatch):
    """A respawn that never happened must not be attributed a model."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    def command(*args, **kwargs):
        if args[0] == "respawn-pane":
            raise RuntimeError("tmux refused the respawn")
        value = "0" if args[-1] == "#{pane_dead}" else "101\n"
        return CompletedProcess(args, 0, value.encode(), b"")
    monkeypatch.setattr(seed, "_tmux", command)
    with pytest.raises(RuntimeError, match="refused the respawn"):
        seed.clear(tmp_path, "session", "genome")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [], "no respawn means nothing to attribute a model to"


def test_the_record_fires_only_after_the_process_actually_rotated(tmp_path, monkeypatch):
    """A respawn whose pid never changes raises, and no identity is recorded."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    def command(*args, **kwargs):
        value = "101\n" if args[-1] == "#{pane_pid}" else "0\n"
        return CompletedProcess(args, 0, value.encode(), b"")
    monkeypatch.setattr(seed, "_tmux", command)
    with pytest.raises(ValueError, match="mind process did not rotate"):
        seed.clear(tmp_path, "session", "genome")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [], "a respawn that did not rotate is not a new invocation"


def test_a_rotation_reconciled_from_the_rotating_phase_still_records(tmp_path, monkeypatch):
    """A respawn that finished before its journal write must still be recorded."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux, wall

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    (tmp_path / "checks").mkdir()
    argv = list(seed._mind_launch_argv(tmp_path, "genome")[2:])
    (tmp_path / "checks" / f"wall-clear-genome-{wake.sequence}.json").write_text(
        '{"phase":"rotating","session":"session","role":"genome","settled":%d,'
        '"before_pid":"101","launch_command":%s}' % (wake.sequence, json.dumps(argv)))
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    def command(*args, **kwargs):
        if args[-1] == "#{pane_start_command}":
            return CompletedProcess(args, 0, " ".join(argv).encode(), b"")
        if args[-1] == "#{pane_pid}":
            return CompletedProcess(args, 0, b"202\n", b"")
        return CompletedProcess(args, 0, b"0\n", b"")
    monkeypatch.setattr(seed, "_tmux", command)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    assert seed.clear(tmp_path, "session", "genome").startswith("clear seed")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [f"mind model top-pain genome launcher={launcher} reason=clear model=vendor/model-a"]


def test_a_failed_rotated_journal_save_records_no_model_identity(tmp_path, monkeypatch):
    """The durable rotated journal must land before the feed claims a new model."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import post_check, tmux

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    pids = iter(["101\n", "202\n", "202\n", "202\n", "202\n"])
    def command(*args, **kwargs):
        if args[-1] == "#{pane_pid}":
            return CompletedProcess(args, 0, next(pids).encode(), b"")
        return CompletedProcess(args, 0, b"0\n", b"")
    monkeypatch.setattr(seed, "_tmux", command)
    real_save = post_check._save
    def save(path, report):
        if report.get("phase") == "rotated":
            raise OSError("journal write failed")
        return real_save(path, report)
    monkeypatch.setattr(post_check, "_save", save)
    with pytest.raises(OSError, match="journal write failed"):
        seed.clear(tmp_path, "session", "genome")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [], "a rotation whose journal did not land is not established"


def test_a_seed_start_records_only_a_live_respawned_mind(tmp_path, monkeypatch):
    """A respawn that leaves a dead mind pane records nothing."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    probe = home / "top-pains" / "genome"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    launcher = home / "minds" / "genome"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_new_session", lambda *a: None)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_state", lambda home, slug, **kw: (None, None, None, None, None, None, None, None))
    def command(*args, **kwargs):
        if args[0] == "has-session":
            return CompletedProcess(args, 1, b"", b"")
        if args[-1] == "#{pane_dead}":
            return CompletedProcess(args, 0, b"1\n", b"")
        return CompletedProcess(args, 0, b"0\n", b"")
    monkeypatch.setattr(seed, "_tmux", command)
    with pytest.raises(ValueError, match="mind pane session:genome.1 did not respawn"):
        seed.start(home, "session", "genome", 5)
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [], "a mind that is still dead has not started"


def test_an_interrupted_rotated_clear_records_the_model_on_retry(tmp_path, monkeypatch):
    """A crash between the durable rotated save and the record is repaired on retry."""
    from subprocess import CompletedProcess
    from mishe_tauftauf import tmux

    launcher = tmp_path / "minds" / "genome"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; wait for changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    (tmp_path / "checks").mkdir()
    argv = list(seed._mind_launch_argv(tmp_path, "genome")[2:])
    (tmp_path / "checks" / f"wall-clear-genome-{wake.sequence}.json").write_text(
        '{"phase":"rotated","session":"session","role":"genome","settled":%d,'
        '"before_pid":"101","after_pid":"202","launch_command":%s}' % (wake.sequence, json.dumps(argv)))
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a: True)
    def command(*args, **kwargs):
        value = "202\n" if args[-1] == "#{pane_pid}" else "0\n"
        return CompletedProcess(args, 0, value.encode(), b"")
    monkeypatch.setattr(seed, "_tmux", command)
    marker = f"mind model top-pain genome launcher={launcher} reason=clear model=vendor/model-a"
    assert seed.clear(tmp_path, "session", "genome").startswith("clear seed")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [marker], "the interrupted rotation is recorded when the clear is retried"
    assert seed.clear(tmp_path, "session", "genome").startswith("held seed")
    bodies = [e.body.splitlines()[0] for e in Feed(tmp_path).entries()
              if e.source == "mishe-tauftauf" and e.body.startswith("mind model ")]
    assert bodies == [marker], "the repaired record is emitted exactly once"
