"""pin-integrity: the pin must equal the last verified delivery's release; unreadable is UNKNOWN."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("pin_integrity", Path(__file__).parent.parent / "checks" / "pin-integrity.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def site(tmp_path, pin, extra=()):
    (tmp_path / "patches").mkdir()
    (tmp_path / "runtime").mkdir()
    for sha, at in (("aaaaaaaa", "2026-11-06T00:00:00+00:00"), ("bbbbbbbb", "2026-11-10T00:00:00+00:00")):
        (tmp_path / "releases" / (sha + "0" * 32)).mkdir(parents=True)
        (tmp_path / "patches" / f"moving-pin-{sha}.json").write_text(
            json.dumps({"phase": "applied", "delivery_verified": True, "applied_at": at}))
    for name, rec in extra:
        (tmp_path / "patches" / name).write_text(json.dumps(rec))
    (tmp_path / "runtime" / "current").symlink_to(tmp_path / "releases" / (pin + "0" * 32))
    return tmp_path


def run(home, capsys):
    code = mod.main(["--home", str(home)])
    return code, capsys.readouterr().out


def test_matching_pin_is_ok(tmp_path, capsys):
    assert run(site(tmp_path, "bbbbbbbb"), capsys)[0] == 0


def test_old_pin_is_red_with_restore_command(tmp_path, capsys):
    code, out = run(site(tmp_path, "aaaaaaaa"), capsys)
    assert code == 2 and "ln -sfn" in out and "bbbbbbbb" in out


def test_delivery_in_progress_is_ok(tmp_path, capsys):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    home = site(tmp_path, "aaaaaaaa", [("moving-pin-cccccccc.json", {"phase": "reviewed", "prepared_at": now})])
    assert run(home, capsys)[0] == 0


def test_no_records_is_unknown(tmp_path, capsys):
    (tmp_path / "patches").mkdir()
    (tmp_path / "runtime").mkdir()
    assert run(tmp_path, capsys)[0] == 3
