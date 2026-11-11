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


def test_signal_watch_needs_a_revision_integer_or_a_value_mapping(home):
    """A scalar signal watch is a revision integer, never a value like "GREEN".

    evaluate_wait compares a scalar against the signal's integer revision, so a string scalar read "changed" on every
    sweep and fired a spurious wake each pass (health's observe-genome-signal-green, 2026-11-11). Refuse it, and keep
    both documented forms working.
    """
    v = wait(); v["signals"] = {"svc": "GREEN"}
    with pytest.raises(ValueError): w.validate_wait(home, v)
    assert w.evaluate_wait(home, v, repo=home, now=100)["state"] == "unknown"   # surfaced, not a silent always-changed
    v["signals"] = {"svc": 3}
    w.validate_wait(home, v)                      # the documented scalar form: a revision integer
    v["signals"] = {"svc": {"value": "GREEN"}}
    w.validate_wait(home, v)                      # a value mapping
    r = record(); r["tasks"][0]["wait"]["signals"] = {"svc": "GREEN"}
    with pytest.raises(ValueError): w.save(home, "docs", r)


@pytest.mark.parametrize("change", [{"claims":[None]}, {"claims":{}}, {"tasks":[dict(id="x",state="waiting",wait=None)]},
    {"claims":[dict(path="x",expires=float("nan"))]}, {"tasks":[dict(id="x",state="ready",retry_intentions=["../../other"])]}])
def test_malformed_nested_records_remain_unknown(home, change):
    from mishe_tauftauf.state_files import write_json
    r=record();r.update(change)
    write_json(home/"work/roles/docs.json",r)
    assert w.load(home,"docs")["state"]=="UNKNOWN"
