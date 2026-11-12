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
LINKS
1: lo: <LOOPBACK,UP> mtu 65536 qdisc noqueue state UNKNOWN
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    RX:  bytes  packets errors dropped  missed   mcast
    1000     10      0       0       0       0
    TX:  bytes  packets errors dropped carrier collsns
    1000     10      0       0       0       0
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff
    RX:  bytes  packets errors dropped  missed   mcast
    987654321 1234567 7      42      0       9
    TX:  bytes  packets errors dropped carrier collsns
    123456789 765432  3       5       0       0
3: eth1: <BROADCAST,UP> mtu 1500 qdisc noop state DOWN
    RX:  bytes  packets errors dropped  missed   mcast
    0        0       0       0       0       0
    TX:  bytes  packets errors dropped carrier collsns
    0        0       0       0       0       0
"""


def test_clients_names_bands_and_rates():
    body = nr.parse(SAMPLE)
    assert body["uptime_s"] == 569000
    assert body["wan"] == {"up": True, "uptime_s": 123456}
    assert body["uplink"] == {"device": "eth0", "rx_errors": 7, "rx_dropped": 42,
                              "tx_errors": 3, "tx_dropped": 5}
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


def test_uplink_counters_read_the_wan_device_only():
    body = nr.parse(SAMPLE)
    assert body["uplink"]["device"] == "eth0"  # not lo, not eth1


def test_uplink_unknown_when_wan_device_has_no_block():
    text = SAMPLE.replace('"l3_device": "eth0"', '"l3_device": "wwan9"')
    assert nr.parse(text)["uplink"] is None


def test_uplink_unknown_without_links_section():
    text = SAMPLE.split("LINKS")[0]
    assert nr.parse(text)["uplink"] is None


def test_router_load_and_free_ram_are_read_from_the_same_pass():
    text = SAMPLE.replace("LINKS\n", "LOAD\n0.42 0.30 0.25 1/200 999\nMemTotal:  235000 kB\nMemAvailable:  180224 kB\nLINKS\n")
    assert nr.parse(text)["router_load"] == {"load_1m": 0.42, "mem_available_mb": 176.0}


def test_router_load_unknown_not_zero_when_missing():
    assert nr.parse(SAMPLE)["router_load"] is None
    partial = SAMPLE.replace("LINKS\n", "LOAD\nnot a load line\nLINKS\n")
    assert nr.parse(partial)["router_load"] is None
