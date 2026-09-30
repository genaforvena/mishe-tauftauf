"""Keep generated callers on an installed clean release during ordinary replants."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path


def checked_source(source: Path, sha: str | None = None) -> tuple[Path, str]:
    source = source.resolve()
    if any(char.isspace() for char in str(source)):
        raise ValueError("runtime source path with whitespace is not supported by generated systemd units")
    try:
        head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.PIPE, timeout=10).strip()
        dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"],
                                        text=True, stderr=subprocess.PIPE, timeout=10).strip()
        root = subprocess.check_output(["git", "-C", str(source), "rev-parse", "--show-toplevel"],
                                       text=True, stderr=subprocess.PIPE, timeout=10).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"runtime source unavailable: {exc}") from exc
    if root != str(source) or dirty or (sha is not None and head != sha):
        raise ValueError("runtime source changed or is not a clean exact-SHA worktree")
    if not (source / "src/mishe_tauftauf/__init__.py").is_file():
        raise ValueError("runtime source package is missing")
    return source, head


def source_for(home: Path, default: Path) -> Path:
    pin = home / "health/runtime-release.json"
    if not pin.exists():
        return default
    try:
        data = json.loads(pin.read_text())
        if data["version"] != 1 or not isinstance(data["sha"], str) or len(data["sha"]) != 40:
            raise ValueError("invalid runtime pin")
        return checked_source(Path(data["source"]), data["sha"])[0]
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise ValueError(f"runtime pin invalid: {exc}") from exc


def package_for(home: Path | None, default: Path) -> Path:
    """Retain installed-package layout when no repository release pin exists."""
    if home is None or not (home / "health/runtime-release.json").exists():
        return default
    return source_for(home, default.parent) / "src"


def select_source(home: Path, source: Path, session: str) -> None:
    source, sha = checked_source(source)
    path = home / "health/runtime-release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "source": str(source), "sha": sha, "session": session}) + "\n")
    temporary.replace(path)


def refresh_cli(home: Path, source: Path) -> None:
    cli = home / "bin/mishe-tauftauf"
    if not cli.exists():
        return
    current = cli.read_text()
    lines = current.splitlines()
    if (len(lines) != 3 or lines[0] != "#!/bin/sh" or not lines[1].startswith("export PYTHONPATH=")
            or not lines[2].startswith("exec ") or not lines[2].endswith(' -m mishe_tauftauf "$@"')):
        raise ValueError("custom runtime CLI must be reconciled before source refresh")
    lines[1] = f"export PYTHONPATH={shlex.quote(str(source / 'src'))}${{PYTHONPATH:+:$PYTHONPATH}}"
    updated = "\n".join(lines) + "\n"
    if updated != current:
        temporary = cli.with_suffix(".source.tmp")
        temporary.write_text(updated)
        temporary.chmod(cli.stat().st_mode)
        temporary.replace(cli)
