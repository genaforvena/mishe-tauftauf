"""The core's own command line: every subcommand's arguments are wired."""
import pytest

from mishe_tauftauf.core import cli as core_cli


def parse(*argv):
    return core_cli.parser().parse_args(["--home", "/tmp/site", *argv])


def test_status_needs_no_turn_arguments():
    args = parse("status")
    assert args.core_command == "status"
    assert args.home.name == "site"



def test_supervisor_dispatches_without_preexisting_wake(tmp_path, monkeypatch):
    from mishe_tauftauf.core.interfaces import Observation
    import time

    calls = []

    class Supervisor:
        def open_dispatch(self):
            return None

        def dispatch(self, role, key, payload, *, discard_skipped_poll):
            calls.append((role, key))
            raise StopIteration

    args = parse("supervise")
    from mishe_tauftauf.core import adapters
    monkeypatch.setattr(adapters, "CanonicalLiveObservation",
                        lambda **kwargs: lambda: Observation(fresh=True, healthy=True, detail="fixture"))
    args.home = tmp_path
    monkeypatch.setattr(core_cli, "_supervisor", lambda args: Supervisor())
    monkeypatch.setattr(time, "sleep", lambda seconds: (_ for _ in ()).throw(StopIteration))
    with pytest.raises(StopIteration):
        core_cli.cmd_supervise(args)
    assert len(calls) == 1
    assert calls[0][0] == "genome"


def test_command_and_synthetic_notes_are_not_accepted():
    with pytest.raises(SystemExit):
        parse("dispatch", "--key", "attempt", "--command", "/usr/bin/true")
    with pytest.raises(SystemExit):
        parse("supervise", "--notes", "/tmp/notes")


def test_reconcile_requires_a_detail():
    args = parse("reconcile", "--detail", "the wall showed no effect")
    assert args.detail == "the wall showed no effect"
    assert args.key is None


def test_an_unknown_subcommand_is_refused():
    with pytest.raises(SystemExit):
        parse("restore-the-plant")
