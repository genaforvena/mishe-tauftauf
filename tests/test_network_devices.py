"""The per-device ping reading: a failed ping is never a pass, and the detector is UNKNOWN when silent.

Ping output is decoded from a fixture string; the detector judges rows from network/devices.jsonl.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
devices = load(REPO / "tools" / "network_devices.py", "network_devices_under_test")
care = load(REPO / "checks" / "network-care.py", "network_care_devices_under_test")

REPLY = """PING 192.168.8.214 (192.168.8.214) 56(84) bytes of data.

--- 192.168.8.214 ping statistics ---
5 packets transmitted, 5 received, 0% packet loss, time 4006ms
rtt min/avg/max/mdev = 0.887/4.812/19.955/7.1 ms
"""
SILENT = """--- 192.168.8.214 ping statistics ---
5 packets transmitted, 0 received, 100% packet loss, time 4090ms
"""
NOW = 1_795_273_200.0


class DecodePing(unittest.TestCase):
    def test_reply_is_ok_with_figures(self):
        res = devices.nb.decode_ping(REPLY, 0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["avg_ms"], 4.812)
        self.assertEqual(res["max_ms"], 19.955)
        self.assertEqual(res["loss_pct"], 0.0)

    def test_total_loss_is_not_ok(self):
        res = devices.nb.decode_ping(SILENT, 1)
        self.assertFalse(res["ok"])

    def test_unreadable_output_is_not_a_pass(self):
        res = devices.nb.decode_ping("", 2)
        self.assertFalse(res["ok"])


def row(ts, **devs):
    return {"ts": ts, "devices": devs}


class DeviceDetector(unittest.TestCase):
    def test_all_answering_is_green(self):
        rows = [row(NOW - 60, **{"iMac-Rozalia": {"ok": True, "avg_ms": 4.8, "loss_pct": 0.0}})]
        code, msg = care.devices_state(rows, NOW)
        self.assertEqual(code, 0)
        self.assertIn("iMac-Rozalia", msg)

    def test_silent_device_is_unknown_not_green(self):
        rows = [row(NOW - 60, **{"iMac-Rozalia": {"ok": False, "why": "no reply"}})]
        code, msg = care.devices_state(rows, NOW)
        self.assertEqual(code, 3)
        self.assertIn("iMac-Rozalia", msg)

    def test_no_rows_is_unknown(self):
        self.assertEqual(care.devices_state([], NOW)[0], 3)

    def test_stale_row_is_unknown(self):
        rows = [row(NOW - 1200, **{"iMac-Rozalia": {"ok": True, "avg_ms": 1.0, "loss_pct": 0.0}})]
        code, msg = care.devices_state(rows, NOW)
        self.assertEqual(code, 3)
        self.assertIn("min old", msg)

    def test_slow_device_is_green_not_red(self):
        rows = [row(NOW - 30, **{"iMac-Rozalia": {"ok": True, "avg_ms": 300.0, "loss_pct": 0.0}})]
        self.assertEqual(care.devices_state(rows, NOW)[0], 0)


if __name__ == "__main__":
    unittest.main()


class NeighbourStateTest(unittest.TestCase):
    """The IPv4 neighbour entry tells 'left the Wi-Fi' (FAILED) from 'associated, silent to ping'."""

    class _Out:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def test_failed_neighbour_is_reported(self):
        run = lambda *a, **k: self._Out("192.168.8.214 dev enp42s0 FAILED \n")
        self.assertEqual(devices.neighbour_state("192.168.8.214", run), "FAILED")

    def test_empty_table_is_unknown_not_failed(self):
        run = lambda *a, **k: self._Out("")
        self.assertEqual(devices.neighbour_state("192.168.8.214", run), "UNKNOWN")

    def test_unreadable_table_is_unknown(self):
        run = lambda *a, **k: self._Out("", 1)
        self.assertEqual(devices.neighbour_state("192.168.8.214", run), "UNKNOWN")

    def test_reachable_neighbour(self):
        run = lambda *a, **k: self._Out("192.168.8.214 dev enp42s0 lladdr 7c:c3:a1:a5:20:d7 REACHABLE\n")
        self.assertEqual(devices.neighbour_state("192.168.8.214", run), "REACHABLE")
