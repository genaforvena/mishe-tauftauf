"""End to end through the CLI with a fake `claude` binary on PATH."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import cli as _cli, intentions, owed, runtime_cli, signal_store as store

FAKE_CLAUDE = """#!/bin/sh
cat > "$MISHE_SEED_HOME/engine-saw-prompt.txt"
echo '{"usage":{"input_tokens":1,"output_tokens":1}}'
if [ -z "$FAKE_NO_YIELD" ]; then
  echo "new wall from fake" | "$PYBIN" -m mishe_tauftauf yield --result changed --again 30m
fi
"""


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / "site"
    (home / "charters").mkdir(parents=True)
    (home / "charters" / "genome.md").write_text("I am genome.")
    # every duty resolver in this checkout's duties.json must be a declared role
    resolvers = {d["resolver"] for d in json.loads((Path(__file__).resolve().parents[1] / "duties.json").read_text())["duties"]}
    for other in sorted(resolvers - {"genome"}):
        (home / "charters" / f"{other}.md").write_text(other)
    (home / "walls").mkdir()
    (home / "walls" / "genome.md").write_text("OLD WALL")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("PYBIN", sys.executable)
    monkeypatch.setenv("PYTHONPATH", str(Path(runtime_cli.__file__).parents[1]))
    monkeypatch.setenv("MISHE_SEED_HOME", str(home))
    monkeypatch.delenv("MISHE_ROLE", raising=False)
    # doctor checks the operator wrapper's pin; a healthy site has one exporting this checkout's src.
    wrapper_bin = home / "bin"
    wrapper_bin.mkdir()
    wrapper = wrapper_bin / "mishe-tauftauf"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export PYTHONPATH={Path(runtime_cli.__file__).parents[1]}\n"
        'exec python3 -m mishe_tauftauf "$@"\n')
    wrapper.chmod(0o755)
    return home


def cli(*argv):
    return _cli.main(list(argv))


def test_signal_register_observe_and_inbox(site, capsys):
    assert cli("signal", "register", "ci", "--max-age", "60", "--subscribers", "genome") == 0
    assert cli("signal", "observe", "ci", "RED", "--source", "t") == 0
    assert owed.check(site, "genome", now=__import__("time").time()).reasons == ["signal:ci"]


def test_wake_with_nothing_owed_does_not_start_the_engine(site):
    assert cli("wake", "genome") == 0
    assert not (site / "engine-saw-prompt.txt").exists()


def test_wake_runs_claude_headless_and_yield_lands(site):
    owed.touch_inbox(site, "genome")
    assert cli("wake", "genome") == 0
    assert "I am genome." in (site / "engine-saw-prompt.txt").read_text()
    assert (site / "walls" / "genome.md").read_text().startswith("new wall from fake")
    assert "again" in [i["id"] for i in intentions.list_(site, "genome")]
    assert owed.check(site, "genome", now=__import__("time").time()).reasons == []


def test_wake_without_yield_exits_nonzero_and_leaves_unknown(site, monkeypatch):
    monkeypatch.setenv("FAKE_NO_YIELD", "1")
    owed.touch_inbox(site, "genome")
    assert cli("wake", "genome") == 3


def test_hold_blocks_wake(site):
    owed.touch_inbox(site, "genome")
    assert cli("hold", "on", "--reason", "owner") == 0
    assert cli("wake", "genome") == 0 and not (site / "engine-saw-prompt.txt").exists()
    assert cli("hold", "off") == 0


def test_yield_outside_a_wake_is_refused(site):
    assert cli("yield", "--result", "changed") == 2


def _healthy_root_fs_probe(site, units_dir, monkeypatch):
    """Doctor's root-fs probe check must see this fixture, not the host's systemd unit or operator sample."""
    import json
    import time
    from mishe_tauftauf import release_promotion
    (site / "operator").mkdir(exist_ok=True)
    (site / "operator" / "dual-probe.jsonl").write_text(json.dumps({"at": time.time(), "event": "minute"}) + "\n")
    monkeypatch.setattr(release_promotion, "_probe_fields", lambda: {
        "FragmentPath": str(units_dir / release_promotion.DUAL_PROBE), "ActiveState": "active"})


def test_sync_then_doctor_green_and_meta_signal_fresh(site, tmp_path, monkeypatch):
    units_dir = tmp_path / "units"
    assert cli("sync", "--unit-dir", str(units_dir), "--no-systemctl") == 0
    assert (units_dir / "mishe-wake-genome.path").exists()
    _healthy_root_fs_probe(site, units_dir, monkeypatch)
    assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    sig = store.read(site, "doctor", now=__import__("time").time())
    assert (sig.state, sig.value) == ("OK", "GREEN")


def test_doctor_red_on_drift_exits_nonzero_and_records_red(site, tmp_path):
    units_dir = tmp_path / "units"
    cli("sync", "--unit-dir", str(units_dir), "--no-systemctl")
    (units_dir / "mishe-wake-genome.path").write_text("# mishe-generated: observation runtime (edit nothing here; run `mishe sync`)\nx\n")
    assert cli("doctor", "--unit-dir", str(units_dir)) == 1
    assert store.read(site, "doctor", now=__import__("time").time()).value == "RED"


def test_a_silent_doctor_reads_unknown(site, tmp_path):
    cli("sync", "--unit-dir", str(tmp_path / "u"), "--no-systemctl")
    cli("doctor", "--unit-dir", str(tmp_path / "u"))
    later = __import__("time").time() + 3600
    assert store.read(site, "doctor", now=later).state == "UNKNOWN"


def test_failed_command_reports_to_resolver(site, monkeypatch):
    sent = []
    from mishe_tauftauf import failure, work_boundary
    monkeypatch.setattr(failure, "default_notify", lambda home, role, text: sent.append(role))
    monkeypatch.setattr(work_boundary, "unit_load_state", lambda unit: "loaded")  # host-independent: no real unit needed
    assert cli("failed", "mishe-wake@docs.service") == 0
    assert sent == ["health"]


def test_signal_run_and_produce_commands(site):
    cli("signal", "register", "egress", "--max-age", "900")
    assert cli("signal", "run", "egress", "--timeout", "10", "--", sys.executable, "-c", "pass") == 0
    now = __import__("time").time()
    assert store.read(site, "egress", now=now).value == "GREEN"
    assert cli("signal", "run", "egress", "--timeout", "10", "--", "/nonexistent/x") == 2
    cli("signal", "register", "dispatch", "--max-age", "900")
    assert cli("produce", "dispatch") == 0
    assert store.read(site, "dispatch", now=now).value == "GREEN"


def test_recording_an_unknown_signal_arms_the_next_due_timer(site, monkeypatch):
    # Every command that writes signal state re-arms the timer to the next real due thing: observe,
    # signal run and produce all do. Recording an UNKNOWN used to skip it, so the timer kept firing on
    # the target computed before the reading changed (the finding that refused moving-pin-5c0a3e5c).
    from mishe_tauftauf import arm as armmod
    cli("signal", "register", "ci", "--max-age", "900")
    armed = []
    monkeypatch.setattr(armmod, "arm", lambda home, *, scheduler: armed.append(home))
    assert cli("signal", "observe-unknown", "ci", "--reason", "no readable observation", "--source", "t") == 0
    assert armed == [site]
    now = __import__("time").time()
    signal = store.read(site, "ci", now=now)
    assert signal.state == "UNKNOWN"
    assert signal.reason == "no readable observation"


def test_sync_installs_declared_producers_and_doctor_agrees(site, tmp_path, monkeypatch):
    (site / "producers.json").write_text(
        '[{"name":"egress","command":["/bin/true"],"every":300,"signal":{"max_age":900,"subscribers":["genome"]}}]')
    d = tmp_path / "u"
    assert cli("sync", "--unit-dir", str(d), "--no-systemctl") == 0
    assert (d / "mishe-producer-egress.timer").exists()
    assert "signal run egress" in (d / "mishe-producer-egress.service").read_text()
    assert "signals/egress.json" in (d / "mishe-wake-genome.path").read_text()
    _healthy_root_fs_probe(site, d, monkeypatch)
    assert cli("doctor", "--unit-dir", str(d)) == 0


def test_releasing_the_hold_offers_the_owed_role(site, monkeypatch):
    from mishe_tauftauf import wake
    started = []
    monkeypatch.setattr(wake, "systemd_starter", started.append)
    owed.touch_inbox(site, "genome")
    cli("hold", "on")
    cli("hold", "off")
    assert started == ["genome"]


def test_releasing_the_hold_starts_every_owed_role_without_a_new_trigger(site, monkeypatch):
    from mishe_tauftauf import wake
    started = []
    monkeypatch.setattr(wake, "systemd_starter", started.append)
    owed.touch_inbox(site, "genome")
    owed.touch_inbox(site, "health")
    cli("hold", "on")
    assert started == []                       # held: nothing fires yet
    cli("hold", "off")
    assert sorted(started) == ["genome", "health"]


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_sync_installs_every_duty_and_routes_failures_to_its_resolver(site, tmp_path):
    d = tmp_path / "u"
    assert cli("sync", "--unit-dir", str(d), "--no-systemctl", "--repo", str(REPO_ROOT)) == 0
    for duty in ("ci", "dispatch", "egress", "devto", "budget", "host-capacity"):
        assert (d / f"mishe-producer-{duty}.timer").exists(), duty
    assert "--red-codes none" in (d / "mishe-producer-devto.service").read_text()
    from mishe_tauftauf import failure
    assert failure.resolver_for(site, "mishe-producer-egress.service") == "health"
    assert failure.resolver_for(site, "mishe-producer-ci.service") == "genome"
    # Review of 20f172c7: a failed health debounce must not route to health's own inbox (self-retrigger).
    assert failure.resolver_for(site, "mishe-wake-debounce-health.service") == "genome"
    assert failure.resolver_for(site, "mishe-wake@health.service") == "genome"


def test_doctor_is_red_when_a_duty_resolver_has_no_charter(site, tmp_path, monkeypatch):
    d = tmp_path / "u"
    cli("sync", "--unit-dir", str(d), "--no-systemctl", "--repo", str(REPO_ROOT))
    _healthy_root_fs_probe(site, d, monkeypatch)
    assert cli("doctor", "--unit-dir", str(d), "--repo", str(REPO_ROOT)) == 0
    (site / "charters" / "health.md").unlink()
    cli("sync", "--unit-dir", str(d), "--no-systemctl", "--repo", str(REPO_ROOT))
    assert cli("doctor", "--unit-dir", str(d), "--repo", str(REPO_ROOT)) == 1


def test_migrate_command_reports_and_is_repeatable(site, capsys):
    assert cli("migrate") == 0
    out = capsys.readouterr().out
    assert "open_dispatches" in out
    assert (site / "intentions" / "genome" / "migrate-wall-next-actions.json").exists()
    assert cli("migrate") == 0


def test_init_installs_default_charters_and_operator_can_send_a_dm(tmp_path, capsys):
    import subprocess
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    home = tmp_path / ".mishe-tauftauf"
    assert _cli.main(["--home", str(home), "init"]) == 0
    assert (home / "charters" / "genome.md").exists() and (home / "charters" / "health.md").exists()
    assert _cli.main(["--home", str(home), "wall", "dm", "--source", "operator", "--to", "genome", "--text", "hi"]) == 0
    assert (home / "inbox" / "genome").exists()


def test_constants_borrowed_from_heavy_modules_stay_equal():
    from mishe_tauftauf import wake, yielding
    assert runtime_cli.WAKE_TIMEOUT == wake.DEFAULT_TIMEOUT
    assert runtime_cli.YIELD_RESULTS == yielding.RESULTS


def test_every_runtime_command_is_in_the_one_parser():
    from mishe_tauftauf import cli
    commands = set(cli.parser()._subparsers._group_actions[0].choices)
    assert {"wake", "yield", "signal", "inbox", "hold", "arm", "due", "failed", "sync", "doctor", "produce", "migrate"} <= commands


def test_discover_show_marks_unanchored_retained_readings_unknown(site, monkeypatch, capsys):
    from mishe_tauftauf import discovery
    monkeypatch.setattr(discovery, "latest", lambda h: {"created":"old", "node":"host", "observations":[{"id":"sense.disk.free","state":"verified","sample":42}]})
    assert cli("discover", "show") == 0
    text = capsys.readouterr().out
    assert "acquisition=unknown" in text
    assert "UNKNOWN retained=verified" in text


def test_discover_show_keeps_fresh_per_source_unknowns(site, monkeypatch, capsys):
    from mishe_tauftauf import discovery, scan_freshness
    point = scan_freshness.endpoint()
    monkeypatch.setattr(discovery, "latest", lambda h: {"created":"now", "node":"host", "acquisition":{"start":point,"end":scan_freshness.endpoint()},"observations":[{"id":"sense.body.redmi10","state":"unknown","sample":"consumer-poll-stale"}]})
    assert cli("discover", "show") == 0
    text = capsys.readouterr().out
    assert "acquisition=recent" in text and "unknown sample=consumer-poll-stale" in text


def test_discover_show_marks_stale_readings_unknown(site, monkeypatch, capsys):
    from mishe_tauftauf import discovery, scan_freshness
    monkeypatch.setattr(discovery, "latest", lambda h: {"created":"old", "node":"host", "observations":[{"id":"sense.disk.free","state":"verified","sample":42}]})
    monkeypatch.setattr(scan_freshness, "age_bounds", lambda snapshot: (901_000_000_000,902_000_000_000))
    assert cli("discover", "show") == 0
    text = capsys.readouterr().out
    assert "acquisition=stale" in text and "UNKNOWN retained=verified" in text


def test_default_pythonpath_is_the_installed_wake_pin(tmp_path, monkeypatch):
    units_dir = tmp_path / ".config" / "systemd" / "user"
    units_dir.mkdir(parents=True)
    (units_dir / "mishe-wake@.service").write_text(
        "[Service]\nEnvironment=PYTHONPATH=/x/releases/abc/src\n", encoding="utf-8")
    monkeypatch.setattr(runtime_cli.Path, "home", classmethod(lambda cls: tmp_path))
    assert runtime_cli.default_pythonpath() == "/x/releases/abc/src"


def test_default_pythonpath_falls_back_to_checkout_without_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_cli.Path, "home", classmethod(lambda cls: tmp_path))
    assert runtime_cli.default_pythonpath() == str(Path(runtime_cli.__file__).resolve().parents[1])


def _doctor_feed_calls(site, tmp_path, monkeypatch):
    """Run doctor on a site with a tape and record each Feed.entries(verify_records=...) call."""
    from mishe_tauftauf import feed as feed_mod
    units_dir = tmp_path / "units"
    assert cli("sync", "--unit-dir", str(units_dir), "--no-systemctl") == 0
    _healthy_root_fs_probe(site, units_dir, monkeypatch)
    (site / "chat.log").write_bytes(b"")
    seen = []
    real = feed_mod.Feed.entries

    def spy(self, *a, **kw):
        seen.append(kw.get("verify_records", True))
        return real(self, *a, **kw)
    monkeypatch.setattr(feed_mod.Feed, "entries", spy)
    return units_dir, seen


def _fake_tape(monkeypatch, seen, length):
    """Stand in for the tape: the whole-tape parse records "parse", a window records its start.

    The real framer is not needed to test the cursor, so these tests run without a built mishe-tape.
    """
    from types import SimpleNamespace
    from mishe_tauftauf import feed as feed_mod

    def fake(self, *a, **kw):
        if kw.get("verify_records", True) is False:
            seen.append("parse")
            return []
        start = kw.get("start", 1)
        seen.append(start)
        return [SimpleNamespace(sequence=s) for s in range(start, min(start + kw["limit"], length + 1))]
    monkeypatch.setattr(feed_mod.Feed, "entries", fake)


def test_doctor_verifies_one_window_per_run_and_advances_cursor(site, tmp_path, monkeypatch):
    """Doctor verifies a FEED_VERIFY_SLICE window per run, advances the stamped cursor,
    and wraps at the tape end, completing a cycle with the stamp time."""
    import time
    from mishe_tauftauf import runtime_cli as rc
    units_dir, seen = _doctor_feed_calls(site, tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "FEED_VERIFY_SLICE", 2)
    _fake_tape(monkeypatch, seen, length=5)
    stamp = site / "doctor-feed-verified.json"
    for expected_cursor in (3, 5):
        assert cli("doctor", "--unit-dir", str(units_dir)) == 0
        assert json.loads(stamp.read_text())["cursor"] == expected_cursor
    assert seen == ["parse", 1, "parse", 3]
    assert json.loads(stamp.read_text())["verified_at"] == 0.0   # no cycle completed yet
    seen.clear()
    before = time.time()
    assert cli("doctor", "--unit-dir", str(units_dir)) == 0     # window [5] is short: wrap
    assert seen == ["parse", 5]
    written = json.loads(stamp.read_text())
    assert written["cursor"] == 1 and written["verified_at"] >= before
    seen.clear()
    assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    assert seen == ["parse", 1]                                  # the next cycle starts at the head


def test_doctor_first_run_verifies_from_head(site, tmp_path, monkeypatch):
    units_dir, seen = _doctor_feed_calls(site, tmp_path, monkeypatch)
    _fake_tape(monkeypatch, seen, length=0)
    assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    assert seen == ["parse", 1]                                  # tape parse, then one window from the head
    stamp = json.loads((site / "doctor-feed-verified.json").read_text())
    assert stamp["cursor"] == 1                                  # empty tape: the window wrapped at once


def test_doctor_reads_malformed_stamp_json_as_head(site, tmp_path, monkeypatch):
    """A stamp that is not a valid cursor object restarts the cycle at the head, not a doctor crash."""
    units_dir, seen = _doctor_feed_calls(site, tmp_path, monkeypatch)
    _fake_tape(monkeypatch, seen, length=0)
    stamp = site / "doctor-feed-verified.json"
    for garbage in ('["just", "a", "list"]', '"unix epoch string"', '42', '{"cursor": "x"}'):
        stamp.write_text(garbage)
        seen.clear()
        assert cli("doctor", "--unit-dir", str(units_dir)) == 0
        assert seen == ["parse", 1]
    assert '"cursor"' in stamp.read_text()                       # a valid object is written back


def test_doctor_stays_red_on_corrupt_tape_without_full_pass(site, tmp_path, monkeypatch):
    from mishe_tauftauf import feed as feed_mod
    units_dir, seen = _doctor_feed_calls(site, tmp_path, monkeypatch)
    def boom(self, *a, **kw):
        raise feed_mod.FeedError("bad line")
    monkeypatch.setattr(feed_mod.Feed, "entries", boom)
    assert cli("doctor", "--unit-dir", str(units_dir)) == 1
    assert store.read(site, "doctor", now=__import__("time").time()).value == "RED"


def test_doctor_feed_cycle_retains_coverage_while_tape_grows(site, tmp_path, monkeypatch):
    """Every sequence is verified at least once per cycle even as the tape grows:
    an entry appended after a completed cycle is still covered by the next one,
    never starved by the cursor restarting at the head."""
    from types import SimpleNamespace
    from mishe_tauftauf import feed as feed_mod, runtime_cli as rc
    units_dir, _ = _doctor_feed_calls(site, tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "FEED_VERIFY_SLICE", 2)
    tape = {"length": 3}
    verified = set()

    def fake(self, *a, **kw):
        if kw.get("verify_records", True) is False:
            return []
        start = kw.get("start", 1)
        end = min(start + kw["limit"] - 1, tape["length"])
        verified.update(range(start, end + 1))
        return [SimpleNamespace(sequence=s) for s in range(start, end + 1)]

    monkeypatch.setattr(feed_mod.Feed, "entries", fake)
    for _ in range(2):                                       # ceil(3/2) runs cover [1..3]
        assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    assert verified == {1, 2, 3}
    tape["length"] = 5                                       # two entries appended after the cycle
    verified.clear()
    for _ in range(3):                                       # ceil(5/2) runs cover [1..5]
        assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    assert verified == {1, 2, 3, 4, 5}


def test_doctor_reports_a_stalled_feed_cycle_so_lost_coverage_is_visible(site, tmp_path, monkeypatch):
    """A cycle that never reaches the tape end turns doctor RED and the doctor signal RED.
    The tape grows by one slice between runs, so the cursor advances but never wraps and
    verified_at never advances; the run budget is the tape length the cycle began with,
    frozen, so that growth cannot inflate the expected count and hide the stall."""
    from types import SimpleNamespace
    from mishe_tauftauf import feed as feed_mod, runtime_cli as rc
    units_dir, _ = _doctor_feed_calls(site, tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "FEED_VERIFY_SLICE", 2)
    monkeypatch.setattr(rc, "FEED_VERIFY_STALL_FACTOR", 2)
    tape = {"length": 4}

    def fake(self, *a, **kw):
        if kw.get("verify_records", True) is False:
            tape["length"] += 2                               # the tape outgrows one slice per run
            return [SimpleNamespace(sequence=s) for s in range(1, tape["length"] + 1)]
        start = kw.get("start", 1)
        end = min(start + kw["limit"] - 1, tape["length"])
        return [SimpleNamespace(sequence=s) for s in range(start, end + 1)]

    monkeypatch.setattr(feed_mod.Feed, "entries", fake)
    states = []
    for _ in range(8):                                        # enough runs to cross the budget
        cli("doctor", "--unit-dir", str(units_dir))
        states.append(store.read(site, "doctor", now=__import__("time").time()).value)
    assert "RED" in states, states
    stamp = json.loads((site / "doctor-feed-verified.json").read_text())
    assert stamp["cursor"] > 1 and stamp["verified_at"] == 0.0   # advanced, never wrapped
    assert stamp["cycle_runs"] > 0


def test_doctor_completes_a_cycle_under_slow_tape_growth_without_a_stall(site, tmp_path, monkeypatch):
    """A tape that grows slower than one slice per run still wraps within its budget, so the
    stall detector stays quiet and an entry appended mid-cycle is still covered."""
    from types import SimpleNamespace
    from mishe_tauftauf import feed as feed_mod, runtime_cli as rc
    units_dir, _ = _doctor_feed_calls(site, tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "FEED_VERIFY_SLICE", 2)
    monkeypatch.setattr(rc, "FEED_VERIFY_STALL_FACTOR", 2)
    tape = {"length": 4, "runs": 0}

    def fake(self, *a, **kw):
        if kw.get("verify_records", True) is False:
            tape["runs"] += 1
            if tape["runs"] == 2:                             # one entry appended mid-cycle
                tape["length"] += 1
            return [SimpleNamespace(sequence=s) for s in range(1, tape["length"] + 1)]
        start = kw.get("start", 1)
        end = min(start + kw["limit"] - 1, tape["length"])
        return [SimpleNamespace(sequence=s) for s in range(start, end + 1)]

    monkeypatch.setattr(feed_mod.Feed, "entries", fake)
    for _ in range(5):
        assert cli("doctor", "--unit-dir", str(units_dir)) == 0
    assert json.loads((site / "doctor-feed-verified.json").read_text())["verified_at"] > 0
