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
        assert d["resolver"] and d["replaces"] and d["covers"], d["name"]
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
