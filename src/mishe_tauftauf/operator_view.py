"""Human operator dashboard and its owned tmux presentation boundary."""
from __future__ import annotations

import argparse
import fcntl
import math
import shlex
import sys
import time
from pathlib import Path

from .observations import validate_slug
from .runtime_source import package_for
from .tmux import _pane_stopped_or_dead, _tmux, await_owned


def render(home: Path) -> tuple[str, bool]:
    home = home.resolve()
    parts = ["OPERATOR — current brief and commands\n"]
    ok = True
    for title, name, required in (
        ("BRIEF", "operator/brief.md", True),
        ("COMMANDS", "operator/commands.md", True),
        ("CURRENT WALL", "walls/operator.md", False),
    ):
        path = home / name
        try:
            if not path.resolve().is_relative_to(home):
                raise ValueError("input escapes owned site")
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("input is empty")
            parts.append(f"\n{title}\n{text.rstrip()}\n")
        except FileNotFoundError:
            if required:
                ok = False
                parts.append(f"\n{title}\nUNKNOWN — missing {name}\n")
        except (OSError, UnicodeError, ValueError) as exc:
            ok = False
            parts.append(f"\n{title}\nUNKNOWN — {name}: {exc}\n")
    return "".join(parts), ok


def ensure(home: Path, session: str, window: str, *, package_root: Path | None = None,
           refresh: bool = True) -> str:
    """Serialize planting and supervision of the same human window."""
    home = home.resolve()
    validate_slug(window)
    if not await_owned(home, session):
        raise ValueError(f"session {session!r} is not owned by {home}")
    lock = home / ".operator-view.lock"
    if lock.is_symlink():
        raise ValueError("operator lock is not owned")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _ensure(home, session, window, package_root=package_root, refresh=refresh)


def _renderer_dependency_error() -> Exception | None:
    """Check the import contract the renderer pane will run under.

    Renderer panes spawn with this unit's interpreter (`sys.executable`), so
    the check must mirror viewport.py's real import, not a metadata lookup.
    """
    try:
        from wcwidth import iter_graphemes, wcswidth  # noqa: F401
    except Exception as exc:  # ModuleNotFoundError is the wake-40528 observed cause
        return exc
    return None


