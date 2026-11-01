import re

from mishe_tauftauf import host_containment


def _phases(stderr):
    return [line.split()[0].split("=", 1)[1] for line in stderr.splitlines() if line.startswith("phase=")]


def test_probe_emits_start_before_opened_and_keeps_io_phases(tmp_path, capsys):
    assert host_containment.filesystem_probe(str(tmp_path)) is True
    err = capsys.readouterr().err
    assert _phases(err) == ["start", "opened", "write", "flush", "fsync", "seek", "read", "done"]
    assert "phase=done ok=True" in err


def test_start_and_opened_carry_monotonic_offsets(tmp_path, capsys):
    host_containment.filesystem_probe(str(tmp_path))
    err = capsys.readouterr().err.splitlines()
    start = next(line for line in err if line.startswith("phase=start"))
    opened = next(line for line in err if line.startswith("phase=opened"))
    assert re.search(r"mono=\d+\.\d{6} elapsed=0\.000000$", start)
    match = re.search(r"mono=(\d+\.\d{6}) elapsed=(\d+\.\d{6})$", opened)
    assert match, opened
    assert float(match.group(2)) >= 0.0


def test_missing_directory_still_reports_start_and_unknown(tmp_path, capsys):
    missing = tmp_path / "absent"
    assert host_containment.filesystem_probe(str(missing)) is False
    err = capsys.readouterr().err
    assert _phases(err) == ["start"]
    assert "UNKNOWN host filesystem probe" in err
