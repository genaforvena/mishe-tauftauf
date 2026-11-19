import json

from mishe_tauftauf import release_promotion as rp

NOW = 1_800_000_000.0


def _site(tmp_path, *, at=NOW - 10, unit=True):
    home = tmp_path / "site"
    (home / "operator").mkdir(parents=True)
    (home / "operator" / "dual-probe.jsonl").write_text(
        json.dumps({"at": at, "event": "minute"}) + "\n" if at is not None else "")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    if unit:
        (unit_dir / rp.DUAL_PROBE).write_text("[Unit]\n")
    return home, unit_dir


def _fields(unit_dir, *, state="active", fragment=None):
    return {"FragmentPath": str(unit_dir / rp.DUAL_PROBE) if fragment is None else fragment, "ActiveState": state}


def test_healthy_installed_unit_with_fresh_sample_has_no_problems(tmp_path):
    home, unit_dir = _site(tmp_path)
    assert rp.probe_problems(home, unit_dir, now=NOW, fields=_fields(unit_dir)) == []


def test_inactive_probe_is_red(tmp_path):
    home, unit_dir = _site(tmp_path)
    problems = rp.probe_problems(home, unit_dir, now=NOW, fields=_fields(unit_dir, state="failed"))
    assert any("ActiveState=failed" in p for p in problems)


def test_transient_fragment_is_red_when_installed_unit_exists(tmp_path):
    home, unit_dir = _site(tmp_path)
    fields = _fields(unit_dir, fragment="/run/user/1000/systemd/transient/" + rp.DUAL_PROBE)
    problems = rp.probe_problems(home, unit_dir, now=NOW, fields=fields)
    assert any("is not the installed unit" in p for p in problems)


def test_stale_sample_is_red_even_when_unit_is_active(tmp_path):
    home, unit_dir = _site(tmp_path, at=NOW - 600)
    problems = rp.probe_problems(home, unit_dir, now=NOW, fields=_fields(unit_dir))
    assert problems == [f"root-fs probe: newest sample 600s old (limit {rp.PROBE_STALE_S}s)"]


def test_missing_sample_file_is_red(tmp_path):
    home, unit_dir = _site(tmp_path)
    (home / "operator" / "dual-probe.jsonl").unlink()
    problems = rp.probe_problems(home, unit_dir, now=NOW, fields=_fields(unit_dir))
    assert any("no readable sample" in p for p in problems)


def test_missing_installed_unit_is_red(tmp_path):
    home, unit_dir = _site(tmp_path, unit=False)
    problems = rp.probe_problems(home, unit_dir, now=NOW, fields=_fields(unit_dir))
    assert any("installed unit" in p and "missing" in p for p in problems)


def test_systemctl_failure_is_red_not_green(tmp_path):
    home, unit_dir = _site(tmp_path)

    def broken(**kw):
        raise FileNotFoundError("systemctl")
    original = rp._probe_fields
    rp._probe_fields = broken
    try:
        out = rp.probe_problems(home, unit_dir, now=NOW)
    finally:
        rp._probe_fields = original
    assert any("systemctl unreadable" in p for p in out)


def test_probe_show_timeout_is_capped_by_the_remaining_budget(tmp_path, monkeypatch):
    """Review #303049: the fixed 30 s root-fs probe show cannot outlive the observer budget."""
    from types import SimpleNamespace
    from mishe_tauftauf import work_boundary as wb

    home, unit_dir = _site(tmp_path)
    monkeypatch.setattr(wb, "time", SimpleNamespace(monotonic=lambda: 0.0))
    timeouts = []

    def probe(**kw):
        timeouts.append(kw["timeout"])
        return _fields(unit_dir)

    monkeypatch.setattr(rp, "_probe_fields", probe)
    assert rp.probe_problems(home, unit_dir, now=NOW, deadline=5.0) == []
    assert timeouts == [5.0]  # min(30, remaining 5), not the fixed 30 s default


def test_probe_timeout_is_red_not_green(tmp_path, monkeypatch):
    """A timed-out root-fs show is reported unreadable, never a healthy probe."""
    import subprocess

    home, unit_dir = _site(tmp_path)

    def slow(**kw):
        raise subprocess.TimeoutExpired(kw["timeout"], ["systemctl", "show"])

    monkeypatch.setattr(rp, "_probe_fields", slow)
    out = rp.probe_problems(home, unit_dir, now=NOW)
    assert any("systemctl unreadable" in p for p in out)
