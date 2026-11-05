"""Every retired detector has a replacement duty; each duty's failure reaches its resolver."""
import json
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import duties, owed, producers, signal_store as store

REPO = Path(__file__).resolve().parents[1]
NOW = 10_000.0
PY = sys.executable
ALL = duties.load(REPO)


def declared(home):
    items = duties.declared(REPO, home)
    for item in items:
        store.register(home, item["name"], max_age=item["signal"]["max_age"],
                       subscribers=item["signal"]["subscribers"])
    return items


def test_every_duty_has_a_resolver_signal_and_sane_timing():
    assert ALL and len({d["name"] for d in ALL}) == len(ALL)
    for d in ALL:
        # A duty that replaces nothing is new coverage; `replaces` must still be declared.
        assert d["resolver"] and isinstance(d["replaces"], list) and d["covers"], d["name"]
        assert d["max_age"] >= d["every"] + d["timeout"], f"{d['name']} would read stale while healthy"
        assert d["max_age"] <= 6 * d["every"], f"{d['name']} would take too long to notice silence"


def test_no_unit_is_both_replaced_and_retired_or_kept():
    manifest = json.loads((REPO / "duties.json").read_text())
    replaced = {u for d in manifest["duties"] for u in d["replaces"]}
    other = {u for r in manifest["retired"] + manifest["kept"] for u in r["units"]}
    assert not replaced & other


@pytest.mark.parametrize("name", [d["name"] for d in ALL])
def test_red_reaches_the_resolver(tmp_path, name, monkeypatch):
    item = next(i for i in declared(tmp_path) if i["name"] == name)
    resolver = item["signal"]["subscribers"][0]
    if item.get("builtin"):
        monkeypatch.setitem(producers.BUILTIN, name, lambda home, now, **kw: "RED")
        producers.produce(tmp_path, name, now=NOW)
    else:
        producers.run(tmp_path, name, [PY, "-c", "import sys; sys.exit(2)"], now=NOW, timeout=10,
                      red_codes=tuple(item.get("red_codes", [2])) or (2,))
        if not item.get("red_codes", [2]):
            return                              # a duty with no RED code reports only GREEN/UNKNOWN
    assert f"signal:{name}" in owed.check(tmp_path, resolver, now=NOW).reasons


@pytest.mark.parametrize("name", [d["name"] for d in ALL if not d.get("builtin")])
def test_a_crashing_check_never_reads_green(tmp_path, name):
    item = next(i for i in declared(tmp_path) if i["name"] == name)
    assert producers.run(tmp_path, name, [PY, "-c", "raise SystemExit(1)"], now=NOW, timeout=10) == 2
    assert store.read(tmp_path, name, now=NOW).state == "UNKNOWN"


@pytest.mark.parametrize("name", [d["name"] for d in ALL])
def test_silence_ages_to_unknown_and_wakes_the_resolver(tmp_path, name):
    item = next(i for i in declared(tmp_path) if i["name"] == name)
    resolver = item["signal"]["subscribers"][0]
    store.observe(tmp_path, name, "GREEN", now=NOW, source="t")
    owed.advance(tmp_path, resolver, owed.check(tmp_path, resolver, now=NOW))
    late = NOW + item["signal"]["max_age"] + 1
    assert store.read(tmp_path, name, now=late).state == "UNKNOWN"
    assert store.expire(tmp_path, now=late) == [name]
    assert f"signal:{name}" in owed.check(tmp_path, resolver, now=late).reasons


def test_readings_renewal_has_a_bounded_senses_owned_producer():
    item = next(d for d in ALL if d["name"] == "readings")
    assert item["resolver"] == "senses"
    assert item["command"][-2:] == ["discover", "scan"]
    assert item["every"] == 600 and item["timeout"] == 180
    assert item["max_age"] == 900 and item["red_codes"] == []


def test_committed_duty_manifest_and_script_use_release_but_repo_argument_stays_shared(tmp_path):
    import json
    from mishe_tauftauf import duties
    repo=tmp_path/"repo";repo.mkdir();source=tmp_path/"release";source.mkdir()
    (repo/"duties.json").write_text(json.dumps(dict(duties=[])))
    declaration=dict(name="test",every=60,timeout=10,max_age=180,resolver="genome",command=["{python}","{repo}/checks/example.py","--repo","{repo}"])
    (source/"duties.json").write_text(json.dumps(dict(duties=[declaration])))
    found=duties.declared(repo,tmp_path/"home",source=source)
    assert found[0]["command"][1]==str(source/"checks/example.py")
    assert found[0]["command"][-1]==str(repo)
