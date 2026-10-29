from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_home(home: Path | None) -> Path:
    if home is not None:
        return Path(home).resolve()
    from .cli import default_home

    return Path(default_home()).resolve()


def _resolve_session(home: Path, session: str | None) -> str:
    if session:
        return session
    # honour MISHE_SEED_SESSION exactly like cli._default_session
    env_session = os.environ.get("MISHE_SEED_SESSION")
    if env_session:
        return env_session
    from .seed import recorded_session

    return recorded_session(home) or "mishe-tauftauf"


def _outer_socket() -> str:
    tmux_env = os.environ.get("TMUX", "")
    if "," in tmux_env:
        return tmux_env.split(",", 1)[0]
    from .tmux import _default_socket

    return _default_socket()


def _prefix(socket: str) -> str | None:
    # query prefix without raising
    if shutil.which("tmux") is None:
        return None
    try:
        result = subprocess.run(
            ["tmux", "-S", socket, "show-options", "-g", "prefix"],
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    # output like "prefix C-b" or "prefix C-a"
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return None


def attach_view(
    home: Path | str | None = None,
    session: str | None = None,
    create_view: bool = True,
) -> dict:
    """Attach to the plant's isolated tmux session, creating an outer view window when inside tmux.

    Returns a dict with keys: ok, home, session, socket, message, action.
    Never starts residents; never touches android-body.
    """
    home_path = _resolve_home(Path(home) if home is not None else None)
    session_name = _resolve_session(home_path, session)

    from .tmux import owned_socket

    socket = owned_socket(home_path, session_name)
    if socket is None:
        return {
            "ok": False,
            "home": str(home_path),
            "session": session_name,
            "socket": None,
            "message": (
                f"no owned tmux session {session_name!r} for home {home_path}. "
                f"Run `mishe seed init --home {home_path}` or `mishe plant --home {home_path}` to create it, "
                f"or check `tmux -S <socket> list-sessions` and `tmux show-option -qv -t {session_name} @mishe-tauftauf-home`."
            ),
            "action": "missing",
        }

    inside = bool(os.environ.get("TMUX"))
    if not inside:
        cmd = f"env -u TMUX TMUX_TMPDIR={Path(socket).parent.parent} tmux -S {socket} attach -t {session_name}"
        # Also canonical form
        alt = f"tmux -S {socket} attach -t {session_name}"
        return {
            "ok": True,
            "home": str(home_path),
            "session": session_name,
            "socket": socket,
            "message": cmd,
            "alt": alt,
            "action": "exec-attach",
        }

    # Inside tmux: create outer view window
    outer = _outer_socket()
    # ensure outer socket dir exists (only mkdir for view)
    try:
        Path(outer).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if not create_view:
        return {
            "ok": True,
            "home": str(home_path),
            "session": session_name,
            "socket": socket,
            "outer": outer,
            "message": f"view creation disabled (--no-create); outer={outer} isolated={socket} session={session_name}",
            "action": "no-create",
        }

    # find outer session name
    outer_session = None
    try:
        r = subprocess.run(
            ["tmux", "-S", outer, "display-message", "-p", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            outer_session = r.stdout.strip()
    except Exception:
        pass
    if not outer_session:
        # fallback: list clients
        try:
            r = subprocess.run(
                ["tmux", "-S", outer, "list-clients", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
                if lines:
                    outer_session = lines[0]
        except Exception:
            pass
    if not outer_session:
        outer_session = "plants"

    # handle prefix nesting
    outer_prefix = _prefix(outer)
    if outer_prefix == "C-a":
        # ensure outer has send-prefix so C-a reaches inner
        try:
            subprocess.run(
                ["tmux", "-S", outer, "bind-key", "C-a", "send-prefix"],
                capture_output=True,
            )
        except Exception:
            pass
    # ensure inner is C-a with send-prefix (preserve, don't change if already)
    inner_prefix = _prefix(socket)
    if inner_prefix == "C-a":
        try:
            subprocess.run(
                ["tmux", "-S", socket, "bind-key", "C-a", "send-prefix"],
                capture_output=True,
            )
        except Exception:
            pass
    elif inner_prefix is not None and inner_prefix != "C-a":
        # leave as is, but ensure send-prefix exists for outer C-a case already handled
        pass
    else:
        # if unknown, still ensure inner stays C-a? don't force; spec says inner remains C-a
        pass

    # check existing view window for this socket
    window_id = None
    # list windows on outer session
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")}
        r = subprocess.run(
            ["tmux", "-S", outer, "list-windows", "-t", outer_session, "-F", "#{window_id}|#{@mishe-view-source}"],
            capture_output=True,
            text=True,
            env=env,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if line.endswith("|" + socket):
                    window_id = line.split("|", 1)[0]
                    break
    except Exception:
        pass

    if window_id:
        # ensure linked to current outer session (preserve panes: link only if not present)
        try:
            env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")}
            r = subprocess.run(
                ["tmux", "-S", outer, "list-windows", "-t", outer_session, "-F", "#{window_id}"],
                capture_output=True,
                text=True,
                env=env,
            )
            existing = set(r.stdout.splitlines()) if r.returncode == 0 else set()
            if window_id not in existing:
                subprocess.run(
                    ["tmux", "-S", outer, "link-window", "-d", "-s", window_id, "-t", f"{outer_session}:"],
                    capture_output=True,
                    env=env,
                )
        except Exception:
            pass
        return {
            "ok": True,
            "home": str(home_path),
            "session": session_name,
            "socket": socket,
            "outer": outer,
            "window_id": window_id,
            "message": f"reusing view {window_id} for {socket} -> {outer_session}",
            "action": "reuse",
        }

    # create new window on outer
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")}
    # Check before creation to preserve panes - already checked no existing window for this socket
    try:
        r = subprocess.run(
            [
                "tmux",
                "-S",
                outer,
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{window_id}",
                "-t",
                f"{outer_session}:",
                "-n",
                "mishe",
                "tmux",
                "-S",
                socket,
                "attach",
                "-t",
                session_name,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if r.returncode == 0 and r.stdout.strip():
            window_id = r.stdout.strip().splitlines()[0].strip()
            # mark source
            subprocess.run(
                ["tmux", "-S", outer, "set-option", "-w", "-t", window_id, "@mishe-view-source", socket],
                capture_output=True,
                env=env,
            )
            # link to other attached sessions (like mishe-tmux-views does) but don't kill panes
            try:
                rc = subprocess.run(
                    ["tmux", "-S", outer, "list-clients", "-F", "#{session_name}"],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if rc.returncode == 0:
                    for target in set(l.strip() for l in rc.stdout.splitlines() if l.strip()):
                        if target == outer_session:
                            continue
                        er = subprocess.run(
                            ["tmux", "-S", outer, "list-windows", "-t", target, "-F", "#{window_id}"],
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        existing = set(er.stdout.splitlines()) if er.returncode == 0 else set()
                        if window_id not in existing:
                            subprocess.run(
                                ["tmux", "-S", outer, "link-window", "-d", "-s", window_id, "-t", f"{target}:"],
                                capture_output=True,
                                env=env,
                            )
            except Exception:
                pass
            return {
                "ok": True,
                "home": str(home_path),
                "session": session_name,
                "socket": socket,
                "outer": outer,
                "window_id": window_id,
                "message": f"created view {window_id} for {socket} -> {outer_session}",
                "action": "created",
            }
        else:
            return {
                "ok": False,
                "home": str(home_path),
                "session": session_name,
                "socket": socket,
                "outer": outer,
                "message": f"failed to create view window: {r.stderr.strip()}",
                "action": "error",
            }
    except Exception as exc:
        return {
            "ok": False,
            "home": str(home_path),
            "session": session_name,
            "socket": socket,
            "outer": outer,
            "message": str(exc),
            "action": "error",
        }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--home", type=Path, default=None)
    p.add_argument("--session", default=None)
    p.add_argument("--no-create", action="store_true")
    args = p.parse_args(argv)
    res = attach_view(home=args.home, session=args.session, create_view=not args.no_create)
    print(res.get("message", ""))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
