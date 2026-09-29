from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

from mishe_tauftauf.feed import Feed


def test_omp_idle_prompt_survives_trailing_attachments_and_rejects_spinner(monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    prompt = " π > INSERT >"
    attachments = "\n".join(f"  attachment card {index}" for index in range(12))
    pane = f"{prompt}\n{attachments}\n\n"
    monkeypatch.setattr(
        seed, "_tmux",
        lambda *args, **kwargs: CompletedProcess(args, 0, b"omp\n") if args[0] == "display-message"
        else CompletedProcess(args, 0, pane.encode()),
    )
    assert seed._mind_ready("session", "genome")

    for output in ("⠋ Working...\n", f"{prompt}\n⠋ Working...\n"):
        monkeypatch.setattr(
            seed, "_tmux",
            lambda *args, output=output, **kwargs: CompletedProcess(args, 0, b"omp\n")
            if args[0] == "display-message" else CompletedProcess(args, 0, output.encode()),
        )
        assert not seed._mind_ready("session", "genome")


def cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "seed", *args],
        text=True, capture_output=True, timeout=20,
    )


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", *args], text=True, capture_output=True, timeout=10)


def wait_for(path: Path, needle: str, seconds: float = 5) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        content = path.read_text() if path.exists() else ""
        if needle in content:
            return content
        time.sleep(0.1)
    raise AssertionError(f"{needle!r} absent from {path}")


def test_doctor_rejects_tracked_local_plant(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    site = repo / ".mishe-tauftauf"
    assert cli(site, "init", "--slug", "genome").returncode == 0
    clean = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(site), "doctor"],
                           capture_output=True, text=True)
    assert "PASS local plant out of Git" in clean.stdout
    (site / "local-plan.md").write_text("node-local work\n")
    subprocess.run(["git", "-C", str(repo), "add", "-f", ".mishe-tauftauf/local-plan.md"], check=True)
    dirty = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(site), "doctor"],
                           capture_output=True, text=True)
    assert dirty.returncode == 1
    assert "HOLD local-plant-in-git" in dirty.stdout


