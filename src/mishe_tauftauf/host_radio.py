"""Decode this host's BLE/Wi-Fi adapter state for the space-perception sense.

External dialects, all read-only and bounded:

* the kernel sysfs ABI, one newline-terminated value per file under
  ``/sys/class/bluetooth``, ``/sys/class/rfkill`` and ``/sys/class/net``;
* the human-readable ``bluetoothctl show`` report.

The decoder returns ordinary Python values and never mutates a radio. A missing
adapter, an unreadable file or a command that does not return inside the bound is
reported as an explicit error — ``None`` for a value that could not be read, a
named error for a failed command — so the consumer keeps the reading UNKNOWN
rather than letting absence read as calm. ``rfkill_unreadable`` exposes the
switches ``rfkill_switches`` omits, so a short list is not read as "no switch".
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

BLUETOOTH_ROOT = Path("/sys/class/bluetooth")
RFKILL_ROOT = Path("/sys/class/rfkill")
NET_ROOT = Path("/sys/class/net")
COMMAND_TIMEOUT_SECONDS = 10.0
"""``bluetoothctl show`` bound: the daemon answers in ~7s on this host (witness,
2026-10-10), so 5s read as a false ``bluetoothctl-timeout`` UNKNOWN."""

POWERED = re.compile(r"^[ \t]*Powered:\s+(yes|no)$", re.MULTILINE)
ACTIVE_INSTANCES = re.compile(r"^[ \t]*ActiveInstances:\s+0x[0-9a-fA-F]+\s+\((\d+)\)$",
                              re.MULTILINE)
SUPPORTED_INSTANCES = re.compile(r"^[ \t]*SupportedInstances:\s+0x[0-9a-fA-F]+\s+\((\d+)\)$",
                                 re.MULTILINE)


def _text(path: Path) -> str | None:
    """One sysfs value, or ``None`` when the file is unreadable or empty."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(256).strip()
    except OSError:
        return None
    return value or None


def bluetooth_controllers(root: Path = BLUETOOTH_ROOT) -> list[str]:
    """Names of the host's Bluetooth controllers, e.g. ``['hci0']``."""
    try:
        return sorted(entry.name for entry in root.glob("hci*"))
    except OSError:
        return []


def wireless_interfaces(root: Path = NET_ROOT) -> list[str]:
    """Names of netdevs backed by a wireless PHY (``/sys/class/net/*/wireless``)."""
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    names = []
    for entry in entries:
        try:
            if (entry / "wireless").is_dir():
                names.append(entry.name)
        except OSError:
            continue
    return names


def operstate(name: str, root: Path = NET_ROOT) -> str | None:
    """The netdev's ``operstate`` value, or ``None`` when it is unreadable."""
    return _text(root / name / "operstate")


def rfkill_switches(root: Path = RFKILL_ROOT) -> list[dict[str, str]]:
    """Every readable rfkill switch as ``{'type', 'soft', 'hard'}``.

    A switch whose files are unreadable is skipped rather than reported as an
    absent switch; ``rfkill_unreadable`` names the skipped ones, so a caller must
    not read a shorter list as "no switch exists".
    """
    try:
        slots = sorted(root.glob("rfkill*"))
    except OSError:
        return []
    switches = []
    for slot in slots:
        kind, soft, hard = _text(slot / "type"), _text(slot / "soft"), _text(slot / "hard")
        if kind is None or soft is None or hard is None:
            continue
        switches.append({"type": kind, "soft": soft, "hard": hard})
    return switches


def rfkill_unreadable(root: Path = RFKILL_ROOT) -> list[str]:
    """The ``type`` of every switch ``rfkill_switches`` had to skip.

    A slot whose ``type`` is itself unreadable is reported as ``unknown`` rather
    than guessed. Empty when every slot reads.
    """
    try:
        slots = sorted(root.glob("rfkill*"))
    except OSError:
        return []
    unreadable = []
    for slot in slots:
        kind, soft, hard = _text(slot / "type"), _text(slot / "soft"), _text(slot / "hard")
        if kind is None or soft is None or hard is None:
            unreadable.append(kind if kind is not None else "unknown")
    return unreadable


def rfkill_verdict(switches: list[dict[str, str]], kind: str) -> str | None:
    """``unblocked``/``blocked`` for one radio kind; ``None`` when it has no switch."""
    matched = [switch for switch in switches if switch["type"] == kind]
    if not matched:
        return None
    blocked = any(switch["soft"] != "0" or switch["hard"] != "0" for switch in matched)
    return "blocked" if blocked else "unblocked"


def controller_state(command: str = "bluetoothctl",
                     timeout: float = COMMAND_TIMEOUT_SECONDS) -> dict[str, object]:
    """Decode ``bluetoothctl show`` into powered and advertising values.

    ``btmgmt info`` did not return within 300 s on this host (discover,
    2026-10-10), so the powered and advertising state is read through
    ``bluetoothctl show`` under a timeout. The daemon answers in ~7s (witness,
    2026-10-10), so the bound is 10s: a 5s timeout read as a false
    ``bluetoothctl-timeout`` UNKNOWN. A timeout, a non-zero exit or output
    that does not carry the powered field yields ``error`` and no values: a hang
    is not evidence that the controller is absent.
    """
    state: dict[str, object] = {"powered": None, "advertising": None, "error": None}
    if shutil.which(command) is None:
        state["error"] = "bluetoothctl-absent"
        return state
    try:
        completed = subprocess.run([command, "show"], capture_output=True, text=True,
                                   timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        state["error"] = "bluetoothctl-timeout"
        return state
    except OSError:
        state["error"] = "bluetoothctl-unreadable"
        return state
    output = completed.stdout or ""
    if "No default controller available" in output:
        state["error"] = "no-default-controller"
        return state
    powered = POWERED.search(output)
    if completed.returncode != 0 or powered is None:
        state["error"] = "bluetoothctl-output-unparsed"
        return state
    state["powered"] = powered.group(1) == "yes"
    active = ACTIVE_INSTANCES.search(output)
    supported = SUPPORTED_INSTANCES.search(output)
    state["advertising"] = {"active": int(active.group(1)) if active else None,
                            "supported": int(supported.group(1)) if supported else None}
    return state
