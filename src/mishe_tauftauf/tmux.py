from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .observations import FOOTER_LEASE_RE, discover, strip_owned_chrome
from .proc_table import children_by_parent, pane_pids

OWNED_OPTION = "@mishe-tauftauf-home"
_LAST_REPAIR: dict[tuple[str, str], float] = {}


class TmuxError(RuntimeError):
    pass


def _default_socket() -> str:
    """The legacy default tmux socket, before per-site sockets existed."""
    return str(Path("/tmp") / f"tmux-{os.getuid()}" / "default")


def _socket_for_home(home: Path) -> str:
    """The per-site tmux socket for a home, honoring inherited TMUX_TMPDIR.

    Mirrors seed._new_session's derivation (site-root TMUX_TMPDIR, else the
    per-site default under /tmp); duplicated here to avoid a circular import.
    """
    tmux_tmpdir = os.environ.get("TMUX_TMPDIR") or str(Path("/tmp") / f"tmux-{os.getuid()}" / home.parent.name)
    return str(Path(tmux_tmpdir) / f"tmux-{os.getuid()}" / "default")


def _site_socket(home: Path) -> str:
    """The socket of ``home``'s own site root, derived from the home alone.

    The ambient socket is the *caller's* site root, so a process carrying
    another site's TMUX_TMPDIR cannot reach a target home through
    :func:`_socket_for_home`: the coordinator unit's drop-in set it to the core
    root, so every probe of a linked home derived the core socket and held a
    live site every 60 s (android-body kernel-sync-6868441d20a6: "tmux session
    is not owned by linked site").
    """
    uid = os.getuid()
    return str(Path("/tmp") / f"tmux-{uid}" / home.parent.name / f"tmux-{uid}" / "default")


def _socket_candidates(home: Path) -> tuple[str, ...]:
    """Every socket that may host a home's session, the caller's own first."""
    return tuple(dict.fromkeys((_socket_for_home(home), _default_socket(), _site_socket(home))))


def _recorded_owner(result: subprocess.CompletedProcess[bytes]) -> str | None:
    """The owner a ``show-option`` probe recorded, or None when it did not answer."""
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip() or None


def owned_socket(home: Path, session: str) -> str | None:
    """The socket whose session records ``home`` as its owner, or None.

    The caller's own socket is probed first, so the documented site-root
    contract and the test harness are unchanged, and the legacy default stays
    reachable for records raised before per-site sockets existed. The home's
    own site socket is the last authority: a caller that carries a *foreign*
    TMUX_TMPDIR — the release coordinator under its unit drop-in — finds there
    the session its target owns instead of holding a live site forever.

    Callers that write (kill, respawn, raise) must act on the socket this
    returns, never on the ambient one, or a verified ownership could be
    followed by a write to another site's server.
    """
    wanted = str(home.resolve())
    for socket in _socket_candidates(home):
        result = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False, socket=socket)
        if _recorded_owner(result) == wanted:
            return socket
    return None


def session_owner(session: str, home: Path | None = None) -> str | None:
    """The home recorded on the session, or None while it is unset or absent.

    When ``home`` is given, the per-site socket is derived from it (the same
    way ``_new_session`` derives the socket) so callers that already hold a
    home do not need to set TMUX_TMPDIR.  Without ``home`` the behaviour is
    unchanged: an inherited TMUX_TMPDIR or the default socket is used.

    This stays ambient on purpose: a caller that may carry another site's
    TMUX_TMPDIR uses :func:`owned_socket` instead, so no reader here can
    disagree with a writer about which server it is talking to.
    """
    if home is None:
        result = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False)
    else:
        socket = _socket_for_home(home)
        result = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False, socket=socket)
        # Fall back to the inherited/common socket when the home-derived server
        # does not contain this session (e.g. tests and legacy records). A live
        # server answers a missing session with rc=0 and empty output, so the
        # fallback must fire on either signal.
        if result.returncode != 0 or not result.stdout:
            result = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False)
    return _recorded_owner(result)


def owns_session(home: Path, session: str) -> bool:
    return session_owner(session, home=home) == str(home.resolve())

def _python_command(home: Path, *args: str) -> list[str]:
    """Carry the byte-set ``home`` pins into panes owned by an older tmux server."""
    from .runtime_source import pane_python_path
    search_path = pane_python_path(home, Path(__file__).resolve().parents[2])
    return ["env", f"PYTHONPATH={search_path}", sys.executable, "-m", "mishe_tauftauf", *args]


