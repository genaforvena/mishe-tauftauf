import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import owed, wake, wall


@pytest.fixture
def site(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    home = tmp_path / ".mishe-tauftauf"
    done = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "init"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return home


def test_a_dm_touches_the_recipients_inbox_and_owes_the_role(site):
    wall.message(site, "genome", "health", "disk is full")
    assert owed.check(site, "health", now=1.0).reasons == ["inbox"]
    assert owed.check(site, "docs", now=1.0).reasons == []


def _append(site, source, text):
    done = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(site), "append",
                           "--source", source, text], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_an_addressed_dm_appended_through_the_plain_cli_touches_the_recipients_inbox(site):
    _append(site, "genome", "[dm] to=health\ndisk is full")
    assert owed.check(site, "health", now=1.0).reasons == ["inbox"]
    assert owed.check(site, "docs", now=1.0).reasons == []


def test_plain_append_that_is_not_an_addressed_dm_owes_nothing(site):
    _append(site, "genome", "[dm] to=health extra words on the first line\nnope")
    _append(site, "genome", "ordinary note\n[dm] to=docs")
    assert owed.check(site, "health", now=1.0).reasons == []
    assert owed.check(site, "docs", now=1.0).reasons == []


def test_a_self_addressed_or_invalid_dm_does_not_touch_an_inbox(site):
    _append(site, "health", "[dm] to=health\nto myself")
    _append(site, "genome", "[dm] to=../escape\nbad slug")
    assert owed.check(site, "health", now=1.0).reasons == []
    assert not (site / "inbox" / "..").resolve().joinpath("escape").exists()


def test_mail_reader_returns_only_addressed_messages_after_the_cursor(site):
    first = wall.message(site, "genome", "health", "one")
    wall.message(site, "genome", "docs", "for someone else")
    second = wall.message(site, "witness", "health", "two")
    mail = wake.default_mail_reader(site, "health", 0)
    assert [seq for seq, _ in mail] == [first.sequence, second.sequence]
    assert wake.default_mail_reader(site, "health", first.sequence) == [(second.sequence, mail[1][1])]
    assert all("for someone else" not in text for _, text in mail)


def test_natural_addressed_prose_to_a_declared_role_wakes_it_but_other_prose_does_not(site):
    # 2026-11-05: Health wrote "health to genome: ..." eight times; none was an addressed DM, so an idle Genome was never woken.
    _append(site, "health", "health to genome: host-capacity still RED, please review cdd289cd\nmore")
    _append(site, "health", "To docs: the page needs a bullet")
    _append(site, "health", "to the extent possible this is a note")            # 'the' is not a declared role
    _append(site, "health", "health to operator: escalation")                      # operator is a person, not a mind to wake
    _append(site, "senses", "senses to senses: note to self")                     # self
    assert owed.check(site, "genome", now=1.0).reasons == ["inbox"]
    assert owed.check(site, "docs", now=1.0).reasons == ["inbox"]
    assert owed.check(site, "senses", now=1.0).reasons == []
    assert not (site / "inbox" / "the").exists() and not (site / "inbox" / "operator").exists()


def test_the_mail_reader_returns_a_natural_addressed_message_the_wake_was_owed_for(site):
    wall.message(site, "genome", "health", "canonical")
    _append(site, "health", "health to genome: natural prose DM")
    _append(site, "health", "health to genome: second\nbody")
    heads = [body.splitlines()[0] for _, body in wake.default_mail_reader(site, "genome", 0)]
    assert heads == ["health to genome: natural prose DM", "health to genome: second"]     # the inbox wake finds its messages
    assert [body.splitlines()[0] for _, body in wake.default_mail_reader(site, "health", 0)] == ["[dm] to=health"]


def test_natural_prose_from_a_mind_slash_role_source_never_wakes_that_same_role(site):
    # Review of the moving-pin delivery: wall.addressed treats ROLE and mind/ROLE as the sender; so must the natural-prose branch.
    _append(site, "mind/senses", "to senses: note to self")
    _append(site, "mind/senses", "mind/senses to docs: please add a bullet")
    assert owed.check(site, "senses", now=1.0).reasons == []
    assert owed.check(site, "docs", now=1.0).reasons == ["inbox"]


def test_canonical_dm_from_a_mind_slash_role_source_to_itself_does_not_wake_it(site):
    _append(site, "mind/senses", "[dm] to=senses\nnote to self")
    _append(site, "mind/senses", "[dm] to=mind/senses\nnote to self")
    _append(site, "mind/senses", "[dm] to=docs\nreal")
    assert owed.check(site, "senses", now=1.0).reasons == []
    assert owed.check(site, "docs", now=1.0).reasons == ["inbox"]


def test_a_one_line_dm_is_recorded_but_warns_that_it_wakes_nobody(site):
    # 2026-11-06: operator DMs written as '[dm] to=senses <text>' on one line were never addressed DMs: no wake, no mail, no warning.
    done = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(site), "append", "--source", "operator",
                           "[dm] to=senses please read the queue"], capture_output=True, text=True)
    assert done.returncode == 0 and "not an addressed DM" in done.stderr and "[dm] to=senses" in done.stderr
    assert owed.check(site, "senses", now=1.0).reasons == []                # recorded as evidence, delivered to nobody
    ok = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(site), "append", "--source", "operator",
                         "[dm] to=senses\nplease read the queue"], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stderr == ""
    assert owed.check(site, "senses", now=1.0).reasons == ["inbox"]
