import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from mishe_tauftauf.unit_psi_reading import attribute  # noqa: E402


def _unit(full60, high, mx, swap=None, swap_max=None):
    cg = {"psi": {"full": {"avg60": full60}, "some": {"avg60": full60 / 2}},
          "events": {"high": high, "max": mx}}
    if swap is not None:
        cg["swap"] = swap
    if swap_max is not None:
        cg["swap_max"] = swap_max
    return cg


def _rec(at, units):
    return {"at": at, "cgroups": units}


def test_empty_is_unknown_not_calm():
    assert attribute([])["state"] == "unknown"


def test_no_cgroups_is_unknown():
    assert attribute([_rec(1.0, {})])["state"] == "unknown"


def test_top_three_by_full_avg60_and_missing_psi_excluded():
    units = {
        "a.service": _unit(1.0, 0, 0),
        "b.service": _unit(9.0, 0, 0),
        "c.service": _unit(5.0, 0, 0),
        "d.service": _unit(7.0, 0, 0),
        "e.service": {"events": {"high": 0}},  # no psi: not ranked, not zero
    }
    out = attribute([_rec(100.0, units)])
    assert out["state"] == "verified"
    assert out["units_with_full_avg60"] == 4
    assert [r["unit"] for r in out["top_full_avg60"]] == ["b.service", "d.service", "c.service"]


def test_high_and_max_deltas_need_an_anchor_in_window():
    old = _rec(0.0, {"x.service": _unit(0.0, 10, 1)})
    new = _rec(200.0, {"x.service": _unit(0.0, 14, 3)})
    out = attribute([old, new], window_s=300)
    assert out["span_s"] == 200.0
    row = out["high_or_max_moved"][0]
    assert (row["high_delta"], row["max_delta"]) == (4, 2)


def test_no_anchor_reports_unknown_delta_not_zero():
    only = _rec(200.0, {"x.service": _unit(0.0, 14, 3)})
    out = attribute([only])
    assert out["span_s"] is None
    assert out["high_or_max_moved"] == []
    assert out["top_full_avg60"][0]["high_delta"] is None
    # a record outside the window is also no anchor
    far = [_rec(0.0, {"x.service": _unit(0.0, 1, 1)}), _rec(1000.0, {"x.service": _unit(0.0, 2, 2)})]
    assert attribute(far, window_s=300)["span_s"] is None


def test_swap_against_swap_max_and_uncapped():
    units = {
        "capped.service": _unit(0.0, 0, 0, swap=50, swap_max=200),
        "uncapped.service": _unit(0.0, 0, 0, swap=10, swap_max="max"),
        "none.service": _unit(0.0, 0, 0),
    }
    out = attribute([_rec(1.0, units)])
    by = {r["unit"]: r for r in out["swap_units"]}
    assert by["capped.service"]["swap_of_max"] == "25.0%"
    assert by["uncapped.service"]["swap_of_max"] == "uncapped"
    assert "none.service" not in by


def test_cli_reads_fixture_file(tmp_path, capsys):
    from unit_psi_attribution import _cli
    path = tmp_path / "u.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        _rec(0.0, {"x.service": _unit(1.0, 1, 0)}),
        _rec(60.0, {"x.service": _unit(2.0, 3, 0)}),
    ]) + "\nnot json\n")
    assert _cli([str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["malformed"] == 1
    assert out["top_full_avg60"][0]["high_delta"] == 2


def test_zero_swap_cap_is_unknown_not_uncapped():
    from mishe_tauftauf.unit_psi_reading import swap_of_max
    assert swap_of_max({"swap": 5, "swap_max": 0}) == (5.0, "unknown")
    assert swap_of_max({"swap": 5, "swap_max": "max"}) == (5.0, "uncapped")
    assert swap_of_max({"swap": 5, "swap_max": 10}) == (5.0, "50.0%")


def test_cli_tail_miss_keeps_anchor_unknown_not_zero(tmp_path, capsys):
    from unit_psi_attribution import _cli
    from mishe_tauftauf.unit_psi_reading import TAIL_BYTES
    path = tmp_path / "big.jsonl"
    pad = "x" * 4096
    # every record that fits in the tail is older than the 300 s window of the newest
    filler = [{"at": 1000.0 + i, "cgroups": {"x.service": _unit(0.0, 0, 0), "pad": pad}}
              for i in range(0, 200)]
    newest = _rec(2000.0, {"x.service": _unit(0.0, 9, 0)})
    path.write_text("\n".join(json.dumps(r) for r in filler + [newest]) + "\n")
    assert path.stat().st_size > TAIL_BYTES
    assert _cli([str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    # the 300 s anchor is far outside the 256 KiB tail: no delta is invented
    assert out["span_s"] is None
    assert out["top_full_avg60"][0]["high_delta"] is None
