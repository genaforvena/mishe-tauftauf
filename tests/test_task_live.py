"""Exercise canonical wall producer/reviewer delivery through actual tmux."""

import subprocess
import sys
import time
import uuid

from mishe_tauftauf.feed import Feed


def test_wall_cpu_producer_and_independent_reviewer_run_without_ledger_gate(tmp_path):
    """Real CLI/tmux producer and reviewer, with deterministic mind stand-ins."""
    import json
    from pathlib import Path
    from mishe_tauftauf import wall

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
        mind = home / "minds" / role
        mind.write_text(f"#!{sys.executable}\nimport sys,json,subprocess\nfrom pathlib import Path\n"
                        "for line in sys.stdin:\n"
                        " if line.startswith('WAKE ') and line.split()[1].isdigit():\n"
                        f"  {work}\n"
                        "  sys.stdout.flush()\n")
        mind.chmod(0o755)
        if role == "genome":
            top = home / "top-pains" / role
            top.write_text("#!/bin/sh\nprintf 'STATE: GREEN\\n'\n")
            top.chmod(0o755)
    proof = home / "scope.md"
    proof.write_text("Registered training waits for historical GPU accounting; independent CPU inference is admitted.")
    wall.write(home, "genome", "Registered research waits for GPU accounting. Produce available CPU inference independently.")
    wall.write(home, "witness", "Review CPU evidence after genome supplies the artifact; keep GPU accounting unresolved.")
    try:
        for role in ("genome", "witness"):
            cli("seed", "start", "--session", session, "--slug", role, "--interval", "0.2")
        produced_wake = cli("seed", "tick", "--session", session, "--slug", "genome").stdout.split()[-1]
        assert await_file(measured)["cases"] == 100
        Feed(home).append("genome", "[dm] to=witness\nThe measured CPU artifact is ready for independent recomputation.")
        review_wake = cli("seed", "tick", "--session", session, "--slug", "witness").stdout.split()[-1]
        assert await_file(reviewed)["independent_recompute"] == "PASS"
        assert not any(e.body.startswith("[task-claim]") for e in Feed(home).entries())
        for role, wake, file in (("genome", produced_wake, measured), ("witness", review_wake, reviewed)):
            handoff = home / f"{role}-checked-handoff.md"
            handoff.write_text(f"Completed {role}'s scoped CPU evidence task; 100 cases checked. Evidence: {file}.\n"
                               "GPU accounting remains unresolved. Next: investigate independently useful work.\n")
            cli("seed", "yield", "--slug", role, "--wake", wake, "--file", str(handoff), "--result", "changed")
            cli("seed", "clear", "--session", session, "--slug", role)
        assert "GPU accounting remains unresolved" in (home / "walls/genome.md").read_text()
        assert "GPU accounting remains unresolved" in (home / "walls/witness.md").read_text()
        assert not any(e.body.startswith("[work]") for e in Feed(home).entries())
    finally:
        cli("seed", "stop", "--session", session, ok=False)