def test_seed_resident_channel_observes_repairs_and_restores(tmp_path: Path) -> None:
    home = tmp_path / "site"
    session = f"mishe-seed-test-{uuid.uuid4().hex[:10]}"
    fixture = tmp_path / "health.txt"
    fixture.write_text("RED\n")
    mind_log = tmp_path / "mind.log"
    assert cli(home, "init", "--slug", "genome").returncode == 0
    assert (home / "charters" / "genome.md").is_file()
    assert "Live evidence" in (home / "doctrine.md").read_text()
    assert "seed run" in (home / "mishe-seed.service").read_text()
    rendered = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                               "pain", "render", "genome"], text=True, capture_output=True)
    assert rendered.returncode == 0, rendered.stderr
    assert "SYSTEM ZERO\nPASS plant doctor" in rendered.stdout
    renderer = home / "top-pains" / "genome"
    renderer.write_text(f"#!/bin/sh\ncat {fixture}\n")
    renderer.chmod(0o755)
    mind = home / "minds" / "genome"
    mind.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        f"log={str(mind_log)!r}\n"
        "with open(log, 'a') as out: out.write('CWD '+os.getcwd()+'\\n')\n"
        f"check=subprocess.run(['mishe-tauftauf','--home',{str(home)!r},'seed','status'],capture_output=True)\n"
        "with open(log, 'a') as out: out.write('CLI '+str(check.returncode)+'\\n')\n"
        "sys.stdout.write('\\x1b[?2004h'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        " with open(log, 'a') as out: out.write(line)\n"
    )
    mind.chmod(0o755)
    try:
        started = cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2")
        assert started.returncode == 0, started.stderr
        panes = tmux("list-panes", "-t", f"{session}:genome", "-F", "#{pane_index}")
        assert panes.stdout.splitlines() == ["0", "1"]
        wait_for(mind_log, "CHARTER genome")
        assert "CLI 0" in mind_log.read_text()
        assert "\x1b[200~" in mind_log.read_text()
        assert "Wait for an explicit WAKE" in mind_log.read_text()
        assert f"pain read genome --launcher tmux --session {session}" in mind_log.read_text()
        assert f"CWD {tmp_path}" in mind_log.read_text()
        assert "Live evidence" in mind_log.read_text()
        assert "Read repository AGENTS.md" in mind_log.read_text()
        first = cli(home, "tick", "--session", session, "--slug", "genome")
        assert first.returncode == 0, first.stderr
        assert "wake" in first.stdout
        wake = first.stdout.strip().split()[-1]
        wait_for(mind_log, f"WAKE {wake}")
        assert "RED" in mind_log.read_text()

        quiet = cli(home, "tick", "--session", session, "--slug", "genome")
        assert quiet.returncode == 0
        assert "held" in quiet.stdout
        assert mind_log.read_text().count(f"WAKE {wake}") == 1
        assert cli(home, "stop", "--session", session).returncode == 0
        assert cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2").returncode == 0
        wait_for(mind_log, f"pending={wake}")
        assert "held" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        assert mind_log.read_text().count(f"WAKE {wake}") == 1

        handoff = tmp_path / "handoff.md"
        handoff.write_text("Checked RED. Repaired fixture. Next: observe GREEN.\n")
        wrong_wake = str(int(wake) + 1)
        rejected = cli(home, "yield", "--slug", "genome", "--wake", wrong_wake, "--file", str(handoff))
        assert rejected.returncode != 0
        assert not (home / "handoffs" / "genome.md").exists()
        assert "held" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        fixture.write_text("GREEN\n")
        time.sleep(0.3)
        during = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "held" in during.stdout
        archive = home / "artifacts" / f"seed-genome-wake-{wake}.md"
        archive.parent.mkdir(exist_ok=True)
        archive.write_text("uncertain prior result\n")
        conflict = cli(home, "yield", "--slug", "genome", "--wake", wake, "--file", str(handoff))
        assert conflict.returncode == 2
        assert "differs" in conflict.stderr
        assert "held" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        archive.unlink()
        settled = cli(home, "yield", "--slug", "genome", "--wake", wake, "--file", str(handoff), "--result", "changed")
        assert settled.returncode == 0, settled.stderr
        assert (home / "handoffs" / "genome.md").read_text() == handoff.read_text()
        assert (home / "artifacts" / f"seed-genome-wake-{wake}.md").read_text() == handoff.read_text()
        assert any("[work] channel=genome" in e.body and "result=changed" in e.body and
                   "Checked RED. Repaired fixture" in e.body for e in Feed(home).entries())
        assert "awaiting clear" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        assert cli(home, "clear", "--session", session, "--slug", "genome").returncode == 0
        second = cli(home, "tick", "--session", session, "--slug", "genome")
        assert second.returncode == 0
        assert "wake" in second.stdout
        assert "GREEN" in wait_for(mind_log, "GREEN")

        wake2 = second.stdout.strip().split()[-1]
        assert cli(home, "yield", "--slug", "genome", "--wake", wake2, "--file", str(handoff), "--continue").returncode == 0
        cleared = cli(home, "clear", "--session", session, "--slug", "genome")
        assert cleared.returncode == 0, cleared.stderr
        wait_for(mind_log, "Context was cleared")
        continuation = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "wake" in continuation.stdout
        wake3 = continuation.stdout.strip().split()[-1]
        assert "CONTINUE TASK" in wait_for(mind_log, "CONTINUE TASK")
        assert cli(home, "yield", "--slug", "genome", "--wake", wake3, "--file", str(handoff)).returncode == 0
        assert cli(home, "clear", "--session", session, "--slug", "genome").returncode == 0
        assert cli(home, "stop", "--session", session).returncode == 0
        assert cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2").returncode == 0
        wait_for(mind_log, "Checked RED. Repaired fixture")
        last = cli(home, "tick", "--session", session, "--slug", "genome")
        assert last.returncode == 0
        assert "quiet" in last.stdout
        assert sum("seed wake genome" in e.body for e in Feed(home).entries()) == 3
        Feed(home).append("genome", "reported my own result")
        assert "quiet" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        Feed(home).append("witness", "routine status report")
        assert "quiet" in cli(home, "tick", "--session", session, "--slug", "genome").stdout
        Feed(home).append("operator", "[task] improve-check owner=genome acceptance=check is clearer")
        wish = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "wake" in wish.stdout
        assert "[task] improve-check owner=genome" in wait_for(mind_log, "[task] improve-check owner=genome")
        wish_wake = wish.stdout.strip().split()[-1]
        assert cli(home, "yield", "--slug", "genome", "--wake", wish_wake, "--file", str(handoff)).returncode == 0
        assert cli(home, "clear", "--session", session, "--slug", "genome").returncode == 0
        time.sleep(0.12)
        self_pick = cli(home, "tick", "--session", session, "--slug", "genome", "--self-pick-seconds", "0.1")
        assert "wake" in self_pick.stdout
        tmux("respawn-pane", "-k", "-t", f"{session}:genome.0", "sh", "-c", "exit 0")
        time.sleep(0.1)
        stale = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "UNKNOWN" in stale.stdout
        tmux("respawn-pane", "-k", "-t", f"{session}:genome.0", "sh", "-c",
             "printf 'GREEN\\n-- pane live 2000-01-01T00:00:00Z · refresh 5s · ticks every frame --\\n'; sleep 10")
        time.sleep(0.1)
        frozen = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "UNKNOWN" in frozen.stdout
    finally:
        cli(home, "stop", "--session", session)


