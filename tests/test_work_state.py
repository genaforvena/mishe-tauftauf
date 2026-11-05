import pytest
from mishe_tauftauf import work_state as w


@pytest.fixture
def home(tmp_path):
    (tmp_path / "charters").mkdir()
    for role in ["docs", "steward"]: (tmp_path / "charters" / f"{role}.md").write_text(role)
    return tmp_path


def wait():
    return dict(resolver="docs", evidence_needed="new bytes", deadline=200,
                escalate_to="steward", action_at_deadline="choose alternative", signals={}, paths={})


def record():
    return dict(version=1, role="docs", initiative="reader journey", tasks=[
        dict(id="repair", deliverable="working view", state="waiting", wait=wait())])


def test_waits_preserve_ready_alternative(home):
    r = record(); w.save(home, "docs", r)
    assert w.all_waiting(w.load(home, "docs"))
    r["tasks"].append(dict(id="edit", deliverable="edit book", state="ready"))
    w.save(home, "docs", r)
    assert not w.all_waiting(w.load(home, "docs"))


def test_content_and_absence_not_mtime(home):
    repo = home / "repo"; repo.mkdir(); path = repo / "page"; path.write_text("a")
    v = wait(); v["paths"] = {"page": w.path_digest(repo, "page")}
    assert w.evaluate_wait(home, v, repo=repo, now=100)["state"] == "unchanged"
    path.touch()
    assert w.evaluate_wait(home, v, repo=repo, now=100)["state"] == "unchanged"
    path.unlink()
    assert w.evaluate_wait(home, v, repo=repo, now=100)["state"] == "changed"
    v["paths"] = {"page": "ABSENT"}
    assert w.evaluate_wait(home, v, repo=repo, now=100)["state"] == "unchanged"
    assert w.evaluate_wait(home, v, repo=repo, now=200)["state"] == "due"


def test_invalid_state_never_mutes(home):
    r = record(); r["tasks"][0]["wait"]["resolver"] = "imaginary"
    with pytest.raises(ValueError): w.save(home, "docs", r)
    p = home / "work/roles/docs.json"; p.parent.mkdir(parents=True); p.write_text("{")
    assert w.load(home, "docs")["state"] == "UNKNOWN"
    assert not w.all_waiting(w.load(home, "docs"))


def test_escape_is_unknown(home):
    v = wait(); v["paths"] = {"../secret": "a"}
    assert w.evaluate_wait(home, v, repo=home, now=100)["state"] == "unknown"


def test_claim_expiry_is_advisory_and_does_not_overwrite(home):
    r = record(); r["claims"] = [dict(path="page", wake="k", base="digest", expires=150)]
    w.save(home, "docs", r)
    assert w.claims(home, now=100)[0]["expired"] is False
    assert w.claims(home, now=200)[0]["expired"] is True
