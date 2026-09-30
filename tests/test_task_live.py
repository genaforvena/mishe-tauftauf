"""Exercise task CLI, actual tmux delivery, yield/clear, and the witness pane."""

import os
import subprocess
import sys
import time
import uuid

from mishe_tauftauf.feed import Feed
from mishe_tauftauf import task_state


def test_mind_selected_task_and_retry_through_live_caller(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-task-test-" + uuid.uuid4().hex[:10]

    def cli(*args, ok=True):
        result = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), *args],
                                capture_output=True, text=True)
        if ok:
            assert result.returncode == 0, result.stderr
        return result

    def wait_for(call, needle):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            text = call()
            if needle in text:
                return text
            time.sleep(0.1)
        raise AssertionError(f"did not observe {needle}: {text}")

    for role in ("genome", "health", "witness"):
        cli("seed", "init", "--slug", role)
        trace = home / (role + ".trace")
        mind = home / "minds" / role
        mind.write_text(f"#!{sys.executable}\nimport sys\n"
                        f"with open({str(trace)!r}, 'a') as f: f.write('ready\\n')\n"
                        "for line in sys.stdin:\n"
                        f" with open({str(trace)!r}, 'a') as f: f.write(line)\n")
        mind.chmod(0o755)
        probe = home / "checks" / "mind-ready" / role
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("#!/bin/sh\nexit 0\n")
        probe.chmod(0o755)
        if role != "witness":
            renderer = home / "top-pains" / role
            renderer.write_text("#!/bin/sh\nprintf 'STATE: GREEN\\n'\n")
            renderer.chmod(0o755)

    try:
        for role in ("genome", "health", "witness"):
            cli("seed", "start", "--session", session, "--slug", role, "--interval", "0.2")
        wait_for(lambda: cli("pain", "read", "health", "--launcher", "tmux", "--session", session).stdout,
                 "-- pane live ")
        Feed(home).append("operator", "[task] inspect-services owner=genome acceptance=checked-service-sample")
        proof = home / "proof.md"
        proof.write_text("Health charter can inspect services; no tracked code mutation is needed.\n")
        cli("task", "step", "inspect-services", "--owner", "genome", "--next-step", "inspect service state",
            "--progress", "scoped read-only service inspection", "--evidence", str(proof))
        first = cli("seed", "tick", "--session", session, "--slug", "health")
        wake = first.stdout.strip().split()[-1]
        assert first.stdout.startswith("wake ")
        wait_for(lambda: (home / "health.trace").read_text(), "MIND SELECTS")
        cli("task", "claim", "inspect-services", "--owner", "health", "--wake", wake,
            "--reason", "Health can carry out this scoped service investigation.", "--evidence", str(proof))
        pane = wait_for(lambda: cli("pain", "read", "witness", "--launcher", "tmux", "--session", session).stdout,
                        "inspect-services owner=health")
        assert "TASK STEP inspect-services owner=health state=waiting" in pane
        cli("task", "wait", "inspect-services", "--owner", "health", "--next-step", "inspect changed service",
            "--reason", "no service change since sample", "--retry-event", "service-changed", "--evidence", str(proof))
        note = home / "handoff.md"
        note.write_text("Verified unchanged service; wait for service-changed.\n")
        cli("seed", "yield", "--slug", "health", "--wake", wake, "--file", str(note), "--result", "verified")
        cli("seed", "clear", "--session", session, "--slug", "health")
        assert "waiting" in cli("seed", "tick", "--session", session, "--slug", "health").stdout
        proof.write_text("A new service state is now checked.\n")
        cli("task", "event", "service-changed", "--source", "operator", "--reason", "checked new service state",
            "--evidence", str(proof))
        retry = cli("seed", "tick", "--session", session, "--slug", "health")
        assert retry.stdout.startswith("wake ")
        wake2 = retry.stdout.strip().split()[-1]
        cli("task", "claim", "inspect-services", "--owner", "health", "--wake", wake2,
            "--reason", "The service-changed retry fired; check the new state.", "--evidence", str(proof))
        cli("task", "wait", "inspect-services", "--owner", "health", "--next-step", "inspect another changed service",
            "--reason", "The new service was checked; wait for another change.", "--retry-event", "service-changed",
            "--evidence", str(proof))
        cli("seed", "yield", "--slug", "health", "--wake", wake2, "--file", str(note), "--result", "verified")
        cli("seed", "clear", "--session", session, "--slug", "health")
        exploration = cli("seed", "tick", "--session", session, "--slug", "health", "--self-pick-seconds", "0.001")
        assert exploration.stdout.startswith("wake ")
        assert 'inspect-services' not in task_state.pending_tasks(Feed(home).entries()).values()
        wake3 = exploration.stdout.strip().split()[-1]
        cli("seed", "yield", "--slug", "health", "--wake", wake3, "--file", str(note), "--result", "verified")
        cli("seed", "clear", "--session", session, "--slug", "health")
        assert "waiting" in cli("seed", "tick", "--session", session, "--slug", "health", "--self-pick-seconds", "0.001").stdout
        assert sum(e.body.startswith("seed wake health ") for e in Feed(home).entries()) == 3
    finally:
        cli("seed", "stop", "--session", session, ok=False)


