import sys
from pathlib import Path
from tests.test_work_receipts import repo, git
from mishe_tauftauf import work_receipts as w


def test_passing_check_binds_exact_bytes_and_later_edit_invalidates(repo):
    r=w.check(repo,[sys.executable,"-c","assert 2+2==4"],now=100)
    assert r["code"]==0 and r["bound"]
    assert w.check_matches(repo,r)
    (repo/"file").write_text("later edit")
    assert not w.check_matches(repo,r)
