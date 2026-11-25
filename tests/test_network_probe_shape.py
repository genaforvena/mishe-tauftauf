"""The network-probe shape detector in checks/network-care.py: the newest probe row must be fresh and carry the host block."""
import importlib.util
import json
import tempfile
import unittest
from calendar import timegm
from pathlib import Path
from time import strptime

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_care_probe_shape", ROOT / "checks" / "network-care.py")
care = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(care)

AT = "2026-11-25T20:50:00Z"
NOW = float(timegm(strptime(AT, "%Y-%m-%dT%H:%M:%SZ")))
HOST = {"cpu_some10": 11.3, "io_some10": 7.6}


def row(at=AT, host=HOST):
    r = {"at": at, "gateway": {"loss_pct": 0.0}}
    if host is not None:
        r["host"] = host
    return r


class ProbeStateTests(unittest.TestCase):
    def test_fresh_row_with_host_is_green(self):
        self.assertEqual(care.probe_state(row(), NOW + 60)[0], 0)

    def test_fresh_row_without_host_is_red(self):
        self.assertEqual(care.probe_state(row(host=None), NOW + 60)[0], 2)

    def test_stale_row_is_unknown_even_without_host(self):
        self.assertEqual(care.probe_state(row(host=None), NOW + 3600)[0], 3)

    def test_no_row_is_unknown(self):
        self.assertEqual(care.probe_state(None, NOW)[0], 3)

    def test_unparseable_time_is_unknown(self):
        self.assertEqual(care.probe_state({"at": "yesterday"}, NOW)[0], 3)

    def test_newest_row_read_from_tail_of_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "network").mkdir()
            lines = [json.dumps(row(at="2026-11-25T20:40:00Z", host=None)), json.dumps(row())]
            (home / "network" / "probe.jsonl").write_text("\n".join(lines) + "\n")
            self.assertEqual(care.newest_probe_row(home)["at"], AT)


if __name__ == "__main__":
    unittest.main()
