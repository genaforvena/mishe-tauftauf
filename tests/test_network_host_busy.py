"""The HOST BUSY line: a busy host next to slow pages is named as host time; a row without
the PSI field is UNKNOWN, never a calm 'not busy'."""
import importlib.util
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_view", ROOT / "tools" / "network_view.py")
view = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(view)


def probe(cpu=None, io=None, web=1800.0, host=True):
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "internet": {"cloudflare": {"ok": True, "total_ms": web}},
        "gateway": {"loss_pct": 0.0},
        "dns_ms": 5.0,
    }
    if host:
        row["host"] = {"cpu_some10": cpu, "io_some10": io}
    return row


class HostBusyLineTests(unittest.TestCase):
    def test_no_rows_is_unknown(self):
        self.assertIn("UNKNOWN", view.host_busy_line([]))

    def test_row_without_host_field_is_unknown_not_calm(self):
        line = view.host_busy_line([probe(host=False)])
        self.assertIn("UNKNOWN", line)
        self.assertNotIn("SLOW HERE", line)

    def test_null_cpu_is_unknown(self):
        self.assertIn("UNKNOWN", view.host_busy_line([probe(cpu=None, io=0.5)]))

    def test_busy_host_with_slow_pages_is_named(self):
        line = view.host_busy_line([probe(cpu=86.0, io=0.5, web=1800.0)])
        self.assertIn("CPU waiting 86%", line)
        self.assertIn("SLOW HERE", line)

    def test_busy_host_with_fast_pages_is_not_flagged(self):
        self.assertNotIn("SLOW HERE", view.host_busy_line([probe(cpu=86.0, io=0.5, web=300.0)]))

    def test_quiet_host_with_slow_pages_is_not_blamed_on_host(self):
        self.assertNotIn("SLOW HERE", view.host_busy_line([probe(cpu=5.0, io=0.0, web=1800.0)]))


if __name__ == "__main__":
    unittest.main()
