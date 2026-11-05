import subprocess
import sys
from pathlib import Path
import pytest
from mishe_tauftauf import release_promotion as p
from mishe_tauftauf.state_files import read_json


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def tree(tmp_path):
    repo=tmp_path / "repo"; repo.mkdir(); home=tmp_path / "home"; home.mkdir()
    git(repo, "init", "-q"); git(repo, "config", "user.name", "test"); git(repo, "config", "user.email", "t@example.org")
    (repo / "docs").mkdir(); (repo / "docs/page.md").write_text("old")
    git(repo,"add",".");git(repo,"commit","-qm","old"); old=git(repo,"rev-parse","HEAD")
    (repo / "docs/page.md").write_text("new");git(repo,"add",".");git(repo,"commit","-qm","new")
    return repo,home,old,git(repo,"rev-parse","HEAD")


def test_failed_probe_restores_old_consumer_and_records_events(tree):
    repo,home,old,sha=tree; pins=[old]; probes=[]
    def activate(source, target): pins[0]=target
    def probe(argv, **kw):
        probes.append(pins[0]); return dict(code=int(pins[0]==sha), output=pins[0])
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert 2+2==4"],observe=["probe"],
                previous=old,activate=activate,probe=probe,reviewer=lambda *a: None,closure=lambda *a: None)
    assert pins[0]==old
    assert probes==[sha,old]
    assert r["phase"]=="rolled-back"
    assert not r.get("delivery_verified",False)


def test_failed_restore_is_unknown(tree):
    repo,home,old,sha=tree
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a: None,probe=lambda *a,**k:dict(code=1,output="bad"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    assert r["phase"]=="UNKNOWN"
    assert r["rollback_probe"]["code"]==1


def test_ordinary_reviewer_outage_does_not_block(tree):
    repo,home,old,sha=tree
    def unavailable(*a): raise ValueError("offline")
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=unavailable,closure=lambda *a:None)
    assert r["phase"]=="delivered" and r["review_pending"]
    assert r["tests_passed"] and r["observed"]


def test_high_impact_cannot_use_ordinary_path(tree):
    repo,home,old,sha=tree
    target=repo/"src/mishe_tauftauf/owed.py";target.parent.mkdir(parents=True);target.write_text("pass")
    git(repo,"add",".");git(repo,"commit","-qm","scheduler")
    with pytest.raises(ValueError, match="strong"):
        p.promote(home,repo,git(repo,"rev-parse","HEAD"),check=["check"],observe=["probe"],previous=old)


def test_dirty_untracked_module_cannot_enter_release(tree):
    repo,home,old,sha=tree
    (repo/"untracked.py").write_text("secret=1")
    source=p.materialize(home,repo,sha)
    assert not (source/"untracked.py").exists()
    assert (source/"docs/page.md").read_text()=="new"


def test_release_mutation_during_check_cannot_promote(tree):
    repo,home,old,sha=tree
    with pytest.raises(ValueError,match="source changed"):
        p.promote(home,repo,sha,check=[sys.executable,"-c","from pathlib import Path; Path(\"docs/page.md\").write_text(\"mutated\")"],
          observe=["probe"], previous=old,activate=lambda *a:None,closure=lambda *a:None)


def test_running_wake_retains_pin(tree):
    repo,home,old,sha=tree
    with p.delivery_lock(home):
        with pytest.raises(ValueError,match="dispatch lock"):
            p.promote(home,repo,sha,check=["check"],observe=["probe"],previous=old)