def test_blocked_goal_delivers_cpu_child_and_completed_evidence_review(tmp_path):
    """Real CLI/tmux producer and reviewer, with deterministic mind stand-ins."""
    import json
    from pathlib import Path
    from mishe_tauftauf import task_state, seed_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-production-test-" + uuid.uuid4().hex[:10]

    def cli(*args, ok=True):
        result = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), *args],
                                capture_output=True, text=True, timeout=30)
        if ok:
            assert result.returncode == 0, result.stderr
        return result

    def await_file(path):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if path.exists():
                return json.loads(path.read_text())
            time.sleep(0.1)
        raise AssertionError(f"producer did not finish: {path}")

    for role in ("genome", "witness"):
        cli("seed", "init", "--slug", role)
        probe = home / "checks/mind-ready" / role
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("#!/bin/sh\nexit 0\n")
        probe.chmod(0o755)
    # Fixture weights and inputs are frozen before the CPU producer runs.
    (home / "cached-model.json").write_text(json.dumps({"weight": 2.0, "bias": 1.0, "inputs": list(range(100))}))
    measured = home / "measured.json"
    reviewed = home / "reviewed.json"
    for role, identity, output in (("genome", "cpu-evaluate", measured), ("witness", "review-evidence", reviewed)):
        work = (
            f"data=json.loads(Path({str(home / 'cached-model.json')!r}).read_text()); "
            "rows=[{'x': x, 'prediction': data['weight']*x+data['bias']} for x in data['inputs']]; "
            f"Path({str(output)!r}).write_text(json.dumps({{'rows':rows,'cases':len(rows)}}))"
            if role == "genome" else
            f"rows=json.loads(Path({str(measured)!r}).read_text())['rows']; "
            "assert len(rows)==100 and all(r['prediction']==2*r['x']+1 for r in rows); "
            f"Path({str(output)!r}).write_text(json.dumps({{'cases':len(rows),'independent_recompute':'PASS'}}))"
        )
        finish = [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "task", "finish", identity,
                  "--owner", role, "--result", "100 CPU rows independently checked" if role == "witness" else "100 CPU rows produced",
                  "--evidence", str(output)]
        mind = home / "minds" / role
        claim = [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "task", "claim", identity,
                 "--owner", role, "--reason", "This scoped producer/reviewer task fits this charter.",
                 "--evidence", str(home / "scope.md")]
        mind.write_text(f"#!{sys.executable}\nimport sys,json,subprocess\nfrom pathlib import Path\n"
                        "for line in sys.stdin:\n"
                        " if line.startswith('WAKE ') and line.split()[1].isdigit():\n"
                        f"  subprocess.run({claim!r}+['--wake',line.split()[1]],check=True)\n"
                        f"  {work}\n"
                        f"  subprocess.run({finish!r},check=True)\n")
        mind.chmod(0o755)
        if role == "genome":
            top = home / "top-pains" / role
            top.write_text("#!/bin/sh\nprintf 'STATE: GREEN\\n'\n")
            top.chmod(0o755)
    proof = home / "scope.md"
    proof.write_text("Registered training waits for historical GPU accounting; independent CPU inference is admitted.")
    cli("task", "add", "research", "--owner", "genome", "--next-step", "finish registered research",
        "--reason", "operator goal", "--evidence", str(proof))
    cli("task", "add", "cpu-evaluate", "--owner", "genome", "--parent", "research",
        "--next-step", "measure frozen cached model on CPU", "--reason", "independent evidence available",
        "--evidence", str(proof))
    cli("task", "wait", "research", "--owner", "genome", "--next-step", "registered replication",
        "--reason", "historical GPU accounting absent", "--producer", "genome", "--retry-event", "gpu-accounted",
        "--alternative", "cpu-evaluate", "--evidence", str(proof))
    cli("task", "add", "review-evidence", "--owner", "witness", "--parent", "research",
        "--next-step", "independently recompute CPU output", "--reason", "review measured artifact",
        "--evidence", str(proof))
    cli("task", "wait", "review-evidence", "--owner", "witness", "--next-step", "independently recompute completed output",
        "--reason", "producer output is not complete", "--producer", "genome", "--retry-task", "cpu-evaluate",
        "--evidence", str(proof))
    try:
        for role in ("genome", "witness"):
            cli("seed", "start", "--session", session, "--slug", role, "--interval", "0.2")
        produced_wake = cli("seed", "tick", "--session", session, "--slug", "genome").stdout.split()[-1]
        assert await_file(measured)["cases"] == 100
        deadline = time.monotonic() + 15
        while "cpu-evaluate" in task_state.states(Feed(home).entries()) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert "cpu-evaluate" not in task_state.states(Feed(home).entries())
        review_wake = cli("seed", "tick", "--session", session, "--slug", "witness").stdout.split()[-1]
        assert await_file(reviewed)["independent_recompute"] == "PASS"
        deadline = time.monotonic() + 15
        while "review-evidence" in task_state.states(Feed(home).entries()) and time.monotonic() < deadline:
            time.sleep(0.1)
        entries = Feed(home).entries()
        assert set(task_state.states(entries)) == {"research"}
        assert {t.identity for t in seed_board.open_tasks(entries)} == {"research"}
        assert task_state.states(entries)["research"].status == "waiting"
        for role, wake, file in (("genome", produced_wake, measured), ("witness", review_wake, reviewed)):
            handoff = home / f"{role}-checked-handoff.md"
            handoff.write_text(f"Completed {role}'s scoped CPU evidence task; 100 cases checked. Evidence: {file}.\n"
                               "Next: choose useful work from the shared task board.\n")
            cli("seed", "yield", "--slug", role, "--wake", wake, "--file", str(handoff), "--result", "changed")
            cli("seed", "clear", "--session", session, "--slug", role)
        cli("task", "add", "next-analysis", "--owner", "genome", "--parent", "research",
            "--next-step", "write measured limitations draft", "--reason", "CPU evidence and review complete",
            "--evidence", str(reviewed))
        assert task_state.select_task(Feed(home).entries(), "genome").identity == "next-analysis"
    finally:
        cli("seed", "stop", "--session", session, ok=False)
