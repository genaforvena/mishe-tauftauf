from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from mishe_tauftauf.feed import Feed


def seeded_home(tmp_path: Path, name: str = "site") -> Path:
    """A site home whose parent directory is a Git worktree, as plant() requires."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / name
    return home


def test_new_session_raises_the_server_in_its_own_scope(monkeypatch) -> None:
    # A seed supervisor's cgroup would otherwise own the tmux server, so an
    # activation restarting the seed set cgroup-kills the session and every turn.
    from mishe_tauftauf import seed

    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    runs = []
    monkeypatch.setattr(seed.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(seed.subprocess, "run", lambda argv, **kwargs: runs.append(argv))
    seed._new_session("mishe-core", "genome", "/workspace")
    assert runs and runs[0][:6] == ["/usr/bin/systemd-run", "--user", "--scope", "--collect",
                                    "--unit", "mishe-core-session.scope"]
    assert runs[0][6:8] == ["--description", "Mishe resident session mishe-core"]
    assert runs[0][8:] == ["tmux", "new-session", "-d", "-s", "mishe-core",
                           "-n", "genome", "-c", "/workspace", "sh"]


def test_new_session_falls_back_to_plain_tmux_without_systemd(monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    calls = []
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(seed.shutil, "which", lambda name: "")
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: (calls.append(args), CompletedProcess(args, 0, b""))[1])
    seed._new_session("mishe-core", "genome", "/workspace")
    assert calls == [("new-session", "-d", "-s", "mishe-core", "-n", "genome", "-c", "/workspace", "sh")]


def test_runner_override_selects_the_plain_tmux_path(monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    # An explicit empty runner is the opt-out for hosts without systemd; it must
    # not fall back to a discovered systemd-run.
    calls = []
    monkeypatch.setenv("MISHE_SESSION_RUNNER", "")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(seed.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: (calls.append(args), CompletedProcess(args, 0, b""))[1])
    runs = []
    monkeypatch.setattr(seed.subprocess, "run", lambda argv, **kwargs: runs.append(argv))
    seed._new_session("mishe-unit-test-session", "genome", "/workspace")
    assert calls == [("new-session", "-d", "-s", "mishe-unit-test-session",
                      "-n", "genome", "-c", "/workspace", "sh")]
    assert runs == []


def test_new_session_attaches_when_a_peer_won_the_raise(monkeypatch) -> None:
    from subprocess import CalledProcessError, CompletedProcess

    from mishe_tauftauf import seed

    # Concurrent supervisors race one fixed scope unit name, so every loser fails
    # with "unit was already loaded". A peer's session exists: attach, don't exit.
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(seed.shutil, "which", lambda name: "/usr/bin/systemd-run")

    def losing_run(argv, **kwargs):
        raise CalledProcessError(1, argv)

    monkeypatch.setattr(seed.subprocess, "run", losing_run)
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(args, 0, b""))
    seed._new_session("mishe-core", "genome", "/workspace")


def test_new_session_reraises_when_no_peer_session_appears(monkeypatch) -> None:
    import pytest
    from subprocess import CalledProcessError, CompletedProcess

    from mishe_tauftauf import seed

    # A raise that is not a lost race must stay fatal instead of being swallowed.
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(seed.shutil, "which", lambda name: "/usr/bin/systemd-run")

    def failing_run(argv, **kwargs):
        raise CalledProcessError(1, argv)

    monkeypatch.setattr(seed.subprocess, "run", failing_run)
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(args, 1, b""))
    monkeypatch.setattr(seed.time, "sleep", lambda _seconds: None)
    clock = iter([0.0, 0.0, 1e9, 2e9])
    monkeypatch.setattr(seed.time, "monotonic", lambda: next(clock))
    with pytest.raises(CalledProcessError):
        seed._new_session("mishe-core", "genome", "/workspace")


def test_health_observation_ignores_changing_samples_but_keeps_verdicts() -> None:
    from mishe_tauftauf.seed import _observation_text

    frame = ("GOAL: tend machine health\nDOCTOR: PASS\nSERVICE health.service: active\n"
             "HOST HEALTH: PASS measured checks\n/: bytes available=100\n"
             "STATE: GREEN — measured checks pass\n"
             "COVERAGE: UNKNOWN — memory pressure not yet gated\n"
             "SYSTEM ZERO\nPASS plant=PASS machine=PASS\n")
    baseline = _observation_text("health", frame)
    assert _observation_text("health", frame.replace("available=100", "available=99")) == baseline
    assert _observation_text("health", frame.replace("HOST HEALTH: PASS", "HOST HEALTH: FAIL")) != baseline
    assert _observation_text("health", frame.replace("COVERAGE: UNKNOWN", "COVERAGE: VERIFIED")) != baseline
    assert _observation_text("health", frame.replace("SERVICE health.service: active",
                                                      "SERVICE health.service: failed")) != baseline


def test_witness_observation_ignores_own_chat_counters_but_keeps_signals() -> None:
    from mishe_tauftauf.seed import _observation_text

    frame = ("WINDOWS: PASS genome,health,witness\nCI: PASS sha=abc\nOPEN TASKS: 2\n"
             "task-a owner=genome state=open at=40\n"
             "task-b owner=health state=taking at=41\n"
             "GENOME WORK: 1:changed@42\n"
             "CHAT RATE: RED — seed observation witness: 18 observations\n"
             "LATEST CHAT.LOG TEXT\nA new sample at 12:00\nSTATE: RED\nCI: FAIL chat text\n"
             "GOAL: keep work coherent\nSTATE: GREEN\n")
    baseline = _observation_text("witness", frame)
    assert _observation_text("witness", frame.replace("18 observations", "19 observations")
                             .replace("1:changed@42", "2:changed@43")
                             .replace("12:00", "12:01")
                             .replace("STATE: RED", "STATE: UNKNOWN")
                             .replace("CI: FAIL chat text", "CI: PASS chat text")
                             .replace("state=open at=40", "state=open at=44")) == baseline
    assert _observation_text("witness", frame.replace("CHAT RATE: RED", "CHAT RATE: GREEN")) != baseline
    assert _observation_text("witness", frame.replace("CI: PASS", "CI: FAIL")) != baseline
    assert _observation_text("witness", frame.replace("OPEN TASKS: 2", "OPEN TASKS: 3")) != baseline
    assert _observation_text("witness", frame.replace("task-a", "task-c")) != baseline
    assert _observation_text("witness", frame.replace("state=taking", "state=open")) != baseline
    assert _observation_text("witness", frame.replace("STATE: GREEN", "STATE: RED")) != baseline


def test_omp_idle_prompt_survives_trailing_attachments_and_rejects_spinner(monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    attachments = "\n".join(f"  attachment card {index}" for index in range(12))
    for prompt in (" π > INSERT >", " π > INSERT y >"):
        pane = f"{prompt}\n{attachments}\n\n"
        monkeypatch.setattr(
            seed, "_tmux",
            lambda *args, **kwargs: CompletedProcess(args, 0, b"omp\n") if args[0] == "display-message"
            else CompletedProcess(args, 0, pane.encode()),
        )
        assert seed._mind_ready("session", "genome")

    for output in ("⠋ Working...\n", f"{prompt}\n⠋ Working...\n",
                   f"{prompt}\n ⠇ 6m > INSERT > ⬢ Atria Dawn Preview\n",
                   f"{prompt}\n ⠇ 6m > INSERT y > ⬢ Atria Dawn Preview\n"):
        monkeypatch.setattr(
            seed, "_tmux",
            lambda *args, output=output, **kwargs: CompletedProcess(args, 0, b"omp\n")
            if args[0] == "display-message" else CompletedProcess(args, 0, output.encode()),
        )
        assert not seed._mind_ready("session", "genome")


def test_custom_mind_requires_a_site_readiness_probe(tmp_path: Path, monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    home = tmp_path / "site"
    monkeypatch.setattr(
        seed, "_tmux",
        lambda *args, **kwargs: CompletedProcess(args, 0, ("custom-agent\n" if args[0] == "display-message"
                                                     else str(home) + "\n").encode()),
    )
    assert not seed._mind_ready("session", "genome")
    probe = home / "checks" / "mind-ready" / "genome"
    probe.parent.mkdir(parents=True)
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    assert seed._mind_ready("session", "genome")


def test_omp_normal_idle_is_ready_but_pending_keys_and_spinner_are_not(monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import seed
    for pane, ready in ((" π > NORMAL > ◑ GPT-6-Luna", True),
                        (" π > NORMAL d >", False),
                        (" π > NORMAL >\n ⠇ 6m > NORMAL >", False)):
        monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(
            args, 0, ("omp\n" if args[0] == "display-message" else pane).encode()))
        assert seed._mind_ready("session", "genome") is ready


def test_omp_normal_delivery_enters_insert_before_typing(monkeypatch):
    from subprocess import CompletedProcess
    from mishe_tauftauf import seed
    calls = []
    def fake_tmux(*args, **kwargs):
        calls.append(args)
        inserted = ("send-keys", "-t", "session:genome.1", "i") in calls
        output = "omp\n" if args[0] == "display-message" else " π > " + ("INSERT" if inserted else "NORMAL") + " >"
        return CompletedProcess(args, 0, output.encode())
    monkeypatch.setattr(seed, "_tmux", fake_tmux)
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    seed._send("session:genome.1", "/clear")
    assert calls.index(("send-keys", "-t", "session:genome.1", "i")) < calls.index(("send-keys", "-t", "session:genome.1", "C-u"))


def test_omp_delivery_holds_when_idle_mode_cannot_be_verified(monkeypatch):
    from subprocess import CompletedProcess
    import pytest
    from mishe_tauftauf import seed
    for pane, capture_code in (("", 1), (" π > NORMAL d >", 0),
                               (" π > NORMAL >\n ⠇ 6m > NORMAL >", 0),
                               (" π > NORMAL >", 0)):
        calls = []
        def fake_tmux(*args, **kwargs):
            calls.append(args)
            return CompletedProcess(args, 0 if args[0] == "display-message" else capture_code,
                                    ("omp\n" if args[0] == "display-message" else pane).encode())
        monkeypatch.setattr(seed, "_tmux", fake_tmux)
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        with pytest.raises(ValueError, match="delivery held"):
            seed._send("session:genome.1", "/clear")
        assert not any(args[0] == "send-keys" and args[-1] != "i" for args in calls)


def test_codex_mind_requires_its_idle_prompt(monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    pane = "› Ask Codex to do anything\n"
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(
        args, 0, ("codex\n" if args[0] == "display-message" else pane).encode()))
    assert seed._mind_ready("session", "genome")
    pane = "› Ask Codex to do anything\n• Working on a task\n"
    assert not seed._mind_ready("session", "genome")


def test_redelivered_wake_includes_restored_charter_and_handoff(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from mishe_tauftauf import seed

    home = tmp_path / "site"
    handoff = home / "handoffs" / "witness.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("Prior checked step and exact next action.\n")
    wake = Feed(home).append("seed", "seed wake witness observation=1\nChecked source changed.", reserved=True)
    sent = []
    monkeypatch.setattr(seed, "_mind_ready", lambda *args: True)
    monkeypatch.setattr(seed, "_send", lambda target, message: sent.append((target, message)))

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(seconds=61)

    monkeypatch.setattr(seed, "datetime", Later)
    assert "redelivered" in seed._redeliver_pending(home, "session", "witness", wake.sequence)
    assert sent[0][0] == "session:witness.1"
    assert "CHARTER witness" in sent[0][1]
    assert "Prior checked step and exact next action." in sent[0][1]
    assert f"REDELIVERY of unsettled WAKE {wake.sequence}" in sent[0][1]


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


def test_core_instruction_updates_reach_existing_site_without_losing_local_additions(tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf import seed

    home = seeded_home(tmp_path)
    seed.init(home, "witness")
    original = seed._core_doctrine()
    monkeypatch.setattr(seed, "_core_doctrine", lambda home=None: original + "\nNew core rule.\n")
    seed.init(home, "witness")
    assert "New core rule." in (home / "doctrine.md").read_text()
    assert "New core rule." in seed._restore_text(home, "witness", "test")

    (home / "doctrine.md").write_text("Local site addition.\n")
    seed.init(home, "witness")
    restored = seed._restore_text(home, "witness", "test")
    assert "New core rule." in restored
    assert "Local site addition." in restored
    assert (home / "doctrine.md").read_text() == "Local site addition.\n"


def test_pre_baseline_doctrine_is_refreshed_as_a_default(tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf import seed

    home = seeded_home(tmp_path)
    (home / "doctrine.md").parent.mkdir()
    (home / "doctrine.md").write_text("legacy copy\n")
    digest = seed._digest
    old_hash = next(iter(seed.LEGACY_INSTRUCTION_HASHES["doctrine.md"]))
    monkeypatch.setattr(seed, "_digest", lambda value: old_hash if value == "legacy copy\n" else digest(value))
    seed.init(home, "witness")
    assert (home / "doctrine.md").read_text() == seed._core_doctrine()


def test_large_wake_reaches_tmux_mind_without_command_limit(tmp_path: Path) -> None:
    from mishe_tauftauf import seed
    from mishe_tauftauf.tmux import OWNED_OPTION

    home = tmp_path / "site"
    home.mkdir()
    received = tmp_path / "received.txt"
    ready = tmp_path / "receiver-ready.txt"
    session = f"mishe-large-wake-{uuid.uuid4().hex[:10]}"
    started = tmux("new-session", "-d", "-s", session, "-n", "genome",
                   f"sh -c 'stty raw -echo; printf ready > {ready}; cat > {received}'")
    assert started.returncode == 0, started.stderr
    try:
        owned = tmux("set-option", "-t", session, OWNED_OPTION, str(home))
        assert owned.returncode == 0, owned.stderr
        # tmux creation can return before stty disables canonical input limits.
        wait_for(ready, "ready")
        message = "WAKE " + "x" * 20000 + " END"
        seed._send(f"{session}:genome.0", message)
        assert message in wait_for(received, " END")
        assert list((home / "tmp").iterdir()) == []
    finally:
        tmux("kill-session", "-t", session)


def test_omp_attachment_gets_a_submission_enter_only_while_idle(tmp_path: Path, monkeypatch) -> None:
    from subprocess import CompletedProcess

    from mishe_tauftauf import seed

    for pane_text, expected_enters in (
        ("╭── 📄 #1 ───╮\n│WAKE text│\n╰ +100 lines ╯\n π > INSERT >", 2),
        ("╭── 📄 #1 ───╮\n⠋ Working...\n π > INSERT >", 1),
        ("╭── 📄 #1 ───╮\n│WAKE text│\n╰ +100 lines ╯\n"
         "The prior answer completed.\n π > INSERT >", 1),
    ):
        calls: list[tuple[str, ...]] = []
        captures = 0

        def fake_tmux(*args: str, **_kwargs):
            nonlocal captures
            calls.append(args)
            if args[0] == "capture-pane":
                captures += 1
            output = (str(tmp_path) + "\n" if args[0] == "show-option" else
                      "omp\n" if args[0] == "display-message" else
                      (" π > INSERT >" if captures == 1 else pane_text) if args[0] == "capture-pane" else "")
            return CompletedProcess(args, 0, output.encode())

        monkeypatch.setattr(seed, "_tmux", fake_tmux)
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        seed._send("session:witness.1", "WAKE text")
        assert sum(args[:4] == ("send-keys", "-t", "session:witness.1", "C-m")
                   for args in calls) == expected_enters


def test_failed_paste_cleans_site_file_and_tmux_buffer(tmp_path: Path, monkeypatch) -> None:
    from subprocess import CompletedProcess

    import pytest

    from mishe_tauftauf import seed
    from mishe_tauftauf.tmux import TmuxError

    calls: list[tuple[str, ...]] = []

    def fake_tmux(*args: str, **_kwargs):
        calls.append(args)
        if args[0] == "paste-buffer":
            raise TmuxError("pane disappeared")
        output = str(tmp_path) + "\n" if args[0] == "show-option" else ""
        return CompletedProcess(args, 0, output.encode())

    monkeypatch.setattr(seed, "_tmux", fake_tmux)
    with pytest.raises(TmuxError, match="pane disappeared"):
        seed._send("session:witness.1", "WAKE secret context")
    assert any(args[0] == "delete-buffer" for args in calls)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_failed_temp_write_removes_partial_wake(tmp_path: Path, monkeypatch) -> None:
    from subprocess import CompletedProcess

    import pytest

    from mishe_tauftauf import seed

    def fake_tmux(*args: str, **_kwargs):
        output = str(tmp_path) + "\n" if args[0] == "show-option" else ""
        return CompletedProcess(args, 0, output.encode())

    def failed_file(**kwargs):
        path = Path(kwargs["dir"]) / "wake-partial"
        path.write_text("partial")

        class File:
            name = str(path)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, _message):
                raise OSError("disk full")

        return File()

    monkeypatch.setattr(seed, "_tmux", fake_tmux)
    monkeypatch.setattr(seed.tempfile, "NamedTemporaryFile", failed_file)
    with pytest.raises(OSError, match="disk full"):
        seed._send("session:witness.1", "WAKE long context")
    assert list((tmp_path / "tmp").iterdir()) == []


def test_generic_top_pain_ci_line_survives_shell_quoting(tmp_path: Path) -> None:
    """The generic top-pain embeds a `python3 -c` in a shell string.

    A Python repr wrapped in shell single quotes collapses to `Path(/home/...)`
    and dies with SyntaxError, silently dropping the CI line from the pane.
    The renderer must keep the Python quotes through the shell layer.
    """
    home = seeded_home(tmp_path)
    assert cli(home, "init", "--slug", "genome").returncode == 0
    renderer = home / "top-pains" / "genome"
    command = subprocess.run(["sh", str(renderer)], capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert "SyntaxError" not in command.stderr
    assert "invalid syntax" not in command.stderr

def test_seed_resident_channel_observes_repairs_and_restores(tmp_path: Path) -> None:
    home = seeded_home(tmp_path)
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
        "with open(log, 'a') as out: out.write('RUNTIME '+str(os.environ.get('XDG_RUNTIME_DIR'))+'\\n')\n"
        "with open(log, 'a') as out: out.write('SITE '+str(os.environ.get('MISHE_SEED_HOME'))+'\\n')\n"
        f"check=subprocess.run(['mishe-tauftauf','--home',{str(home)!r},'seed','status'],capture_output=True)\n"
        "with open(log, 'a') as out: out.write('CLI '+str(check.returncode)+'\\n')\n"
        "sys.stdout.write('\\x1b[?2004h'); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        " with open(log, 'a') as out: out.write(line)\n"
    )
    mind.chmod(0o755)
    probe = home / "checks" / "mind-ready" / "genome"
    probe.parent.mkdir(parents=True)
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    try:
        started = cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2")
        assert started.returncode == 0, started.stderr
        panes = tmux("list-panes", "-t", f"{session}:genome", "-F", "#{pane_index}")
        assert panes.stdout.splitlines() == ["0", "1"]
        wait_for(mind_log, "CWD ")
        assert "CLI 0" in mind_log.read_text()
        assert "CHARTER genome" not in mind_log.read_text()
        assert f"CWD {tmp_path}" in mind_log.read_text()
        assert f"RUNTIME {os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}" in mind_log.read_text()
        assert f"SITE {home}" in mind_log.read_text()
        first = cli(home, "tick", "--session", session, "--slug", "genome")
        assert first.returncode == 0, first.stderr
        assert "wake" in first.stdout
        wake = first.stdout.strip().split()[-1]
        wait_for(mind_log, f"WAKE {wake}")
        assert "CHARTER genome" in mind_log.read_text()
        assert "\x1b[200~" in mind_log.read_text()
        assert f"pain read genome --launcher tmux --session {session}" in mind_log.read_text()
        assert "Live evidence" in mind_log.read_text()
        assert "Read repository AGENTS.md" in mind_log.read_text()
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
        before_clear = mind_log.read_text()
        context_count = before_clear.count("CHARTER genome")
        starts = before_clear.count(f"CWD {tmp_path}")
        old_pid = tmux("display-message", "-pt", f"{session}:genome.1", "#{pane_pid}").stdout.strip()
        assert cli(home, "clear", "--session", session, "--slug", "genome").returncode == 0
        new_pid = tmux("display-message", "-pt", f"{session}:genome.1", "#{pane_pid}").stdout.strip()
        assert new_pid != old_pid
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and mind_log.read_text().count(f"CWD {tmp_path}") < starts + 1:
            time.sleep(0.05)
        assert mind_log.read_text().count(f"CWD {tmp_path}") == starts + 1
        assert mind_log.read_text().count("CHARTER genome") == context_count
        second = cli(home, "tick", "--session", session, "--slug", "genome")
        assert second.returncode == 0
        assert "wake" in second.stdout
        assert "GREEN" in wait_for(mind_log, "GREEN")
        assert mind_log.read_text().count("CHARTER genome") == context_count + 1
        assert "Checked RED. Repaired fixture" in mind_log.read_text()

        wake2 = second.stdout.strip().split()[-1]
        assert cli(home, "yield", "--slug", "genome", "--wake", wake2, "--file", str(handoff), "--continue").returncode == 0
        cleared = cli(home, "clear", "--session", session, "--slug", "genome")
        assert cleared.returncode == 0, cleared.stderr
        assert mind_log.read_text().count("CHARTER genome") == context_count + 1
        continuation = cli(home, "tick", "--session", session, "--slug", "genome")
        assert "wake" in continuation.stdout
        wake3 = continuation.stdout.strip().split()[-1]
        assert "CONTINUE TASK" in wait_for(mind_log, "CONTINUE TASK")
        assert cli(home, "yield", "--slug", "genome", "--wake", wake3, "--file", str(handoff)).returncode == 0
        assert cli(home, "clear", "--session", session, "--slug", "genome").returncode == 0
        assert cli(home, "stop", "--session", session).returncode == 0
        context_count = mind_log.read_text().count("CHARTER genome")
        assert cli(home, "start", "--session", session, "--slug", "genome", "--interval", "0.2").returncode == 0
        assert mind_log.read_text().count("CHARTER genome") == context_count
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
        Feed(home).append("genome", "[done] improve-check — checked the addressed task")
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
    home = seeded_home(tmp_path)
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
        "with trace.open('a') as out: out.write('started\\n')\n"
        "for line in sys.stdin:\n"
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
    probe = home / "checks" / "mind-ready" / "genome"
    probe.parent.mkdir(parents=True)
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    runner = None
    try:
        runner = subprocess.Popen(
            [sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "seed", "run",
             "--session", session, "--interval", "0.2", "--clear-grace-seconds", "0.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        wait_for(trace, "saw RED", seconds=8)
        wait_for(trace, "saw GREEN", seconds=8)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and trace.read_text().count("started") < 2:
            time.sleep(0.05)
        assert trace.read_text().count("started") >= 2
        assert fixture.read_text() == "GREEN\n"
        assert (home / "handoffs" / "genome.md").read_text() == "Checked fixture: GREEN\n"
    finally:
        if runner is not None:
            runner.terminate()
            runner.communicate(timeout=5)
        cli(home, "stop", "--session", session)


def test_seed_run_holds_on_checker_outage_instead_of_exiting(monkeypatch, capsys) -> None:
    """An unavailable semantic checker must not kill the resident supervisor."""
    import pytest

    from mishe_tauftauf import seed

    calls = {"tick": 0}

    def fake_tick(*args, **kwargs):
        calls["tick"] += 1
        if calls["tick"] == 1:
            raise ValueError("Codex CLI failed with exit 1")
        raise KeyboardInterrupt

    monkeypatch.setattr(seed, "start", lambda *args, **kwargs: "ready")
    monkeypatch.setattr(seed, "tick", fake_tick)
    monkeypatch.setattr(seed, "_clear_due", lambda *args, **kwargs: False)
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        seed.run(Path("/nonexistent-site"), "mishe-test", "genome", 0.1, 0)
    output = capsys.readouterr().out
    assert "HOLD seed genome tick: Codex CLI failed with exit 1" in output
    assert calls["tick"] == 2


def test_seed_adds_live_channel_to_existing_owned_session(tmp_path: Path) -> None:
    home = seeded_home(tmp_path)
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
    home = seeded_home(tmp_path)
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
