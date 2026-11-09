"""The Rust network probe must produce the retired Python probe's rows.

The Python implementation of this responsibility is retired (see
rust/mishe-network-probe/CONTRACT.md). The answers it gave are recorded here
as the golden reference: the row shape, the field set, the exit statuses and
the parse decisions. The binary is checked against those, not against a
mock: a fake network would prove nothing.

Skipped with a reason when no binary resolves (MISHE_NETWORK_PROBE_BIN, then
the site install), because the site install does not exist yet.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def binary():
    """The probe binary, or None when none resolves."""
    override = os.environ.get("MISHE_NETWORK_PROBE_BIN")
    if override:
        return Path(override) if os.access(override, os.X_OK) else None
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        return None
    candidate = Path(home) / "bin" / "mishe-network-probe"
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


BIN = binary()


def run(home, timeout=90):
    """Run the probe into a scratch home; return (exit code, last row or None)."""
    done = subprocess.run([str(BIN), "--home", str(home)], capture_output=True, text=True,
                          timeout=timeout)
    path = Path(home) / "network" / "probe.jsonl"
    rows = []
    if path.is_file():
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return done.returncode, (rows[-1] if rows else None), done.stdout


def test_binary_resolves_or_skip():
    if BIN is None:
        import pytest
        pytest.skip("no mishe-network-probe binary resolves (MISHE_NETWORK_PROBE_BIN or site install)")
    assert BIN.is_file()


def test_row_has_the_python_shape(tmp_path):
    """One row, with exactly the fields and nesting the retired Python probe wrote."""
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    code, row, _ = run(tmp_path)
    assert code in (0, 2), f"a probe that ran must report healthy (0) or down (2), not {code}"
    assert row is not None, "the probe wrote no row"
    assert set(row) == {"at", "gateway_ip", "gateway", "internet", "dns_ms"}
    # at: UTC, the format the Python probe wrote and the view parses.
    assert len(row["at"]) == 20 and row["at"].endswith("Z") and row["at"][10] == "T"
    assert set(row["internet"]) == {"gstatic", "cloudflare"}
    for site in row["internet"].values():
        if site["ok"]:
            assert set(site) == {"ok", "connect_ms", "tls_ms", "total_ms"}
            for key in ("connect_ms", "tls_ms", "total_ms"):
                assert isinstance(site[key], float)
                assert round(site[key], 1) == site[key], "timings keep one decimal place"
        else:
            assert set(site) == {"ok", "curl_exit"}
            assert isinstance(site["curl_exit"], int)
    if row["gateway"] is not None:
        assert "loss_pct" in row["gateway"]
        assert set(row["gateway"]) <= {"loss_pct", "min_ms", "avg_ms", "max_ms", "jitter_ms"}
    assert row["dns_ms"] is None or isinstance(row["dns_ms"], float)


def test_each_run_appends_exactly_one_row(tmp_path):
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    run(tmp_path)
    run(tmp_path)
    path = tmp_path / "network" / "probe.jsonl"
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2, "each probe run appends one row and rewrites none"


def test_missing_home_is_a_usage_error(tmp_path):
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    done = subprocess.run([str(BIN)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 1
    assert "usage" in done.stderr.lower()


def test_unknown_argument_is_a_usage_error(tmp_path):
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    done = subprocess.run([str(BIN), "--home", str(tmp_path), "--nonsense"],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 1


def test_verdict_text_matches_the_python_probe(tmp_path):
    """The stdout verdict is the Python probe's text, byte for byte."""
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    code, row, out = run(tmp_path)
    gateway_down = row["gateway"] is None or row["gateway"]["loss_pct"] >= 50
    internet_down = not any(site["ok"] for site in row["internet"].values())
    if gateway_down or internet_down:
        assert code == 2
        expected = ("network down: " + ("router/LAN not answering" if gateway_down else "") +
                    (" internet unreachable (no secure site answered)" if internet_down else ""))
        assert out.strip() == expected
    else:
        assert code == 0
        assert out.strip() == ""


def test_probe_never_writes_a_quiet_network_when_it_cannot_run(tmp_path):
    """A helper that cannot run is UNKNOWN (3), never a healthy (0) or quiet answer."""
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    # An empty PATH removes ip, ping and curl: the probe cannot run at all.
    done = subprocess.run([str(BIN), "--home", str(tmp_path / "home")],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": "/nonexistent"})
    assert done.returncode == 3, f"a probe that cannot run must exit 3, not {done.returncode}"
    assert "mishe-network-probe" in done.stderr
    # A row is not written when the probe could not run: nothing was measured.
    path = tmp_path / "home" / "network" / "probe.jsonl"
    assert not path.exists() or not path.read_text().strip()


def test_row_is_appended_to_the_site_home_the_caller_names(tmp_path):
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    home = tmp_path / "elsewhere"
    run(home)
    assert (home / "network" / "probe.jsonl").is_file()
    assert not (tmp_path / "network" / "probe.jsonl").exists()


def test_json_row_is_compact_and_single_line(tmp_path):
    """The Python probe wrote compact JSON on one line; the reader parses rows per line."""
    import pytest
    if BIN is None:
        pytest.skip("no mishe-network-probe binary resolves")
    run(tmp_path)
    line = (tmp_path / "network" / "probe.jsonl").read_text().splitlines()[-1]
    assert "\n" not in line
    assert ", " not in line, "the row keeps Python's compact separators"
    assert json.loads(line)
