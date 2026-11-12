"""Router reading: parsing of the read-only router output. A malformed reading is a ValueError, never a pass."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import network_router as nr  # noqa: E402

SAMPLE = """UPTIME 569000.12
RADIO ra0
AA:BB:CC:00:00:01  -29 dBm / unknown (SNR -29)  0 ms ago
\tRX: 39.0 MBit/s, MCS 4, 20MHz                      0 Pkts.
\tTX: 130.0 MBit/s, MCS 14, 20MHz                    0 Pkts.
\texpected throughput: unknown
AA:BB:CC:00:00:02  -9 dBm / unknown (SNR -9)  0 ms ago
\tRX: 86.0 MBit/s, VHT-MCS 8, 20MHz, VHT-NSS 1         0 Pkts.
\tTX: 100.0 MBit/s, VHT-MCS 9, 20MHz, VHT-NSS 1         0 Pkts.
RADIO rax0
78:8A:86:FC:48:16  -32 dBm / unknown (SNR -32)  0 ms ago
\tRX: 234.0 MBit/s, VHT-MCS 5, 80MHz, VHT-NSS 1         0 Pkts.
\tTX: 433.0 MBit/s, VHT-MCS 9, 80MHz, VHT-NSS 1         0 Pkts.
LEASES
1794500000 aa:bb:cc:00:00:01 192.168.8.50 iPad-Rozalia 01:aa
1794500001 aa:bb:cc:00:00:02 192.168.8.51 * 01:bb
WAN
{"up": true, "uptime": 123456, "l3_device": "eth0"}
"""


def test_clients_names_bands_and_rates():
    body = nr.parse(SAMPLE)
    assert body["uptime_s"] == 569000
    assert body["wan"] == {"up": True, "uptime_s": 123456}
    c = body["clients"]
    assert len(c) == 3
    assert c[0]["name"] == "iPad-Rozalia" and c[0]["band"] == "2.4" and c[0]["dbm"] == -29
    assert c[0]["rx"] == 39.0 and c[0]["tx"] == 130.0
    assert c[1]["name"] == "unnamed device"  # lease hostname "*"
    assert c[2]["band"] == "5" and c[2]["name"] == "unnamed device"  # MAC not in the leases


def test_mac_is_hashed_not_shown():
    body = nr.parse(SAMPLE)
    text = json.dumps(body)
    assert "AA:BB" not in text and "78:8A" not in text
    assert all(len(x["id"]) == 8 for x in body["clients"])


def test_unreadable_output_is_an_error():
    try:
        nr.parse("garbage")
    except ValueError:
        return
    raise AssertionError("no uptime line must be a ValueError")


def test_failed_run_is_a_row_not_a_pass():
    row = nr._row(1.0, error="ssh exit 255: no route")
    assert row["ok"] is False and "error" in row and "clients" not in row


def test_trim_keeps_two_days():
    now = 1_000_000.0
    rows = [{"ts": now - 3 * 86400}, {"ts": now - 60}]
    assert nr.trim(rows, now) == [{"ts": now - 60}]
