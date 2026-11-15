"""Only shell text emitted by committed-release promotion."""
import json
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


def newest_sample_at(path, tail_lines=20):
    """Newest `at` epoch among the last few JSONL samples, or None when there is no readable sample."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()[-tail_lines:]
    except (OSError, UnicodeDecodeError):
        return None
    stamps = []
    for line in lines:
        try:
            stamps.append(float(json.loads(line)["at"]))
        except (ValueError, KeyError, TypeError):
            continue
    return max(stamps) if stamps else None


def failing_line(output, limit=240):
    """The last diagnostic line of a promotion check's captured output, or "" when there is none.

    A captured check's stdout/stderr is external text, decoded here so the runtime acts on the
    chosen line. ``checks/pin-delivery.py`` selects the same line for genome's ``pin-delivery``
    signal, so the published outcome detail names the same reason for the same failure.
    """
    if not isinstance(output, str):
        return ""
    lines = [line for line in output.splitlines() if line.startswith("FAILED") or "Error" in line]
    chosen = lines[-1] if lines else (output.splitlines()[-1] if output.strip() else "")
    return chosen[:limit]


def unit_fields(show_text):
    """Parse `systemctl --user show -p ...` key=value output into a dict."""
    return dict(row.split("=", 1) for row in show_text.splitlines() if "=" in row)


def is_transient_fragment(fragment):
    """systemd keeps transient units under the user runtime directory."""
    return fragment.startswith("/run/user/")


def trigger_unit(path):
    return path.suffix in (".timer", ".path")


def wrapper_text(source, python, home=None):
    """The POSIX operator wrapper. With ``home`` it also exports MISHE_SEED_HOME.

    Without the export a bare ``mishe`` run from any directory that lacks the
    site variables resolves a cwd-relative ``.mishe-tauftauf`` and writes nothing
    (2026-11-13: a sync from the wrong cwd silently wrote 0 units). The export is
    the absolute site, so the wrapper names its own home wherever it runs.
    """
    lines = ["#!/bin/sh", "export PYTHONPATH=" + shlex.quote(str(source/"src"))]
    if home is not None:
        lines.append("export MISHE_SEED_HOME=" + shlex.quote(str(Path(home).resolve())))
    lines.append("exec " + shlex.quote(python) + ' -m mishe_tauftauf "$@"')
    return "\n".join(lines) + "\n"


def wrapper_shape_ok(lines):
    """True when ``lines`` is a wrapper written by ``wrapper_text``: a PYTHONPATH line,
    optionally followed by MISHE_SEED_HOME export lines, then the exec line."""
    return (len(lines) in (3, 4) and lines[0] == "#!/bin/sh"
            and lines[1].startswith("export PYTHONPATH=")
            and all(line.startswith("export MISHE_SEED_HOME=") for line in lines[2:-1])
            and lines[-1].startswith("exec ")
            and lines[-1].endswith(' -m mishe_tauftauf "$@"'))


def unit_properties(text):
    """``systemctl show`` output (external text, decoded at this boundary) as {property: value}."""
    return dict(line.split("=", 1) for line in str(text).splitlines() if "=" in line)


def unit_file_pins(text):
    """PYTHONPATH assignments an installed unit file declares (``Environment=PYTHONPATH=...`` lines)."""
    return [line[len("Environment="):] for line in str(text).splitlines() if line.startswith("Environment=PYTHONPATH=")]


def loaded_pins(environment_text):
    """PYTHONPATH assignments in a loaded ``Environment`` property, as ``PYTHONPATH=value`` words."""
    return [word for word in str(environment_text).split() if word.startswith("PYTHONPATH=")]


def crate_binaries(manifest_text):
    """Binary names a Cargo.toml (external text, decoded at this boundary) builds: its [[bin]] names, else the package name."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: tomllib arrived in 3.11
        import tomli as tomllib
    manifest = tomllib.loads(manifest_text)
    named = [entry["name"] for entry in manifest.get("bin", []) if isinstance(entry, dict) and entry.get("name")]
    return named or [manifest["package"]["name"]]


def binary_env_name(binary):
    """The variable a binary is exported as: no ``mishe-`` prefix, upper case, dashes to underscores, ``_BIN`` suffix."""
    stem = binary[len("mishe-"):] if binary.startswith("mishe-") else binary
    return "MISHE_" + stem.replace("-", "_").upper() + "_BIN"
