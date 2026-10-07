"""Human operator dashboard and its owned tmux presentation boundary."""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .observations import validate_slug
from .runtime_source import package_for
from .tmux import _tmux, owns_session


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


def ensure(home: Path, session: str, window: str, *, package_root: Path | None = None) -> None:
    """Install a top dashboard, preserving the existing operator shell by pane ID."""
    home = home.resolve()
    validate_slug(window)
    if not owns_session(home, session):
        raise ValueError(f"session {session!r} is not owned by {home}")
    target = f"{session}:{window}"
    panes = _tmux("list-panes", "-t", target, "-F", "#{pane_id}").stdout.decode().splitlines()
    dashboard = None
    if len(panes) == 2:
        dashboard = _tmux("show-option", "-wqv", "-t", target,
                          "@mishe-operator-dashboard").stdout.decode().strip()
        if dashboard != panes[0]:
            raise ValueError("operator top pane is not an installed dashboard; preserve existing panes")
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args(argv)
    text, ok = render(args.home)
    sys.stdout.write(text)
    if not ok:
        sys.stderr.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
