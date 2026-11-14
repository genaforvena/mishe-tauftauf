"""Boundary adapter: decode the unit PSI sampler's JSONL bytes and systemd show text.

The reader (unit_psi_reading) owns the file I/O and the validity rules; this
module owns the text decoding only, so parsing stays inside an approved
boundary (see parsing-policy.json).
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path


class SystemdShowUnknown(RuntimeError):
    """mishe-systemd-show is missing, failed, or answered outside its contract.

    UNKNOWN, never a quiet empty dict: the unit reader turns it into an
    unreadable sampler state.
    """


def _systemd_show_binary() -> str:
    """Resolve mishe-systemd-show. Fails closed; there is no Python fallback.

    ``MISHE_SYSTEMD_SHOW_BIN`` is an explicit override (tests, scratch builds).
    Otherwise the site install ``$SITE/bin/mishe-systemd-show`` must match the
    sha256 recorded beside it in ``mishe-systemd-show.install.json``.
    """
    override = os.environ.get("MISHE_SYSTEMD_SHOW_BIN")
    if override:
        if not os.path.isfile(override) or not os.access(override, os.X_OK):
            raise SystemdShowUnknown("MISHE_SYSTEMD_SHOW_BIN names no executable file")
        return override
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        raise SystemdShowUnknown("mishe-systemd-show unavailable: no site home and no MISHE_SYSTEMD_SHOW_BIN")
    binary = Path(home) / "bin" / "mishe-systemd-show"
    try:
        record = json.loads((binary.parent / "mishe-systemd-show.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise SystemdShowUnknown(f"mishe-systemd-show install missing or unreadable: {exc}") from exc
    if not os.access(binary, os.X_OK) or record.get("sha256") != digest:
        raise SystemdShowUnknown("mishe-systemd-show install is not executable or does not match its recorded sha256")
    return str(binary)


def parse_systemd_show(raw: str) -> dict[str, str]:
    """Decode ``systemctl show`` text into properties via mishe-systemd-show.

    The binary (rust/mishe-systemd-show/CONTRACT.md) emits one ``KEY=VALUE`` line
    per distinct key, last value wins, in first-seen order. Exit 0 is the only
    success; anything else raises SystemdShowUnknown.
    """
    binary = _systemd_show_binary()
    try:
        result = subprocess.run(
            [binary], input=raw.encode("utf-8"), capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise SystemdShowUnknown(f"mishe-systemd-show could not run: {exc}") from exc
    if result.returncode != 0:
        raise SystemdShowUnknown(f"mishe-systemd-show exit {result.returncode}")
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemdShowUnknown("mishe-systemd-show emitted non-UTF-8 output") from exc
    props: dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not key:
            raise SystemdShowUnknown("mishe-systemd-show emitted a line outside its contract")
        props[key] = value
    return props


def parse_jsonl_records(raw: bytes) -> tuple[list[dict], int]:
    """Parse sampler lines into records that carry a numeric 'at' and a dict 'cgroups'.

    Returns (records, malformed). Blank lines are ignored; anything else that is
    not such a record is counted as malformed, never silently dropped.
    """
    records: list[dict] = []
    malformed = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            malformed += 1
            continue
        if isinstance(record, dict) and isinstance(record.get("at"), (int, float)) \
                and isinstance(record.get("cgroups"), dict):
            records.append(record)
        else:
            malformed += 1
    return records, malformed
