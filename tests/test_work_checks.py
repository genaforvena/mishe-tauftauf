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


def test_commit_latency_only_for_exact_checked_tree(repo):
    import time
    (repo/"file").write_text("checked")
    check=w.check(repo,[sys.executable,"-c","from pathlib import Path; assert Path(\"file\").read_text()==\"checked\""],now=time.time()-10)
    check["finished_at"]=time.time()-5
    git(repo,"add","file");git(repo,"commit","-qm","checked")
    sha=git(repo,"rev-parse","HEAD")
    result=w.bound_check_latency(repo,sha,[check])
    assert len(result)==1 and result[0]["commit"]==sha and result[0]["seconds_upper"]>=result[0]["seconds_lower"]>=0
    (repo/"file").write_text("later")
    assert w.bound_check_latency(repo,sha,[check])==[]
