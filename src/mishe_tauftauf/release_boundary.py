"""Only shell text emitted by committed-release promotion."""
import shlex
from pathlib import Path


def owned_runtime_units(home, unit_dir):
    """The runtime marker plus this exact site's environment or watched paths."""
    from .units import MARKER
    texts = {}
    for path in Path(unit_dir).glob("mishe-*.*"):
        if path.suffix not in (".service", ".timer", ".path") or path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text.startswith(MARKER):
            texts[path] = text
    owned = {p for p, text in texts.items() if
             f"Environment=MISHE_SEED_HOME={Path(home).resolve()}\n" in text or
             any(line.startswith(("PathChanged=", "PathModified=")) and
                 Path(line.split("=", 1)[1]).is_relative_to(Path(home).resolve()) for line in text.splitlines())}
    owned.update(p for p in texts if p.suffix == ".timer" and p.with_suffix(".service") in owned)
    return sorted(owned)


def unit_fields(show_text):
    """Parse `systemctl --user show -p ...` key=value output into a dict."""
    return dict(row.split("=", 1) for row in show_text.splitlines() if "=" in row)


def is_transient_fragment(fragment):
    """systemd keeps transient units under the user runtime directory."""
    return fragment.startswith("/run/user/")


def trigger_unit(path):
    return path.suffix in (".timer", ".path")


def wrapper_text(source, python):
    return "#!/bin/sh\nexport PYTHONPATH=" + shlex.quote(str(source/"src")) + "\nexec " + shlex.quote(python) + ' -m mishe_tauftauf "$@"\n'
