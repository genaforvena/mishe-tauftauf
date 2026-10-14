"""Decode this host's BLE/Wi-Fi adapter state for the space-perception sense.

External dialects, all read-only and bounded:

* the kernel sysfs ABI, one newline-terminated value per file under
  ``/sys/class/bluetooth``, ``/sys/class/rfkill`` and ``/sys/class/net``;
* the human-readable ``bluetoothctl show`` report;
* the camera reflex's own class word and transition log, and the kernel's UVC
  messages for the webcam (``~/.mesh/.cam-watch.state``,
  ``~/.mesh/cam-events.log``, journald), which carry the same read-only,
  external-text character.

The decoder returns ordinary Python values and never mutates a radio. A missing
adapter, an unreadable file or a command that does not return inside the bound is
reported as an explicit error — ``None`` for a value that could not be read, a
named error for a failed command — so the consumer keeps the reading UNKNOWN
rather than letting absence read as calm. ``rfkill_unreadable`` exposes the
switches ``rfkill_switches`` omits, so a short list is not read as "no switch".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
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

def ethernet_interfaces(root: Path = NET_ROOT) -> list[str]:
    """Names of wired physical netdevs: not wireless, not loopback, not virtual.

    A physical Ethernet NIC has a ``device`` symlink in sysfs pointing at its
    PCI/USB bus device; virtual interfaces (tailscale, docker, bridges) have
    no such link. Wireless devices carry a ``wireless`` directory. Loopback
    is named ``lo``. The remainder is wired physical Ethernet.
    """
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    names = []
    for entry in entries:
        if entry.name == "lo" or (entry / "wireless").is_dir():
            continue
        if (entry / "device").exists():
            names.append(entry.name)
    return names


def link_speed(name: str, root: Path = NET_ROOT) -> int | None:
    """The netdev's negotiated link rate in Mb/s, or ``None`` when unreadable.

    ``speed`` is a signed integer in Mb/s; -1 means the link is down or the
    value is unknown. A negative value other than -1 is treated as unreadable.
    """
    raw = _text(root / name / "speed")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def carrier_state(name: str, root: Path = NET_ROOT) -> int | None:
    """The netdev's carrier state (1 = link up, 0 = link down), or ``None``."""
    raw = _text(root / name / "carrier")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value




def operstate(name: str, root: Path = NET_ROOT) -> str | None:
    """The netdev's ``operstate`` value, or ``None`` when it is unreadable."""
    return _text(root / name / "operstate")


IFF_UP = 0x1
"""The administrative-up bit of ``/sys/class/net/<name>/flags``."""


def admin_up(name: str, root: Path = NET_ROOT) -> bool | None:
    """Whether the netdev is administratively up, or ``None`` when it will not read.

    ``operstate`` follows carrier, not the administrative flag: a radio that is
    administratively up but has no association also reads ``down``, so the
    ``flags`` bit is the only place the two states separate (discover, wake
    4454). An unreadable or undecodable value reads as ``None``, which is "the
    admin state did not read" and must not be reported as down.
    """
    value = _text(root / name / "flags")
    if value is None:
        return None
    try:
        return bool(int(value, 16) & IFF_UP)
    except ValueError:
        return None


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


# The camera's fault surface is host hardware state of the same read-only kind:
# the class word the motion daemon writes, its transition log, and the kernel's
# UVC messages. The producer's stats after the first separator and a truncated
# tail are opaque: a row or line that does not decode is skipped, never guessed.

_CAM_STATE_CLASS = re.compile(r"^\s*([A-Z][A-Z-]*)")
_CAM_EVENT_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+cam state (\S+)→(\S+)")
_URB_FAILURE = re.compile(r"uvcvideo.*Failed to resubmit video URB")
_PROBE_FAILURE = re.compile(r"uvcvideo.*Failed to set UVC probe control")


def decode_cam_state(text: str) -> str | None:
    """The class word from a ``.cam-watch.state`` line, or None when absent.

    ``SHUTTERED|diff=1.19|mean=20|span=4|sd=1.41`` decodes to ``SHUTTERED``; the
    stats past the separator belong to the producer and are not decoded here.
    A covered lens is a class, not a fault: the consumer decides.
    """
    match = _CAM_STATE_CLASS.search(text)
    return match.group(1) if match else None


def decode_cam_events(raw: bytes) -> list[tuple[float, str]]:
    """``cam-events.log`` rows as (epoch seconds, transition token).

    ``2026-10-11T02:25:24Z  cam state SHUTTERED→CAPTURE-FAIL (dev=/dev/video0)``
    decodes to ``(epoch, "CAPTURE-FAIL")``, the class the row transitions to. A
    row that does not decode — a truncated tail, a foreign dialect — is skipped
    rather than guessed, and undecodable bytes are replaced when splitting lines.
    """
    rows: list[tuple[float, str]] = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        match = _CAM_EVENT_ROW.search(line)
        if match is None:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        rows.append((stamp.replace(tzinfo=timezone.utc).timestamp(), match.group(3)))
    return rows


def decode_urb_failures(text: str) -> int:
    """Count of ``uvcvideo ... Failed to resubmit video URB`` lines in journald text.

    The kernel's own wording is the dialect; lines that do not carry both parts
    are not failures of this class.
    """
    return sum(1 for line in text.split("\n") if _URB_FAILURE.search(line))


def decode_probe_failures(text: str) -> int:
    """Count of ``uvcvideo ... Failed to set UVC probe control`` lines.

    The kernel's own wording is the dialect; unrelated messages are skipped.
    """
    return sum(1 for line in text.split("\n") if _PROBE_FAILURE.search(line))


def decode_cam_manifest(text: str) -> bool | None:
    """Whether a ``health/camera.json`` text declares this node's camera.

    ``{"version": 1}`` decodes True. Text that decodes as JSON with another
    version, or does not decode at all, returns None: a present manifest that is
    not a usable declaration is monitoring drift, which the caller must surface
    as UNKNOWN rather than read as absence. A missing file is the caller's I/O
    failure, not something to guess at here.
    """
    try:
        manifest = json.loads(text)
    except ValueError:
        return None
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        return None
    return True