def _tmux(*args: str, check: bool = True, socket: str | None = None) -> subprocess.CompletedProcess[bytes]:
    if shutil.which("tmux") is None:
        raise TmuxError("tmux executable is unavailable")
    cmd = ["tmux"]
    if socket:
        cmd.extend(["-S", socket])
    cmd.extend(args)
    environment = os.environ.copy()
    if environment.get("TMUX_TMPDIR"):
        # An inherited pane socket otherwise overrides the explicit test/site
        # directory, turning fixture kill-server into a live-server teardown.
        environment.pop("TMUX", None)
    result = subprocess.run(cmd, capture_output=True, env=environment)
    if check and result.returncode:
        raise TmuxError(result.stderr.decode("utf-8", "replace").strip() or f"tmux {' '.join(args)} failed")
    return result


def await_owned(home: Path, session: str, timeout: float = 15.0) -> bool:
    """Wait out a concurrent raise that created the session but has not owned it yet.

    A supervisor raises the session and only then records the owning home, so a
    second supervisor can observe the session with the option still unset. That
    window is not a foreign session: wait for the owner to be recorded, and fail
    at once only when a *different* home appears, or when the deadline passes.
    """
    wanted = str(home.resolve())
    deadline = time.monotonic() + timeout
    while True:
        owner = session_owner(session, home=home)
        if owner == wanted:
            return True
        if owner is not None:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def start(home: Path, session: str = "mishe-tauftauf", interval: float = 5.0) -> None:
    if _tmux("has-session", "-t", session, check=False).returncode != 0:
        _tmux("new-session", "-d", "-s", session, "-n", "bootstrap")
        _tmux("set-option", "-t", session, OWNED_OPTION, str(home.resolve()))
    elif not await_owned(home, session):
        raise TmuxError(f"session {session!r} exists but is not owned by {home}")
    command = _python_command(home, "--home", str(home), "pain", "watch")
    slugs = discover(home)
    if "observability" not in slugs:
        _install_observability(home, session)
        slugs = discover(home)
    for slug in slugs:
        target = f"{session}:{slug}"
        if _tmux("list-windows", "-t", session, "-F", "#{window_name}", check=False).stdout.decode().splitlines().count(slug) == 0:
            _tmux("new-window", "-d", "-t", session, "-n", slug, "sh")
            _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
            _tmux("split-window", "-v", "-t", target, "sh", "-c", "printf 'Mind surface ready\\n'; exec sh")
            _tmux("respawn-pane", "-k", "-t", f"{target}.0", *command, slug, "--interval", str(interval))
        panes = _tmux("list-panes", "-t", target, "-F", "#{pane_index}").stdout.decode().splitlines()
        if "1" not in panes:
            _tmux("split-window", "-v", "-t", target, "sh", "-c", "printf 'Mind surface ready\\n'; exec sh")
        _tmux("set-option", "-p", "-t", f"{target}.1", "remain-on-exit", "on")
        _tmux("select-layout", "-t", target, "even-vertical")
    if "bootstrap" in _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines() and slugs:
        _tmux("kill-window", "-t", f"{session}:bootstrap")


