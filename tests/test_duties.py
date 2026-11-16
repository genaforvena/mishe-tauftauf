"""Every retired detector has a replacement duty; each duty's failure reaches its resolver."""
import json
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import duties, owed, producers, signal_store as store, units

REPO = Path(__file__).resolve().parents[1]
NOW = 10_000.0
PY = sys.executable
ALL = duties.load(REPO)


def declared(home):
    items = duties.declared(REPO, home)
    for item in items:
        store.register(home, item["name"], max_age=item["signal"]["max_age"],
                       subscribers=item["signal"]["subscribers"],
                       wake_on=item["signal"].get("wake_on", "change"))
    return items


def test_every_duty_has_a_resolver_signal_and_sane_timing():
    assert ALL and len({d["name"] for d in ALL}) == len(ALL)
    for d in ALL:
        # A duty that replaces nothing is new coverage; `replaces` must still be declared.
        assert d["resolver"] and isinstance(d["replaces"], list) and d["covers"], d["name"]
        assert d["max_age"] >= d["every"] + d["timeout"], f"{d['name']} would read stale while healthy"
        # The producer timer adds RandomizedDelaySec + AccuracySec on top of the period, so a run
        # can start this late; a max_age inside that window reads expired between healthy runs and
        # wakes the subscriber for nothing (2026-11-16: audio-listener-coverage flapped every run).
        latest_start = d["every"] + units.timer_jitter(d["every"]) + units.TIMER_ACCURACY_S
        assert d["max_age"] > latest_start, \
            f"{d['name']} would read stale between healthy runs (max_age {d['max_age']} <= {latest_start})"
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
    if item["signal"].get("wake_on") == "state":
        # A reading signal (footprint) carries no RED state: its fault channel is UNKNOWN (silence or a
        # failed read), covered by test_silence_ages_to_unknown_and_wakes_the_resolver and by the
        # failed-producer signal. A value drift must NOT wake its resolver.
        if item.get("builtin"):
            monkeypatch.setitem(producers.BUILTIN, name, lambda home, now, **kw: "anon=1.00GiB charged=2.00GiB psi_avg10=0.00")
            producers.produce(tmp_path, name, now=NOW)
        assert f"signal:{name}" not in owed.check(tmp_path, resolver, now=NOW).reasons
        return
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

def test_budget_consistency_missing_input_does_not_leave_green(tmp_path):
    item = next(i for i in declared(tmp_path) if i["name"] == "budget-consistency")
    store.observe(tmp_path, item["name"], "GREEN", now=NOW, source="previous")
    assert producers.run(tmp_path, item["name"], item["command"], now=NOW + 1,
                         timeout=item["timeout"], red_codes=tuple(item["red_codes"]),
                         unknown_codes=tuple(item["unknown_codes"])) == 2
    assert store.read(tmp_path, item["name"], now=NOW + 1).state == "UNKNOWN"
    assert "signal:budget-consistency" in owed.check(tmp_path, "senses", now=NOW + 1).reasons


def test_readings_renewal_has_a_bounded_senses_owned_producer():
    item = next(d for d in ALL if d["name"] == "readings")
    assert item["resolver"] == "senses"
    assert item["command"][-2:] == ["discover", "scan"]
    assert item["every"] == 600 and item["timeout"] == 300
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


@pytest.mark.parametrize("name", ["devto-article-duplication", "pub-index-self-claims", "pub-account-count-claims"])
def test_devto_claim_checks_declare_the_unknown_exit(tmp_path, name):
    """A devto claim check exits 3 when the account cannot be read (e.g. HTTP 429).

    Each check documents ``0 GREEN / 2 RED / 3 UNKNOWN``, but the duty omitted the codes,
    so exit 3 was an *undeclared* code: ``producers.run`` recorded the value UNKNOWN yet
    returned 2, which failed the unit and minted a spurious
    ``failed-mishe-producer-<name>-service``. On 2026-11-16 a DEV.to 429 did exactly that
    for ``pub-account-count-claims``; the new failure signal then drifted the generated
    ``mishe-wake-pub.path`` and read doctor RED until the next ``mishe sync``. A declared
    UNKNOWN exit returns 0 (the unit succeeds, the signal ages to UNKNOWN) so a transient
    read failure is never a unit failure.
    """
    item = next(i for i in declared(tmp_path) if i["name"] == name)
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert producers.run(tmp_path, name, [PY, "-c", "import sys; sys.exit(3)"], now=NOW, timeout=10,
                         red_codes=tuple(item["red_codes"]),
                         unknown_codes=tuple(item["unknown_codes"])) == 0
    # A real disagreement (exit 2) is a RED observation, not a unit failure.
    assert producers.run(tmp_path, name, [PY, "-c", "import sys; sys.exit(2)"], now=NOW + 1, timeout=10,
                         red_codes=tuple(item["red_codes"]),
                         unknown_codes=tuple(item["unknown_codes"])) == 0
    assert store.read(tmp_path, name, now=NOW + 1).value == "RED"


