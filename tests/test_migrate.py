import subprocess
import sys

import pytest

from mishe_tauftauf import intentions, migrate, owed, wall
from mishe_tauftauf.core.state import DispatchStore
from mishe_tauftauf.feed import Feed

NOW = 50_000.0


@pytest.fixture
def site(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    home = tmp_path / ".mishe-tauftauf"
    done = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "init"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    for role in ("genome", "health", "docs"):
        (home / "charters").mkdir(exist_ok=True)
        (home / "charters" / f"{role}.md").write_text(role)
    return home


def wake(home, role):
    return Feed(home).append("seed", f"seed wake {role} observation=1\nwoken").sequence


def settle(home, role, seq, cont=False):
    Feed(home).append("seed", f"seed yield {role} wake={seq}{' continue=1' if cont else ''}\nTurn settled (changed); notes")


def test_a_wake_with_no_yield_becomes_an_open_dispatch_to_reconcile(site):
    seq = wake(site, "genome")
    report = migrate.apply(site, ["genome", "health", "docs"], now=NOW)
    record = DispatchStore(site / "dispatch").read("genome")
    assert record.state.name == "unknown" and str(seq) in record.key and "old runtime" in record.detail
    assert DispatchStore(site / "dispatch").read("health") is None
    assert report["open_dispatches"] == ["genome"]


def test_a_settled_wake_leaves_no_dispatch_but_a_continue_becomes_an_intention(site):
    settle(site, "health", wake(site, "health"), cont=True)
    settle(site, "docs", wake(site, "docs"), cont=False)
    migrate.apply(site, ["genome", "health", "docs"], now=NOW)
    assert DispatchStore(site / "dispatch").open_records() == []
    assert "continue" in [i["id"] for i in intentions.list_(site, "health")]
    assert "continue" not in [i["id"] for i in intentions.list_(site, "docs")]


def test_every_role_gets_a_first_wake_intention_to_convert_its_wall_and_the_idle_initiative(site):
    migrate.apply(site, ["genome", "health"], now=NOW)
    for role in ("genome", "health"):
        ids = [i["id"] for i in intentions.list_(site, role)]
        assert "migrate-wall-next-actions" in ids and "idle-initiative" in ids


def test_messages_after_the_last_yield_are_unread_and_earlier_ones_are_not(site):
    wall.message(site, "witness", "genome", "old, already seen")
    settle(site, "genome", wake(site, "genome"))
    after = wall.message(site, "witness", "genome", "arrived after yield")
    (site / "inbox" / "genome").unlink()                       # the migration decides, not the old touch
    migrate.apply(site, ["genome"], now=NOW)
    assert owed.tape_cursor(site, "genome") < after.sequence
    assert "inbox" in owed.check(site, "genome", now=NOW).reasons
    mail = [seq for seq, _ in __import__("mishe_tauftauf.wake", fromlist=["x"]).default_mail_reader(
        site, "genome", owed.tape_cursor(site, "genome"))]
    assert mail == [after.sequence]


def test_apply_is_idempotent_and_never_replaces_an_existing_record(site):
    wake(site, "genome")
    migrate.apply(site, ["genome"], now=NOW)
    first = (site / "dispatch" / "genome.json").read_bytes()
    migrate.apply(site, ["genome"], now=NOW + 5)
    assert (site / "dispatch" / "genome.json").read_bytes() == first