def test_seed_follow_drives_a_checked_repair_without_a_judge(tmp_path: Path) -> None:
    home = tmp_path / "site"
    session = f"mishe-seed-test-{uuid.uuid4().hex[:10]}"
    fixture = tmp_path / "health.txt"
    fixture.write_text("RED\n")
    trace = tmp_path / "mind-trace.txt"
    assert cli(home, "init", "--slug", "genome").returncode == 0
    renderer = home / "top-pains" / "genome"
    renderer.write_text(f"#!/bin/sh\ncat {fixture}\n")
    renderer.chmod(0o755)
    mind = home / "minds" / "genome"
    mind.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, re, subprocess, sys\n"
        f"home=pathlib.Path({str(home)!r})\n"
        f"fixture=pathlib.Path({str(fixture)!r})\n"
        f"trace=pathlib.Path({str(trace)!r})\n"
        "for line in sys.stdin:\n"
        " if line.strip() == '/clear':\n"
        "  with trace.open('a') as out: out.write('saw clear\\n')\n"
        " if match := re.search(r'WAKE (\\d+)', line):\n"
        "  with trace.open('a') as out: out.write('saw '+fixture.read_text().strip()+'\\n')\n"
        "  if fixture.read_text().strip() == 'RED':\n"
        "   fixture.write_text('GREEN\\n')\n"
        "   assert fixture.read_text().strip() == 'GREEN'\n"
        "  note=home/'mind-handoff.md'\n"
        "  note.write_text('Checked fixture: '+fixture.read_text().strip()+'\\n')\n"
        "  subprocess.run([sys.executable,'-m','mishe_tauftauf','--home',str(home),'seed','yield','--slug','genome','--wake',match.group(1),'--file',str(note)],check=True)\n"
    )
    mind.chmod(0o755)
    runner = None
    try:
        runner = subprocess.Popen(
            [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "seed", "run",
             "--session", session, "--interval", "0.2", "--clear-grace-seconds", "0.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        wait_for(trace, "saw RED", seconds=8)
        wait_for(trace, "saw GREEN", seconds=8)
        wait_for(trace, "saw clear", seconds=8)
        assert fixture.read_text() == "GREEN\n"
        assert (home / "handoffs" / "genome.md").read_text() == "Checked fixture: GREEN\n"
    finally:
        if runner is not None:
            runner.terminate()
            runner.communicate(timeout=5)
        cli(home, "stop", "--session", session)


def test_seed_adds_live_channel_to_existing_owned_session(tmp_path: Path) -> None:
    home = tmp_path / "site"
    session = f"mishe-seed-test-{uuid.uuid4().hex[:10]}"
    trace = tmp_path / "resident.txt"
    assert cli(home, "init", "--slug", "genome").returncode == 0
    mind = home / "minds" / "genome"
    mind.write_text(f"#!{sys.executable}\nimport sys\nwith open({str(trace)!r}, 'a') as out: out.write('started\\n')\nfor line in sys.stdin: pass\n")
    mind.chmod(0o755)
    assert tmux("new-session", "-d", "-s", session, "-n", "operator", "sh").returncode == 0
    assert tmux("set-option", "-t", session, "@mishe-tauftauf-home", str(home.resolve())).returncode == 0
    assert tmux("resize-window", "-t", session, "-x", "80", "-y", "24").returncode == 0
    try:
        started = cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2")
        assert started.returncode == 0, started.stderr
        wait_for(trace, "started")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            pane = tmux("capture-pane", "-p", "-t", f"{session}:genome.0").stdout
            if "-- pane live " in pane:
                break
            time.sleep(0.1)
        assert "STATE: GREEN" in pane
        assert "WORKTREE" in pane
        assert "-- pane live " in pane
        assert tmux("list-panes", "-t", f"{session}:operator", "-F", "#{pane_index}").stdout.splitlines() == ["0"]
        refused = cli(home, "stop", "--session", session)
        assert refused.returncode == 2
        assert "not raised by this seed" in refused.stderr
        assert tmux("has-session", "-t", session).returncode == 0
    finally:
        tmux("kill-session", "-t", session)


def test_seed_witness_profile_reads_chat_and_reports_missing_channels(tmp_path: Path) -> None:
    home = tmp_path / "site"
    assert cli(home, "init", "--slug", "genome").returncode == 0
    assert cli(home, "init", "--slug", "witness").returncode == 0
    assert "coordination steward" in (home / "charters" / "witness.md").read_text()
    Feed(home).append("operator", "[wish] inspect the missing genome channel")
    view = subprocess.run([str(home / "top-pains" / "witness")],
                          env={"MISHE_SEED_SESSION": "missing-test-session", "PATH": "/usr/bin:/bin"},
                          text=True, capture_output=True)
    assert view.returncode == 0, view.stderr
    assert "STATE: UNKNOWN" in view.stdout
    assert "[wish] inspect the missing genome channel" in view.stdout
    assert "UNKNOWN witness tmux unavailable" in (home / "observations" / "witness").read_text()
    Feed(home).append("witness", "[task] repair-pane owner=genome source=top-pains/genome acceptance=live-pane retry=next-wake")
    view = subprocess.run([str(home / "top-pains" / "witness")], text=True, capture_output=True)
    assert "OPEN TASKS: 1" in view.stdout
    assert "repair-pane owner=genome state=open" in view.stdout
    Feed(home).append("genome", "[taking] repair-pane")
    Feed(home).append("genome", "[done] repair-pane — live pane checked")
    for wake in range(1, 4):
        Feed(home).append("seed", f"[work] channel=genome wake={wake} observation=5 result=verified continue=0 handoff_sha256=abc archive=note")
    view = subprocess.run([str(home / "top-pains" / "witness")], text=True, capture_output=True)
    assert "OPEN TASKS: 0" in view.stdout
    assert "LOOP: RED" in view.stdout
    assert "FAIL witness repeated genome no-change turns" in (home / "observations" / "witness").read_text()
