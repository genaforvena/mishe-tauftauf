from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mishe_tauftauf import discovery, host_radio


SHOW = (
    "Controller 00:13:25:AC:CD:1B (public)\n"
    "\tManufacturer: 0x005d (93)\n"
    "\tName: mesh-home\n"
    "\tPowered: yes\n"
    "\tDiscovering: no\n"
    "Advertising Features:\n"
    "\tActiveInstances: 0x00 (0)\n"
    "\tSupportedInstances: 0x05 (5)\n"
)


def _sysfs(root: Path, name: str, value: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_sysfs_decode_reads_presence_rfkill_and_operstate(tmp_path: Path) -> None:
    bluetooth, rfkill, net = tmp_path / "bluetooth", tmp_path / "rfkill", tmp_path / "net"
    (bluetooth / "hci0").mkdir(parents=True)
    _sysfs(rfkill, "rfkill0/type", "bluetooth\n")
    _sysfs(rfkill, "rfkill0/soft", "0\n")
    _sysfs(rfkill, "rfkill0/hard", "0\n")
    _sysfs(rfkill, "rfkill1/type", "wlan\n")
    _sysfs(rfkill, "rfkill1/soft", "1\n")
    _sysfs(rfkill, "rfkill1/hard", "0\n")
    (net / "enp42s0").mkdir(parents=True)
    (net / "wlxabc").mkdir(parents=True)
    (net / "wlxabc" / "wireless").mkdir()
    _sysfs(net, "wlxabc/operstate", "down\n")

    assert host_radio.bluetooth_controllers(bluetooth) == ["hci0"]
    # An ethernet netdev has no `wireless` child, so it is not a radio interface.
    assert host_radio.wireless_interfaces(net) == ["wlxabc"]
    assert host_radio.operstate("wlxabc", net) == "down"
    switches = host_radio.rfkill_switches(rfkill)
    assert switches == [{"type": "bluetooth", "soft": "0", "hard": "0"},
                        {"type": "wlan", "soft": "1", "hard": "0"}]
    assert host_radio.rfkill_verdict(switches, "bluetooth") == "unblocked"
    assert host_radio.rfkill_verdict(switches, "wlan") == "blocked"
    assert host_radio.rfkill_verdict(switches, "wwan") is None


def test_absent_sysfs_roots_decode_to_empty_not_to_a_verdict(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert host_radio.bluetooth_controllers(missing) == []
    assert host_radio.wireless_interfaces(missing) == []
    assert host_radio.rfkill_switches(missing) == []
    assert host_radio.operstate("hci0", missing) is None


def test_controller_state_decodes_indented_report_read_only(monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=SHOW, stderr="", returncode=0)

    monkeypatch.setattr(host_radio.shutil, "which", lambda name: "/usr/bin/bluetoothctl")
    monkeypatch.setattr(host_radio.subprocess, "run", run)

    assert host_radio.controller_state() == {
        "powered": True, "advertising": {"active": 0, "supported": 5}, "error": None}
    # The only radio call is the bounded, non-interactive read: no mutation subcommand.
    assert calls == [(["bluetoothctl", "show"],
                      {"capture_output": True, "text": True,
                       "timeout": host_radio.COMMAND_TIMEOUT_SECONDS,
                       "stdin": subprocess.DEVNULL})]


def test_controller_state_timeout_is_unknown_not_absent(monkeypatch) -> None:
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(host_radio.shutil, "which", lambda name: "/usr/bin/bluetoothctl")
    monkeypatch.setattr(host_radio.subprocess, "run", run)

    assert host_radio.controller_state() == {
        "powered": None, "advertising": None, "error": "bluetoothctl-timeout"}


def test_controller_state_missing_tool_is_reported_not_silent(monkeypatch) -> None:
    monkeypatch.setattr(host_radio.shutil, "which", lambda name: None)
    assert host_radio.controller_state() == {
        "powered": None, "advertising": None, "error": "bluetoothctl-absent"}


@pytest.mark.parametrize("stdout, returncode, error", [
    ("", 0, "bluetoothctl-output-unparsed"),
    ("No default controller available\n", 0, "no-default-controller"),
    ("\tPowered: yes\n", 1, "bluetoothctl-output-unparsed"),
])
def test_controller_state_unread_report_is_an_error(monkeypatch, stdout, returncode, error) -> None:
    monkeypatch.setattr(host_radio.shutil, "which", lambda name: "/usr/bin/bluetoothctl")
    monkeypatch.setattr(host_radio.subprocess, "run",
                        lambda command, **kwargs: SimpleNamespace(
                            stdout=stdout, stderr="", returncode=returncode))

    state = host_radio.controller_state()
    assert state == {"powered": None, "advertising": None, "error": error}


def _radio_row(monkeypatch, *, controllers, interfaces, switches, controller, operstates=None):
    monkeypatch.setattr(discovery, "bluetooth_controllers", lambda: list(controllers))
    monkeypatch.setattr(discovery, "wireless_interfaces", lambda: list(interfaces))
    monkeypatch.setattr(discovery, "rfkill_switches", lambda: list(switches))
    monkeypatch.setattr(discovery, "controller_state", lambda: dict(controller))
    monkeypatch.setattr(discovery, "operstate",
                        lambda name: (operstates or {}).get(name))
    return discovery._host_radio_read()


def test_host_radio_verified_sample_names_every_state(monkeypatch) -> None:
    row = _radio_row(
        monkeypatch, controllers=["hci0"], interfaces=["wlxabc"],
        switches=[{"type": "bluetooth", "soft": "0", "hard": "0"},
                  {"type": "wlan", "soft": "0", "hard": "0"}],
        controller={"powered": True, "advertising": {"active": 0, "supported": 5},
                    "error": None},
        operstates={"wlxabc": "down"})

    assert row["id"] == "sense.space.host-radio"
    assert row["state"] == "verified"
    assert row["sample"] == ("BLE hci0 powered=yes adv=0/5; wifi wlxabc=down; "
                            "rfkill bt=unblocked wlan=unblocked")
    assert row["consumer"] == "space-perception/host-half"
    assert row["controllers"] == ["hci0"] and row["wireless"] == ["wlxabc"]


def test_host_radio_missing_adapter_is_unknown_not_absent_calm(monkeypatch) -> None:
    row = _radio_row(monkeypatch, controllers=[], interfaces=["wlxabc"],
                     switches=[{"type": "wlan", "soft": "0", "hard": "0"}],
                     controller={"powered": None, "advertising": None, "error": None},
                     operstates={"wlxabc": "up"})

    assert row["state"] == "unknown"
    assert row["reason"] == "no-bluetooth-controller"
    assert row["sample"].startswith("host radio UNKNOWN no-bluetooth-controller; BLE absent")


def test_host_radio_unread_controller_is_unknown_and_names_the_error(monkeypatch) -> None:
    row = _radio_row(
        monkeypatch, controllers=["hci0"], interfaces=["wlxabc"],
        switches=[{"type": "bluetooth", "soft": "0", "hard": "0"},
                  {"type": "wlan", "soft": "0", "hard": "0"}],
        controller={"powered": None, "advertising": None, "error": "bluetoothctl-timeout"},
        operstates={"wlxabc": "down"})

    assert row["state"] == "unknown"
    assert row["reason"] == "bluetoothctl-timeout"
    assert "BLE hci0 powered=unread" in row["sample"]


def test_host_radio_missing_wireless_interface_is_unknown(monkeypatch) -> None:
    row = _radio_row(monkeypatch, controllers=["hci0"], interfaces=[],
                     switches=[{"type": "bluetooth", "soft": "0", "hard": "0"}],
                     controller={"powered": True, "advertising": None, "error": None})

    assert row["state"] == "unknown"
    assert row["reason"] == "no-wifi-interface"
    assert "wifi absent" in row["sample"]


def test_host_radio_blocked_switch_is_verified_and_names_the_block(monkeypatch) -> None:
    row = _radio_row(
        monkeypatch, controllers=["hci0"], interfaces=["wlxabc"],
        switches=[{"type": "bluetooth", "soft": "1", "hard": "0"},
                  {"type": "wlan", "soft": "0", "hard": "0"}],
        controller={"powered": True, "advertising": None, "error": None},
        operstates={"wlxabc": "down"})

    assert row["state"] == "verified"
    assert row["sample"].startswith("BLE hci0 powered=yes rfkill-blocked;")
    assert "rfkill bt=blocked wlan=unblocked" in row["sample"]


def test_scan_carries_the_host_radio_row(tmp_path: Path) -> None:
    snapshot = json.loads(discovery.scan(tmp_path).read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in snapshot["observations"]}
    assert rows["sense.space.host-radio"]["state"] in {"verified", "unknown"}
    assert rows["sense.space.host-radio"]["consumer"] == "space-perception/host-half"
    assert rows["command.bluetoothctl"]["state"] in {"available", "unavailable"}