def test_devto_watcher_routes_delivery_failure_red_and_scan_failure_unknown(tmp_path):
    """A failed append is RED; an unreadable scan only ages to UNKNOWN."""
    item = next(i for i in declared(tmp_path) if i["name"] == "devto")
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert producers.run(tmp_path, "devto", [PY, "-c", "import sys; sys.exit(3)"],
                         now=NOW, timeout=10, red_codes=(2,), unknown_codes=(3,)) == 0
    assert store.read(tmp_path, "devto", now=NOW).state == "UNKNOWN"
    assert producers.run(tmp_path, "devto", [PY, "-c", "import sys; sys.exit(2)"],
                         now=NOW + 1, timeout=10, red_codes=(2,), unknown_codes=(3,)) == 0
    assert store.read(tmp_path, "devto", now=NOW + 1).value == "RED"


def test_devto_watcher_delivery_duty_schedules_the_committed_check_against_the_plant():
    """The installed-watcher contract must be observed live, not only in a test.

    ``checks/pub-devto-watch-delivery.py`` reads the *installed* watcher
    (``{home}/pub/bin/devto-watch``) and requires exit 2 for a failed delivery and exit 3
    for an unreadable scan. That source is not in the committed tree, so the in-tree test
    skips under pytest (``tests/conftest.py`` strips ``MISHE_SEED_HOME``), and the delivery
    reviewer cannot see the bytes. Before this duty the check had no runner at all, so an
    edit to either the watcher or the devto duty could break the 2=RED / 3=UNKNOWN routing
    with every test green (2026-11-16: the delivery review of 25ddc765 returned verdict
    ``unknown`` for exactly this unobservable contract). The duty names the committed check
    through ``{repo}`` and passes ``--home {home}`` so it reads the real watcher.
    """
    item = next(d for d in ALL if d["name"] == "pub-devto-watch-delivery")
    assert item["resolver"] == "pub"
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    check = REPO / "checks" / "pub-devto-watch-delivery.py"
    assert check.is_file(), "the watcher contract must be a committed check"
    assert item["command"][:2] == ["{python}", "{repo}/checks/pub-devto-watch-delivery.py"]
    assert item["command"][2:4] == ["--home", "{home}"]



def test_ilya_bot_duty_runs_the_committed_detector_against_the_plant():
    """The bot's liveness must be observed by a duty, not by an untracked process.

    A destination that points at a site-local copy, a resolver other than health,
    or a missing RED/UNKNOWN routing would leave a dead bot invisible to Mishe.
    """
    item = next(d for d in ALL if d["name"] == "ilya-telegram-bot")
    assert item["resolver"] == "health"
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert (REPO / "checks" / "telegram-bot-alive.py").is_file()
    assert item["command"][:2] == ["{python}", "{repo}/checks/telegram-bot-alive.py"]
    assert item["command"][2:4] == ["--home", "{home}"]



def test_lzhezalia_duty_names_its_own_unit_and_heartbeat():
    """A copy of the Ilya duty that reuses the defaults would watch the wrong bot."""
    item = next(d for d in ALL if d["name"] == "lzhezalia-bot")
    assert item["resolver"] == "health"
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert item["command"][:2] == ["{python}", "{repo}/checks/telegram-bot-alive.py"]
    assert item["command"][2:4] == ["--home", "{home}"]
    assert item["command"][4:6] == ["--unit", "lzhezalia-bot.service"]
    assert item["command"][6:8] == ["--heartbeat", "{home}/health/lzhezalia-bot.json"]
    assert item["command"][8:10] == ["--state", "{home}/health/lzhezalia-bot-heal.json"]


def test_darinci_duty_names_its_own_unit_and_heartbeat():
    item = next(d for d in ALL if d["name"] == "darinci-bot")
    assert item["resolver"] == "health"
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert item["command"][:2] == ["{python}", "{repo}/checks/telegram-bot-alive.py"]
    assert item["command"][4:6] == ["--unit", "darinci-bot.service"]
    assert item["command"][6:8] == ["--heartbeat", "{home}/health/darinci-bot.json"]
    assert item["command"][8:10] == ["--state", "{home}/health/darinci-bot-heal.json"]
