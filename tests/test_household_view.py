"""The household window reads change from the log; pairs must be adjacent frames."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("household_view", ROOT / "tools/household_view.py")
hv = importlib.util.module_from_spec(spec)
sys.modules["household_view"] = hv
spec.loader.exec_module(hv)


def test_few_rows_pair_adjacent_frames(tmp_path, monkeypatch):
    log = tmp_path / "signatures.jsonl"
    rows = [{"room": "living room", "status": "ok", "grid": [g] * 48, "captured_at_epoch_s": i,
             "mean_luma": g} for i, g in enumerate([10, 10, 50, 50, 90])]
    log.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(hv, "LOG", log)
    cam = {"reading": None, "hist": [], "dist": []}
    hv.refresh("living room", cam)
    # adjacent differences: 0, 40, 0, 40 (not all zeros as with self-pairing)
    assert cam["dist"] == [0.0, 40.0, 0.0, 40.0]


def test_single_row_has_no_change(tmp_path, monkeypatch):
    log = tmp_path / "signatures.jsonl"
    log.write_text('{"room": "bedroom", "status": "ok", "grid": [5,5,5,5], "captured_at_epoch_s": 1}\n')
    monkeypatch.setattr(hv, "LOG", log)
    cam = {"reading": None, "hist": [], "dist": []}
    hv.refresh("bedroom", cam)
    assert cam["dist"] == []