def _ensure(home: Path, session: str, window: str, *, package_root: Path | None,
            refresh: bool) -> str:
    """Install a top dashboard, preserving the existing operator shell by pane ID."""
    home = home.resolve()
    validate_slug(window)
    if not await_owned(home, session):
        raise ValueError(f"session {session!r} is not owned by {home}")
    dependency_error = _renderer_dependency_error()
    if dependency_error is not None:
        # Renderer panes spawn with this unit's interpreter, so a missing
        # dependency dies in every respawn and the supervisor flap-loops
        # (wake 40528: ModuleNotFoundError: wcwidth at ~1/10 s). Refuse before
        # any tmux mutation with the failing import named instead.
        raise ValueError(
            f"selected runtime interpreter {sys.executable} cannot load the renderer "
            f"dependency wcwidth ({dependency_error}); panes would die at import — "
            "activate the dashboard with an interpreter that has it installed"
        ) from dependency_error
    shell_path = home / "bin" / "operator-shell"
    shell_path.parent.mkdir(parents=True, exist_ok=True)
    generated_shell = ("#!/bin/sh\n"
                       "# mishe-generated operator resume shell\n"
                       "if [ -x /home/mesh-home/.local/bin/omp ]; then\n"
                       "    /home/mesh-home/.local/bin/omp --cwd " +
                       shlex.quote(str(home.parent.resolve())) + " --continue\n"
                       "    status=$?\n"
                       "    printf 'omp --continue exited (%s); opening a shell\\n' \"$status\" >&2\n"
                       "else\n"
                       "    printf 'omp unavailable; opening a shell\\n' >&2\n"
                       "fi\n"
                       "exec \"${SHELL:-/bin/sh}\" -l\n")
    current_shell = shell_path.read_text(encoding="utf-8") if shell_path.exists() else None
    if current_shell is None or (current_shell != generated_shell
                                 and "# mishe-generated operator" in current_shell.splitlines()[1:3]):
        shell_path.write_text(generated_shell, encoding="utf-8")
        shell_path.chmod(0o755)
    names = _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines()
    trigger = ""
    if window not in names:
        _tmux("new-window", "-d", "-t", session, "-n", window, "-c", str(home.parent),
              str(home / "bin" / "operator-shell"))
        # Whole-window loss is the root cause here; keep it even though the
        # dashboard install below would otherwise report a bare restore.
        trigger = "missing-window"
    target = f"{session}:{window}"
    panes = _tmux("list-panes", "-t", target, "-F", "#{pane_id}").stdout.decode().splitlines()
    restored_shell = False
    if len(panes) == 1:
        marker = _tmux("show-option", "-wqv", "-t", target,
                       "@mishe-operator-dashboard").stdout.decode().strip()
        if marker == panes[0]:
            shell = _tmux("split-window", "-d", "-v", "-l", "50%", "-P", "-F", "#{pane_id}",
                          "-t", panes[0], "-c", str(home.parent),
                          str(home / "bin" / "operator-shell")).stdout.decode().strip()
            panes.append(shell)
            restored_shell = True
            # The dashboard survived alone; the shell underneath died.
            trigger = trigger or "missing-shell"
        elif marker:
            # The recorded dashboard pane is gone; the lone pane is the shell.
            trigger = trigger or "missing-top"
    dashboard = None
    if len(panes) == 2:
        dashboard = _tmux("show-option", "-wqv", "-t", target,
                          "@mishe-operator-dashboard").stdout.decode().strip()
        if dashboard != panes[0]:
            raise ValueError("operator top pane is not an installed dashboard; preserve existing panes")
        _tmux("set-option", "-p", "-t", dashboard, "remain-on-exit", "on")
        if not refresh:
            if _pane_stopped_or_dead(session, window):
                trigger = trigger or "pane-stopped-or-dead"
            else:
                from .dashboard import read
                try:
                    read(home, window)
                    detail = f" (trigger: {trigger})" if trigger else ""
                    return f"operator shell restored in {target}{detail}" if restored_shell else ""
                except ValueError as exc:
                    # A live pane whose publication is unreadable, missing or stale is
                    # respawned here. Naming the check that fired separates "the renderer
                    # died" from "the supervisor killed a live renderer", which the shared
                    # tape otherwise cannot tell apart.
                    trigger = trigger or f"publication: {exc}"
    elif len(panes) != 1:
        raise ValueError("operator window must contain a shell or a dashboard above a shell")
    for name in ("operator", "operator/brief.md", "operator/commands.md", "top-pains", f"top-pains/{window}"):
        path = home / name
        if path.is_symlink() or not path.resolve().is_relative_to(home):
            raise ValueError(f"operator installation path is not owned: {name}")
    root = (package_root if package_root is not None else
            package_for(home, Path(__file__).resolve().parents[1])).resolve()
    if not (root / "mishe_tauftauf/operator_view.py").is_file():
        raise ValueError("selected runtime lacks the operator renderer; activate it before installation")
    directory = home / "operator"
    directory.mkdir(exist_ok=True)
    # The human's lower pane resumes its omp thread after a tmux-server death:
    # `omp --continue` reopens the previous session for the CWD, so a fresh
    # shell starts where the conversation left off instead of empty. Only the
    # operator window uses this; resident mind panes keep their own launchers.
    # Written by ensure (not planted once) so a repaired checkout reaches the
    # running supervisor without a replant; an operator-customized shell is
    # preserved byte-for-byte.
    defaults = {
        "brief.md": "Edit this page with the current human discussion and decisions.\n",
        "commands.md": (
            f"Site: {home}\n"
            f"mishe-tauftauf --home {shlex.quote(str(home))} pain read {window} --launcher dashboard\n"
            f"mishe-tauftauf --home {shlex.quote(str(home))} wall show --owner operator\n"
            f"mishe-tauftauf --home {shlex.quote(str(home))} wall dm --source operator --to ROLE --file MESSAGE\n"
            "Edit this page with installed local capabilities and their exact commands.\n"
        ),
    }
    for name, text in defaults.items():
        path = directory / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    top = home / "top-pains" / window
    top.parent.mkdir(exist_ok=True)
    previous = (top.read_bytes(), top.stat().st_mode & 0o777) if top.exists() else None
    top.write_text("#!/bin/sh\nexport PYTHONPATH=" + shlex.quote(str(root)) +
                   "\nexec " + shlex.quote(sys.executable) +
                   " -m mishe_tauftauf.operator_view --home " + shlex.quote(str(home)) + "\n",
                   encoding="utf-8")
    top.chmod(0o755)
    command = ["env", f"MISHE_SEED_SESSION={session}", f"PYTHONPATH={root}", sys.executable,
               "-m", "mishe_tauftauf", "--home", str(home), "pain", "watch", window, "--interval", "5"]
    if len(panes) == 1:
        try:
            dashboard = _tmux("split-window", "-d", "-b", "-v", "-l", "50%", "-P", "-F", "#{pane_id}",
                              "-t", panes[0], *command).stdout.decode().strip()
            _tmux("set-option", "-w", "-t", target, "@mishe-operator-dashboard", dashboard)
            _tmux("set-option", "-p", "-t", dashboard, "remain-on-exit", "on")
        except Exception:
            try:
                if dashboard:
                    _tmux("kill-pane", "-t", dashboard)
            finally:
                if previous is None:
                    top.unlink(missing_ok=True)
                else:
                    top.write_bytes(previous[0])
                    top.chmod(previous[1])
            raise
    elif len(panes) == 2:
        _tmux("respawn-pane", "-k", "-t", dashboard, *command)
    _tmux("set-option", "-w", "-t", target, "automatic-rename", "off")
    detail = f" (trigger: {trigger})" if trigger else ""
    return f"operator dashboard restored in {target}{detail}; lower pane preserved"


def run(home: Path, session: str, window: str, interval: float) -> None:
    """Recover presentation faults, retaining each recovery on the shared tape."""
    from .wall import message
    while True:
        status = ensure(home, session, window, refresh=False)
        if status:
            print(status, flush=True)
            message(home, "operator-view", "health", status +
                    ". Resolver health: inspect recurrence and loaded consumer; "
                    "recovery is not causal resolution. Next health wake, within 120s.")
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--session")
    parser.add_argument("--window", default="operator")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args(argv)
    if args.follow:
        if not args.session or not math.isfinite(args.interval) or args.interval <= 0:
            parser.error("follow requires session and a positive finite interval")
        run(args.home, args.session, args.window, args.interval)
        return 0
    text, ok = render(args.home)
    sys.stdout.write(text)
    if not ok:
        sys.stderr.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