def test_privacy_scan_runs_on_a_timer_with_the_site_credentials():
    """The privacy scan had no duty, so its signal aged to UNKNOWN between hand runs.

    A duty that runs the scanner without the site credentials would read every item as
    unreadable, and a duty that forgot the RED and UNKNOWN exit codes would turn a
    violation into a GREEN. Both are pinned here: the credentials and the rule terms
    reach the scanner as absolute paths, and a scan that cannot read its input stays
    UNKNOWN.
    """
    item = next(d for d in ALL if d["name"] == "pub-privacy-scan")
    assert item["resolver"] == "pub"
    assert item["red_codes"] == [2] and item["unknown_codes"] == [3]
    assert item["command"][:2] == ["{python}", "{repo}/checks/pub-privacy-scan.py"]
    assert item["command"][2] == "--all"
    # Every site-local path is named explicitly, so the check never resolves one by
    # a rule of its own: the credentials, the rule terms, the report and the CLI.
    assert f"{{home}}/pub/access/devto.env" in item["command"]
    assert f"{{home}}/pub/privacy-terms.json" in item["command"]
    assert f"{{home}}/pub/privacy-scan.json" in item["command"]
    assert f"{{home}}/bin/mishe" in item["command"]
    # The scan paces its reads: a foreground run measures about 4.5 minutes, so the
    # duty's timeout must outlast it and its max_age must outlast one whole period.
    assert item["timeout"] >= 540 and item["every"] == 3600 and item["max_age"] == 7200


def test_privacy_scan_duty_runs_the_committed_check_not_a_site_local_copy():
    """The duty must run the check the repository ships, not a copy outside it.

    The scanner used to live at SITE_HOME/pub/bin/pub-privacy-scan, so the delivery
    gate's reviewer could not see its exit-code contract and refused the change on
    2026-11-14 with "insufficient context to assess deeper defects". A reviewer can
    only judge bytes it is given. The check therefore lives in checks/ and the duty
    names it through {repo}, which resolves to the delivered release worktree: the
    running bytes are the committed ones, and a revert restores the old pin.
    """
    item = next(d for d in ALL if d["name"] == "pub-privacy-scan")
    assert "{home}/pub/bin/pub-privacy-scan" not in " ".join(item["command"])
    check = REPO / "checks" / "pub-privacy-scan.py"
    assert check.is_file(), "the privacy scan must be a committed check"
    assert "{repo}/checks/pub-privacy-scan.py" in item["command"]


def test_privacy_scan_duty_yields_a_producer_unit_that_names_the_check(tmp_path):
    """The duty must generate a timer whose service runs the committed check.

    This is the mechanism the reviewer asked to see: a duty that produced no unit
    would leave the signal aging to UNKNOWN between hand runs, which is exactly the
    gap the duty was added to close. The rendered service names the check and the
    release it runs from, so the running bytes are the committed ones.
    """
    from mishe_tauftauf import producers, units

    item = next(d for d in ALL if d["name"] == "pub-privacy-scan")
    # A release worktree carries the committed manifest and the committed check,
    # so the duty is read from the same release its {repo} word names.
    source = tmp_path / "release"
    (source / "checks").mkdir(parents=True)
    (source / "checks" / "pub-privacy-scan.py").write_text("", encoding="utf-8")
    (source / "duties.json").write_text((REPO / "duties.json").read_text(encoding="utf-8"))
    # The real path: producers.load turns the declared duty into the producer the
    # unit renders, so the exit codes the bus reads are the ones the duty declares.
    loaded = producers.load(tmp_path / "home", python=PY,
                             declared=duties.declared(REPO, tmp_path / "home", source=source))
    producer = next(p for p in loaded if p.name == "pub-privacy-scan")
    cfg = units.Config(home=tmp_path / "home", repo=REPO, python=PY, pythonpath=str(source / "src"),
                       unit_dir=tmp_path / "units")
    rendered = units.render(cfg, ["pub"], producers=[producer])
    service = rendered["mishe-producer-pub-privacy-scan.service"]
    timer = rendered["mishe-producer-pub-privacy-scan.timer"]
    assert str(source / "checks" / "pub-privacy-scan.py") in service
    # The producer runs the check through `mishe signal run`, so the exit codes the
    # duty declares are the ones the bus reads.
    assert "--red-codes" in service and "--unknown-codes" in service
    assert "OnUnitActiveSec=3600" in timer
    assert f"Unit=mishe-producer-pub-privacy-scan.service" in timer


def test_privacy_scan_duty_expires_to_unknown_when_it_stops_running(tmp_path):
    """A duty that stops running must leave UNKNOWN, not a standing GREEN."""
    item = next(i for i in declared(tmp_path) if i["name"] == "pub-privacy-scan")
    assert producers.run(tmp_path, item["name"], [PY, "-c", "raise SystemExit(1)"], now=NOW, timeout=10) == 2
    assert store.read(tmp_path, item["name"], now=NOW).state == "UNKNOWN"
