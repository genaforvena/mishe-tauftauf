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


def test_check_binding_includes_executable_mode(repo):
    tool=repo/"tool.py";tool.write_text("#!/usr/bin/env python3\nprint(42)\n");tool.chmod(0o755)
    git(repo,"add","tool.py");git(repo,"commit","-qm","tool")
    result=w.check(repo,[str(tool)],now=100)
    assert result["code"]==0 and w.check_matches(repo,result)
    tool.chmod(0o644)
    assert not w.check_matches(repo,result)
