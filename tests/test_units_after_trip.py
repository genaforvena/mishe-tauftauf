"""Drill for checks/units-after-trip.py: the post-trip state goes RED, one bounded start, then GREEN.

Simulated, not live: a fake systemctl/journalctl stands in for the host, so no real unit is stopped or started.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "checks" / "units-after-trip.py"


def _load():
    spec = importlib.util.spec_from_file_location("units_after_trip", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _PostTripHost:
    """One harmless enabled unit dead after an unmet-condition skip, plus host-guard stopped by design."""

    def __init__(self):
        self.active = False
        self.calls = []

    def run(self, args):
        self.calls.append(list(args))
        if args[:1] == ["list-unit-files"]:
            return _Result("mishe-drill-probe.service enabled enabled\nmishe-host-guard.service enabled enabled\n")
        if args[:1] == ["show"] and args[1] == "mishe-drill-probe.service":
            state = "active" if self.active else "inactive"
            return _Result(f"ActiveState={state}\nResult=success\nRestart=always\n")
        if args[:1] == ["show"]:
            return _Result("ActiveState=inactive\nResult=success\nRestart=always\n")
        if args[:1] == ["start"]:
            self.active = True
        return _Result()

    def runj(self, args):
        return _Result("Stopped drill\nunit skipped because of an unmet condition check (ConditionPathExists=!tripped)\n")


def test_dead_after_trip_goes_red_then_one_start_then_green():
    mod = _load()
    host = _PostTripHost()
    code, lines, state = mod.evaluate({}, now=1000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=120)
    assert code == 2 and lines[0] == "dead after trip: 1"
    assert not any(call[:1] == ["start"] for call in host.calls), "no start inside the grace period"

    code, lines, state = mod.evaluate(state, now=1130.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=120)
    assert code == 2 and "started once (exit 0)" in lines[1]
    assert sum(call[:1] == ["start"] for call in host.calls) == 1

    host.active = True
    code, lines, state = mod.evaluate(state, now=1200.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=120)
    assert code == 0 and state == {}


def test_latch_present_is_green_and_clears_episodes():
    mod = _load()
    host = _PostTripHost()
    code, lines, state = mod.evaluate({"mishe-drill-probe.service": {"dead_since": 1.0, "started": True}},
                                      now=1300.0, latch_present=True, run=host.run, runj=host.runj,
                                      act=True, grace=120)
    assert code == 0 and state == {}
    assert not any(call[:1] == ["start"] for call in host.calls)


def test_failure_after_a_start_does_not_repeat_the_start():
    """A later unit that raises must not erase the started flag of an earlier unit (the defect of -2)."""
    mod = _load()

    class _TwoUnits(_PostTripHost):
        def __init__(self):
            super().__init__()
            self.fail_second = True

        def run(self, args):
            if args[:1] == ["list-unit-files"]:
                return _Result("mishe-drill-probe.service enabled enabled\n"
                               "mishe-drill-second.service enabled enabled\n")
            if args[:1] == ["show"] and args[1] == "mishe-drill-second.service":
                if self.fail_second:
                    raise RuntimeError("show mishe-drill-second.service failed: transient")
                return _Result("ActiveState=inactive\nResult=success\nRestart=always\n")
            return super().run(args)

    host = _TwoUnits()
    code, lines, state = mod.evaluate({}, now=1000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert code == 3, "the unreadable second unit makes the run undecidable"
    assert state["mishe-drill-probe.service"]["started"] is True, "the first unit's start is recorded"
    assert sum(call[:1] == ["start"] for call in host.calls) == 1

    host.fail_second = False
    host.active = False  # the unit is dead again: only the recorded started flag can stop a second start
    code, lines, state = mod.evaluate(state, now=1100.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert sum(call[:1] == ["start"] and call[1] == "mishe-drill-probe.service" for call in host.calls) == 1, \
        "the next run must not start the same unit again"


def test_undecodable_journal_line_does_not_drop_an_earlier_start():
    """A ValueError (UnicodeDecodeError) from one unit's journal read is an unknown for that unit, not a crash."""
    mod = _load()

    class _BadJournalSecond(_PostTripHost):
        def run(self, args):
            if args[:1] == ["list-unit-files"]:
                return _Result("mishe-drill-probe.service enabled enabled\n"
                               "mishe-drill-second.service enabled enabled\n")
            if args[:1] == ["show"] and args[1] == "mishe-drill-second.service":
                return _Result("ActiveState=inactive\nResult=success\nRestart=always\n")
            return super().run(args)

        def runj(self, args):
            if args[-1:] == ["mishe-drill-second.service"] or "mishe-drill-second.service" in args:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return super().runj(args)

    host = _BadJournalSecond()
    code, lines, state = mod.evaluate({}, now=1000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert code == 3, "the unreadable second unit is UNKNOWN, not a traceback"
    assert state["mishe-drill-probe.service"]["started"] is True, "the first unit's start survives the ValueError"
    assert sum(call[:1] == ["start"] for call in host.calls) == 1


def test_raising_start_still_counts_as_the_one_start():
    mod = _load()

    class _StartRaises(_PostTripHost):
        def run(self, args):
            if args[:1] == ["start"]:
                self.calls.append(list(args))
                raise OSError("systemctl timed out")
            return super().run(args)

    host = _StartRaises()
    code, lines, state = mod.evaluate({}, now=1000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert code == 3 and state["mishe-drill-probe.service"]["started"] is True
    code, lines, state = mod.evaluate(state, now=1200.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert sum(call[:1] == ["start"] for call in host.calls) == 1


def test_deliberately_stopped_unit_is_not_started():
    mod = _load()

    class _Stopped(_PostTripHost):
        def runj(self, args):
            return _Result("Stopped mishe-host-guard by operator\n")

    host = _Stopped()
    code, lines, state = mod.evaluate({}, now=5000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert code == 0 and not any(call[:1] == ["start"] for call in host.calls)


def test_consent_gated_microphone_listener_is_never_revived_after_trip():
    mod = _load()
    assert "mishe-audio-listener.service" in mod.DELIBERATE

    class _ListenerHost(_PostTripHost):
        def run(self, args):
            if args[:1] == ["list-unit-files"]:
                return _Result("mishe-audio-listener.service enabled enabled\n")
            return super().run(args)

    host = _ListenerHost()
    code, lines, state = mod.evaluate({}, now=5000.0, latch_present=False, run=host.run, runj=host.runj,
                                      act=True, grace=0)
    assert code == 0 and not any(call[:1] == ["start"] for call in host.calls)


def test_failed_state_save_after_a_start_reports_unknown_and_does_not_crash(tmp_path, monkeypatch, capsys):
    mod = _load()
    host = _PostTripHost()
    monkeypatch.setattr(mod, "run_systemctl", host.run)
    monkeypatch.setattr(mod, "run_journal", host.runj)
    monkeypatch.setattr(mod, "LATCH", tmp_path / "no-latch")

    def _save_fails(path, state):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "save_state", _save_fails)
    code = mod.main(["--home", str(tmp_path), "--act", "--grace", "0"])
    out = capsys.readouterr().out
    assert code == 3
    assert "cannot save state" in out
    assert sum(call[:1] == ["start"] for call in host.calls) == 1
