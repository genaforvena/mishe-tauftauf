import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "checks" / "initiative-progress.py"


def test_report_distinguishes_open_overdue_done_unknown(tmp_path):
    (tmp_path / "charters").mkdir()
    (tmp_path / "charters" / "a.md").write_text("charter")
    (tmp_path / "done").touch()
    items = [dict(id=n, owner="a", goal=n, due=due, check=check) for n, due, check in [
        ("open", 200, {"kind": "path", "path": "missing"}),
        ("late", 50, {"kind": "path", "path": "missing"}),
        ("done", 50, {"kind": "path", "path": "done"}),
        ("unknown", 50, {"kind": "signal", "name": "missing", "state": "GREEN"}),
    ]]
    (tmp_path / "initiatives.json").write_text(json.dumps({"initiatives": items}))
    run = subprocess.run([sys.executable, str(SCRIPT), "--home", str(tmp_path), "--repo", str(tmp_path), "--now", "100"], capture_output=True, text=True)
    assert run.returncode == 0
    data = json.loads(run.stdout)
    assert {r["id"]: r["status"] for r in data["items"]} == {"open": "open", "late": "overdue", "done": "check-passed", "unknown": "unknown"}
    assert data["checked_at"] == 100


def test_missing_register_is_unknown(tmp_path):
    run = subprocess.run([sys.executable, str(SCRIPT), "--home", str(tmp_path), "--repo", str(tmp_path)], capture_output=True, text=True)
    assert run.returncode != 0
    assert "UNKNOWN" in run.stderr
