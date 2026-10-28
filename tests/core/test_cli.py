"""The core's own command line: every subcommand's arguments are wired."""
import pytest

from mishe_tauftauf.core import cli as core_cli


def parse(*argv):
    return core_cli.parser().parse_args(["--home", "/tmp/site", *argv])


def test_status_needs_no_turn_arguments():
    args = parse("status")
    assert args.core_command == "status"
    assert args.home.name == "site"


def test_dispatch_carries_everything_the_worker_needs():
    args = parse("dispatch", "--key", "wake-1", "--notes", "/tmp/notes.md",
                 "--command", "/usr/bin/true", "--result", "changed",
                 "--continue")
    assert args.key == "wake-1"
    assert args.notes == "/tmp/notes.md"
    assert args.command == ["/usr/bin/true"]
    assert args.result == "changed"
    assert args.continue_task is True
    assert args.timeout > 0


def test_supervise_carries_everything_the_worker_needs():
    """The resident loop builds the same worker as a single dispatch."""
    args = parse("supervise", "--notes", "/tmp/notes.md",
                 "--command", "/usr/bin/true", "--result", "verified",
                 "--continue", "--interval", "5")
    assert args.notes == "/tmp/notes.md"
    assert args.command == ["/usr/bin/true"]
    assert args.result == "verified"
    assert args.continue_task is True
    assert args.interval == 5
    assert args.timeout > 0


def test_supervise_builds_a_worker_without_an_attribute_error(tmp_path, monkeypatch):
    """The resident loop must be able to build its worker."""
    args = parse("supervise", "--notes", str(tmp_path / "notes.md"),
                 "--command", "/usr/bin/true")
    args.home = tmp_path
    worker = core_cli._canonical_worker(args)
    assert worker.role == args.role
    assert worker.notes_file == tmp_path / "notes.md"


def test_supervisor_key_reads_pending_canonical_wake(tmp_path):
    from mishe_tauftauf.feed import Feed

    args = parse("supervise", "--notes", str(tmp_path / "notes.md"),
                 "--command", "/usr/bin/true")
    args.home = tmp_path
    args.role = "genome"
    assert core_cli._pending_wake(args) is None
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    assert core_cli._pending_wake(args) == wake.sequence


def test_supervisor_holds_without_a_canonical_wake(tmp_path, monkeypatch, capsys):
    from mishe_tauftauf.core.interfaces import Observation
    import time

    class Supervisor:
        def open_dispatch(self):
            return None

        def observe(self):
            return Observation(fresh=True, healthy=True, detail="fixture")

        def dispatch(self, *args, **kwargs):
            pytest.fail("dispatched without a canonical wake")

    args = parse("supervise", "--notes", str(tmp_path / "notes.md"),
                 "--command", "/usr/bin/true")
    args.home = tmp_path
    monkeypatch.setattr(core_cli, "_supervisor", lambda args: Supervisor())
    def stop(_):
        raise StopIteration
    monkeypatch.setattr(time, "sleep", stop)
    with pytest.raises(StopIteration):
        core_cli.cmd_supervise(args)
    assert "no canonical wake" in capsys.readouterr().out


def test_reconcile_requires_a_detail():
    args = parse("reconcile", "--detail", "the wall showed no effect")
    assert args.detail == "the wall showed no effect"
    assert args.key is None


def test_an_unknown_subcommand_is_refused():
    with pytest.raises(SystemExit):
        parse("restore-the-plant")
