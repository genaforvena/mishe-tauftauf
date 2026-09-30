"""Exercise task CLI, actual tmux delivery, yield/clear, and the witness pane."""

import os
import subprocess
import sys
import time
import uuid

from mishe_tauftauf.feed import Feed


def test_offered_task_and_retry_through_live_caller(tmp_path):
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
        cli("task", "offer", "inspect-services", "--owner", "genome", "--helper", "health", "--evidence", str(proof))
        first = cli("seed", "tick", "--session", session, "--slug", "health")
        wake = first.stdout.strip().split()[-1]
        assert first.stdout.startswith("wake ")
        wait_for(lambda: (home / "health.trace").read_text(), "TASK TO ADVANCE: inspect-services")
        pane = wait_for(lambda: cli("pain", "read", "witness", "--launcher", "tmux", "--session", session).stdout,
                        "inspect-services owner=health")
        assert "TASK STEP inspect-services owner=health state=waiting" in pane
        cli("task", "wait", "inspect-services", "--owner", "health", "--next-step", "inspect changed service",
            "--reason", "no service change since sample", "--retry-event", "service-changed", "--evidence", str(proof))
        note = home / "handoff.md"
        note.write_text("Verified unchanged service; wait for service-changed.\n")
        cli("seed", "yield", "--slug", "health", "--wake", wake, "--file", str(note), "--result", "verified")
        cli("seed", "clear", "--session", session, "--slug", "health")
        assert "waiting" in cli("seed", "tick", "--session", session, "--slug", "health", "--self-pick-seconds", "0.001").stdout
        proof.write_text("A new service state is now checked.\n")
        cli("task", "event", "service-changed", "--source", "operator", "--reason", "checked new service state",
            "--evidence", str(proof))
        retry = cli("seed", "tick", "--session", session, "--slug", "health")
        assert retry.stdout.startswith("wake ")
        wake2 = retry.stdout.strip().split()[-1]
        cli("seed", "yield", "--slug", "health", "--wake", wake2, "--file", str(note), "--result", "verified")
        cli("seed", "clear", "--session", session, "--slug", "health")
        assert "waiting" in cli("seed", "tick", "--session", session, "--slug", "health", "--self-pick-seconds", "0.001").stdout
        assert sum(e.body.startswith("seed wake health ") for e in Feed(home).entries()) == 2
    finally:
        cli("seed", "stop", "--session", session, ok=False)
