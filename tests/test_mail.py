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