def _install_observability(home: Path, session: str) -> None:
    path = home / "top-pains" / "observability"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    quoted_session = shlex.quote(session)
    quoted_feed = shlex.quote(str(home / "chat.log" if (home / "chat.log").exists() else home / "feed"))
    script = f'''#!/bin/sh
printf '%s\\n' 'DESIRED STATE: every Top Pain lease advances and failures remain visible' 'UNRESOLVED: pane liveness is checked independently'
if command -v tmux >/dev/null 2>&1 && tmux has-session -t {quoted_session} 2>/dev/null; then
  for window in $(tmux list-windows -t {quoted_session} -F '#{{window_name}}' 2>/dev/null); do
    lease=$(tmux capture-pane -p -t {quoted_session}:\"$window\".0 -S -50 2>/dev/null | sed -n '/^-- pane live /p' | tail -n 1)
    if [ -n \"$lease\" ]; then printf 'window %s · %s\\n' \"$window\" \"$lease\"; else printf 'UNKNOWN — window %s has no visible lease\\n' \"$window\"; fi
  done
else
  printf '%s\\n' 'UNKNOWN — tmux session unavailable'
fi
printf '%s\\n' 'OUTSTANDING OBSERVATION FAILURES'
if [ -r {quoted_feed} ]; then
  sed -n '/^    | .*\\(UNKNOWN\\|HOLD pane-\\)/p' {quoted_feed} | tail -n 20
else
  printf '%s\\n' 'UNKNOWN — feed unavailable'
fi
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def stop(home: Path, session: str = "mishe-tauftauf") -> None:
    if _tmux("has-session", "-t", session, check=False).returncode != 0:
        return
    if not owns_session(home, session):
        raise TmuxError(f"refusing to remove unowned session {session!r}")
    _tmux("kill-session", "-t", session)

def list_sessions(socket: str | None = None) -> list[str]:
    """All session names on a specific tmux socket (or default), or [] when tmux is missing."""
    if shutil.which("tmux") is None:
        return []
    result = _tmux("list-sessions", "-F", "#{session_name}", check=False, socket=socket)
    if result.returncode:
        return []
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line]


def _pane_descendants(session: str, socket: str | None = None) -> list[int]:
    """Every pid under a session's panes, the panes themselves included.

    A seed mind is started in its own session so closing a pane cannot SIGHUP an
    in-flight mind, so that child outlives a leaked ``kill-session``. While the
    pane still lives its parent link is intact, so the tree is read before the
    session is killed and reaped afterwards.
    """
    result = _tmux("list-panes", "-t", session, "-F", "#{pane_pid}", check=False, socket=socket)
    if result.returncode:
        return []
    pending = pane_pids(result.stdout)
    children = children_by_parent()
    found: list[int] = []
    while pending:
        pid = pending.pop()
        found.append(pid)
        pending.extend(children.get(pid, ()))
    return found

def _all_tmux_sockets() -> list[str]:
    """All tmux sockets under /tmp, including pytest TMUX_TMPDIR directories.

    The orphan sweeper needs to scan every socket, not just the default one,
    because pytest fixtures create per-test sockets via TMUX_TMPDIR. Orphaned
    test sessions on those sockets are invisible to the default-socket sweep.
    """
    sockets: set[str] = set()
    # Default socket
    uid = os.getuid()
    default = Path(f"/tmp/tmux-{uid}/default")
    if default.exists():
        sockets.add(str(default))
    # Pytest sockets: /tmp/pytest-of-*/mishe-test-tmux*/tmux-*/default
    for tmpdir in Path("/tmp").glob("pytest-of-*"):
        for socket in tmpdir.glob("*/*/tmux-*/default"):
            if socket.exists():
                sockets.add(str(socket))
    # Any other tmux sockets directly under /tmp
    for socket in Path("/tmp").glob("tmux-*/default"):
        if socket.exists():
            sockets.add(str(socket))
    return sorted(sockets)


def sweep_orphan_test_sessions() -> list[str]:
    """Kill mishe-tauftauf-test-* sessions whose owning pid is dead.

    A hard-killed pytest leaks its tmux session because the test's finally
    cleanup never runs. The session name embeds the creating pid
    (``mishe-tauftauf-test-{pid}`` plus an optional ``-<word>`` suffix); when
    that pid is gone the session is orphaned. The pane's detached children are
    reaped with it, or a leaked mind keeps running with no owner. Returns the
    names of sessions killed, each name once: a session visible on more than one
    socket is reaped once and reported once.

    Scans all tmux sockets under /tmp, including pytest TMUX_TMPDIR sockets,
    so isolated test sessions are also reapable.
    """
    killed: list[str] = []
    for socket in _all_tmux_sockets():
        for session in list_sessions(socket):
            if not session.startswith("mishe-tauftauf-test-") or session in killed:
                continue
            pid_str = session[len("mishe-tauftauf-test-"):].split("-", 1)[0]
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                descendants = _pane_descendants(session, socket=socket)
                _tmux("kill-session", "-t", session, check=False, socket=socket)
                for child in descendants:
                    try:
                        os.kill(child, signal.SIGKILL)
                    except OSError:
                        pass
                killed.append(session)
            except PermissionError:
                pass
    return killed


def capture_raw(session: str, slug: str) -> str:
    result = _tmux("capture-pane", "-p", "-J", "-t", f"{session}:{slug}.0", "-S", "-", check=False)
    if result.returncode:
        return f"UNKNOWN — top-pain {slug} pane missing\n"
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"UNKNOWN — top-pain {slug} pane invalid UTF-8: {exc}\n"
    if not text.strip():
        return f"UNKNOWN — top-pain {slug} pane empty\n"
    lines = text.splitlines()
    leases = [index for index, line in enumerate(lines) if FOOTER_LEASE_RE.fullmatch(line)]
    if leases:
        end = leases[-1] + 1
        start = leases[-2] + 1 if len(leases) > 1 else 0
        lines = lines[start:end]
        text = "\n".join(lines) + "\n"
    return text


def capture_top(home: Path, session: str, slug: str) -> str:
    return strip_owned_chrome(capture_raw(session, slug))


def lease_value(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if FOOTER_LEASE_RE.fullmatch(line):
            return line
    return None


def _pane_stopped_or_dead(session: str, slug: str) -> bool:
    result = _tmux(
        "display-message", "-p", "-t", f"{session}:{slug}.0",
        "#{pane_dead} #{pane_pid}", check=False,
    )
    if result.returncode:
        return True
    fields = result.stdout.decode("ascii", "replace").split()
    if not fields or fields[0] == "1":
        return True
    if len(fields) > 1:
        try:
            status = Path(f"/proc/{int(fields[1])}/status").read_text(encoding="ascii")
        except (OSError, ValueError):
            return False
        for line in status.splitlines():
            if line.startswith("State:") and "\tT " in line:
                return True
    return False


def check_pane(home: Path, session: str, slug: str, wait: float = 11.0) -> tuple[bool, str]:
    if _pane_stopped_or_dead(session, slug):
        return False, f"HOLD pane-frozen: {slug} renderer process is stopped or dead"
    first = capture_raw(session, slug)
    # capture_raw returns sentinel strings when it cannot read the pane; match
    # them exactly, because a live pane's own text may quote the same words.
    if first == f"UNKNOWN — top-pain {slug} pane missing\n":
        return False, f"HOLD pane-missing: {slug}"
    # A live renderer can have a process before it prints its first frame. Treat
    # the empty sentinel like the lease path below: observe two bounded windows
    # before declaring a fault.
    for _ in range(2):
        if first != f"UNKNOWN — top-pain {slug} pane empty\n":
            break
        time.sleep(wait)
        first = capture_raw(session, slug)
    if first == f"UNKNOWN — top-pain {slug} pane empty\n":
        return False, f"HOLD pane-empty: {slug}"
    if first.startswith("UNKNOWN — top-pain"):
        return False, f"HOLD pane-fallback: {slug}"
    lease1 = lease_value(first)
    if lease1 is None:
        return False, f"HOLD pane-fallback: {slug} has no owned lease"
    # A loaded host can starve a healthy renderer past a single window; only two
    # consecutive windows with no advance mean the pane is actually frozen.
    for _ in range(2):
        time.sleep(wait)
        lease2 = lease_value(capture_raw(session, slug))
        if lease2 is not None and lease2 != lease1:
            return True, f"PASS pane-live: {slug}"
    return False, f"HOLD pane-frozen: {slug} lease did not advance"

def check_mind_pane(session: str, slug: str) -> tuple[bool, str]:
    """Verify a chartered mind's resident pane, which the renderer lease cannot see.

    `check_pane` reads `{session}:{slug}.0` only, so a mind pane that died
    (`remain-on-exit` keeps it visible at status 127) still reports a live
    renderer. The linked-site verifier checks `.1`; the local plant must too.
    """
    result = _tmux("display-message", "-p", "-t", f"{session}:{slug}.1", "#{pane_dead}", check=False)
    if result.returncode:
        return False, f"HOLD mind-pane-missing: {slug}"
    if result.stdout.decode("ascii", "replace").strip() != "0":
        return False, f"HOLD mind-pane-dead: {slug}"
    return True, f"PASS mind-pane-live: {slug}"
def repair_top(home: Path, session: str, slug: str) -> tuple[bool, str]:
    if not owns_session(home, session):
        return False, f"UNKNOWN pane command ownership for {session}:{slug}.0"
    target = f"{session}:{slug}.0"
    command_result = _tmux(
        "display-message", "-p", "-t", target, "#{pane_start_command}", check=False,
    )
    start_command = command_result.stdout.decode("utf-8", "replace").strip()
    if command_result.returncode or "mishe_tauftauf" not in start_command.replace("-", "_") or "pain watch" not in start_command:
        return False, f"UNKNOWN pane command for {target}: {start_command or 'unreadable'}"
    key = (session, slug)
    current = time.monotonic()
    previous = _LAST_REPAIR.get(key)
    if previous is not None and current - previous < 60.0:
        return False, f"HOLD pane-frozen recurrence within {current - previous:.1f}s for {slug}"
    _LAST_REPAIR[key] = current
    command = _python_command(home, "--home", str(home), "pain", "watch", slug, "--interval", "5")
    _tmux("respawn-pane", "-k", "-t", target, *command)
    return True, f"pane {slug} respawned once"




def launch_mind(home: Path, session: str, slug: str, sequence: int, attempt: int, invocation: str, context: str) -> None:
    context_path = home / "minds" / f".{invocation}.context"
    context_path.write_text(context, encoding="utf-8")
    target = f"{session}:{slug}.1"
    command = _python_command(home, "--home", str(home), "tmux-mind-run", slug,
                              str(sequence), str(attempt), invocation, str(context_path), "--session", session)
    _tmux("respawn-pane", "-k", "-t", target, *command)
