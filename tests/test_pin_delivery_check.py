"""Controlled-failure tests for checks/pin-delivery.py (run from the committed check file).

The check is stdlib-only until it re-observes the delivery latch, and those tests import the
package their run pinned (``env PYTHONPATH=src``), so the latch assertions use the tree under test.
"""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

CHECK = Path(__file__).resolve().parents[1] / "checks" / "pin-delivery.py"
spec = importlib.util.spec_from_file_location("pin_delivery_check", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 1_000_000.0
SHA = "a" * 40


def _site(tmp_path, records, pointer=SHA):
    (tmp_path / "work" / "releases").mkdir(parents=True)
    for i, record in enumerate(records):
        (tmp_path / "work" / "releases" / f"{i}.json").write_text(json.dumps(record), encoding="utf-8")
    if pointer:
        package = tmp_path / "releases" / pointer / "src" / "mishe_tauftauf"
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
        (package / "__main__.py").write_text("print('ok')\n", encoding="utf-8")
        (tmp_path / "runtime").mkdir()
        (tmp_path / "runtime" / "current").symlink_to(tmp_path / "releases" / pointer)
    return tmp_path


def _run(home, capsys, **extra):
    argv = ["--home", str(home), "--now", str(NOW)]
    code = check.main(argv)
    return code, capsys.readouterr()


def _rec(phase, **over):
    base = {"sha": SHA, "phase": phase, "created_at": NOW - 600, "paths": ["a"]}
    base.update(over)
    return base


def test_delivered_and_pointer_agrees_is_green(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("delivered")]), capsys)
    assert code == 0 and "delivered" in out.out


def test_delivered_but_pointer_elsewhere_is_unknown(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("delivered")], pointer="b" * 40), capsys)
    assert code == 3 and "runtime/current names" in out.err


def test_delivered_age_counts_from_delivery_not_promotion_start(tmp_path, capsys):
    """A slow promotion must not make the pin look older than it is (2026-11-18 05:52Z).

    The 2b5c27f5 promotion started 05:20Z and the pin moved at 05:51:56Z; the check still read
    "delivered 32 min ago" for a pin that had named the release for one minute.
    """
    rec = _rec("delivered", created_at=NOW - 600, delivered_at=NOW - 60)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 0 and "delivered 1 min ago" in out.out


def test_delivered_detail_shows_each_stage_duration(tmp_path, capsys):
    """The standing runtime question is answerable from the signal, not a raw-record dig."""
    rec = _rec("delivered", waiting_since=NOW - 540, activation_started_at=NOW - 120,
               delivered_at=NOW - 60)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 0 and "check 1m, wait 7m, activate 1m" in out.out


def test_failed_check_is_red_and_names_the_failing_test(tmp_path, capsys):
    rec = _rec("check-failed", check={"output": "..\nFAILED tests/test_x.py::test_y - boom\n1 failed"})
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 2 and "check-failed" in out.err and "tests/test_x.py::test_y" in out.err


def test_rolled_back_is_red_with_the_recorded_failure(tmp_path, capsys):
    code, out = _run(_site(tmp_path, [_rec("rolled-back", failure="functional probe failed")]), capsys)
    assert code == 2 and "functional probe failed" in out.err


def test_delivery_busy_is_red_and_names_the_failure(tmp_path, capsys):
    # The promote writes delivery-busy only as it gives up, so a delivered run is not in flight.
    rec = _rec("delivery-busy", failure="dispatch lock still held after a 2400s activation hold")
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 2 and "activation hold" in out.err


def test_only_the_newest_record_counts(tmp_path, capsys):
    old = _rec("check-failed", created_at=NOW - 7200)
    new = _rec("delivered", created_at=NOW - 60)
    assert _run(_site(tmp_path, [old, new]), capsys)[0] == 0


def _latch(home):
    """The site's moving-pin-delivery registration and a 8000s-old VERIFIED reading (past max_age)."""
    from mishe_tauftauf import signal_store
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    signal_store.observe(home, "moving-pin-delivery", "VERIFIED", now=NOW - 8000,
                         source="release_promotion", event=SHA + ":delivered")
    return signal_store


def test_a_delivered_reading_refreshes_the_latch_and_keeps_its_value(tmp_path, capsys):
    """A quiet pin must not age the delivery latch to a false UNKNOWN (2026-11-17, fc834468)."""
    home = _site(tmp_path, [_rec("delivered")])
    signal_store = _latch(home)
    stale = signal_store.read(home, "moving-pin-delivery", now=NOW)
    assert stale.state == "UNKNOWN", "the fixture must start past max_age, or it proves nothing"
    stored = (home / "signals" / "moving-pin-delivery.json").read_bytes()

    code, out = _run(home, capsys)

    assert code == 0 and "delivered" in out.out
    reading = signal_store.read(home, "moving-pin-delivery", now=NOW)
    assert reading.state == "OK" and reading.value == "VERIFIED"
    assert (home / "signals" / "moving-pin-delivery.json").read_bytes() == stored, \
        "a refresh must not rewrite the reading (that would wake every subscriber)"
    assert (home / "signals" / "moving-pin-delivery.detail").exists() is False


def test_a_reading_the_sweep_turned_unknown_is_restored(tmp_path, capsys):
    """The sweep's UNKNOWN is not permanent: the record still says the outcome (2026-11-17 15:01:42Z)."""
    home = _site(tmp_path, [_rec("delivered")])
    signal_store = _latch(home)
    signal_store.observe_unknown(home, "moving-pin-delivery", now=NOW,
                                 reason="expired after 7200s without observation", source="release_promotion")
    assert signal_store.read(home, "moving-pin-delivery", now=NOW).state == "UNKNOWN"

    code, out = _run(home, capsys)

    assert code == 0 and "delivered" in out.out
    stored = signal_store.recorded(home, "moving-pin-delivery")
    assert stored["state"] == "OK" and stored["value"] == "VERIFIED"
    assert stored["event"] == SHA + ":delivered", "the restored reading keeps the promotion's event"


def test_a_latch_that_was_never_observed_stays_unobserved(tmp_path, capsys):
    """A refresh creates no reading: a missing monitor must still age to UNKNOWN."""
    home = _site(tmp_path, [_rec("delivered")])
    from mishe_tauftauf import signal_store
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])

    assert _run(home, capsys)[0] == 0

    assert signal_store.read(home, "moving-pin-delivery", now=NOW).state == "UNKNOWN"
    assert not (home / "signals" / "moving-pin-delivery.seen").exists()


def test_a_pointer_that_disagrees_does_not_refresh_the_latch(tmp_path, capsys):
    """Only a verified reading refreshes: an UNKNOWN must not be kept warm."""
    home = _site(tmp_path, [_rec("delivered")], pointer="b" * 40)
    _latch(home)

    assert _run(home, capsys)[0] == 3

    assert abs((home / "signals" / "moving-pin-delivery.seen").stat().st_mtime - (NOW - 8000)) < 1


def test_a_matching_pointer_without_its_package_is_red(tmp_path, capsys):
    home = _site(tmp_path, [_rec("delivered")])
    shutil.rmtree(home / "releases" / SHA / "src" / "mishe_tauftauf")
    signal_store = _latch(home)

    code, output = _run(home, capsys)

    assert code == 2
    assert "runtime package is missing" in output.err
    assert signal_store.read(home, "moving-pin-delivery", now=NOW).state == "UNKNOWN"


def test_a_matching_pointer_without_its_entry_point_is_red(tmp_path, capsys):
    home = _site(tmp_path, [_rec("delivered")])
    (home / "releases" / SHA / "src" / "mishe_tauftauf" / "__main__.py").unlink(missing_ok=True)

    code, output = _run(home, capsys)

    assert code == 2
    assert "__main__.py is missing" in output.err


def test_an_empty_entry_point_is_red_not_green(tmp_path, capsys):
    home = _site(tmp_path, [_rec("delivered")])
    (home / "releases" / SHA / "src" / "mishe_tauftauf" / "__main__.py").write_text("\n  \n", encoding="utf-8")

    code, output = _run(home, capsys)

    assert code == 2
    assert "__main__.py is missing" in output.err


@pytest.mark.parametrize("source", ["# only a comment\n", "# one\n# two\n", '"""just a docstring"""\n', "def (:\n"])
def test_an_entry_point_that_runs_nothing_is_red_not_green(tmp_path, capsys, source):
    home = _site(tmp_path, [_rec("delivered")])
    (home / "releases" / SHA / "src" / "mishe_tauftauf" / "__main__.py").write_text(source, encoding="utf-8")

    code, output = _run(home, capsys)

    assert code == 2
    assert "__main__.py is missing" in output.err


def test_an_entry_point_with_a_statement_after_a_comment_is_green(tmp_path, capsys):
    home = _site(tmp_path, [_rec("delivered")])
    (home / "releases" / SHA / "src" / "mishe_tauftauf" / "__main__.py").write_text(
        "# entry\nimport sys\nsys.exit(0)\n", encoding="utf-8")

    code, _output = _run(home, capsys)

    assert code == 0


@pytest.mark.parametrize("target_kind", ["missing", "file", "loop"])
def test_an_unusable_pointer_is_unknown_and_does_not_refresh_the_latch(tmp_path, capsys, target_kind):
    home = _site(tmp_path, [_rec("delivered")])
    target = home / "releases" / SHA
    shutil.rmtree(target)
    if target_kind == "file":
        target.touch()
    elif target_kind == "loop":
        target.symlink_to(target.name)
    signal_store = _latch(home)

    code, output = _run(home, capsys)

    assert code == 3
    assert "runtime/current" in output.err
    assert signal_store.read(home, "moving-pin-delivery", now=NOW).state == "UNKNOWN"


def test_running_is_green_until_stuck_then_unknown(tmp_path, capsys):
    assert _run(_site(tmp_path, [_rec("checking", created_at=NOW - 300)]), capsys)[0] == 0
    stuck = tmp_path / "b"
    stuck.mkdir()
    assert _run(_site(stuck, [_rec("checking", created_at=NOW - 7200)]), capsys)[0] == 3


@pytest.mark.parametrize("phase", ["checking", "waiting-for-wakes", "activating"])
def test_in_flight_promotion_detects_damage_to_the_current_pin(tmp_path, capsys, phase):
    candidate = "b" * 40
    promotion = _rec(phase, sha=candidate, previous=SHA, created_at=NOW - 300)
    home = _site(tmp_path, [_rec("delivered"), promotion])
    target = home / "releases" / SHA
    assert _run(home, capsys)[0] == 0

    shutil.rmtree(target / "src" / "mishe_tauftauf")
    code, output = _run(home, capsys)
    assert code == 2
    assert f"{candidate[:8]} is {phase}" in output.err
    assert f"runtime package is missing from {target}" in output.err

    shutil.rmtree(target)
    code, output = _run(home, capsys)
    assert code == 3
    assert "runtime/current is unusable" in output.err


def test_in_flight_activation_accepts_the_pin_after_the_flip(tmp_path, capsys):
    code, output = _run(_site(tmp_path, [_rec("activating", previous="b" * 40)]), capsys)
    assert code == 0 and "activating" in output.out


def test_a_long_check_does_not_age_the_wait_that_follows_it(tmp_path, capsys):
    """The check stage can take 25 min and a healthy wait for the dispatch lock 50 more.

    Measured from ``created_at`` a live promotion passed --stuck-min 60 and read UNKNOWN for
    waiting on wakes that had to finish first (2026-11-18, the 632f743e cadence promotion).
    """
    rec = _rec("waiting-for-wakes", created_at=NOW - 5400, waiting_since=NOW - 300)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 0 and "waiting-for-wakes (5 min)" in out.out


def test_a_wait_that_overran_its_own_start_is_unknown(tmp_path, capsys):
    rec = _rec("waiting-for-wakes", created_at=NOW - 9000, waiting_since=NOW - 5400)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 3 and "has been waiting-for-wakes for 90 min" in out.err


def test_activation_is_aged_from_its_own_start(tmp_path, capsys):
    rec = _rec("activating", created_at=NOW - 9000, activation_started_at=NOW - 120)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 0 and "activating (2 min)" in out.out


def test_a_legacy_running_record_without_a_stamp_keeps_the_old_reading(tmp_path, capsys):
    rec = _rec("waiting-for-wakes", created_at=NOW - 5400)
    code, out = _run(_site(tmp_path, [rec]), capsys)
    assert code == 3 and "has been waiting-for-wakes for 90 min" in out.err


def test_no_record_is_unknown(tmp_path, capsys):
    (tmp_path / "work" / "releases").mkdir(parents=True)
    assert _run(tmp_path, capsys)[0] == 3


def test_a_corrupt_newest_record_is_unknown_not_green(tmp_path, capsys):
    """A record that cannot be parsed is not skipped in silence.

    This is the defect the review of the 2026-11-14 release named on
    2026-11-15: ``newest()`` skipped any record that failed to parse, so a
    corrupt or torn record was passed over and an OLDER record answered
    instead.  With the older record saying "delivered" and the pointer
    naming that same sha, the check reported GREEN -- a healthy pin, while
    the record of the promotion that actually happened was unreadable.

    The newest promotion is then UNKNOWN.  Which record is newest cannot be
    known from a file that does not parse, so the check must not pass on the
    ones it can read.
    """
    home = _site(tmp_path, [_rec("delivered", created_at=NOW - 7200)])
    (home / "work" / "releases" / "9.json").write_text(
        '{"sha": "bbbb", "phase": "delivered", "created_at":', encoding="utf-8")
    code, out = _run(home, capsys)
    assert code == 3
    assert "could not be read" in out.err


def test_a_corrupt_record_alongside_a_good_newest_is_unknown(tmp_path, capsys):
    """A corrupt record is UNKNOWN even when a readable record is newer.

    A corrupt record cannot be ordered against a readable one, so the
    check cannot claim the readable one is the newest promotion.
    """
    home = _site(tmp_path, [_rec("delivered", created_at=NOW - 60)])
    (home / "work" / "releases" / "9.json").write_text("{not json", encoding="utf-8")
    code, out = _run(home, capsys)
    assert code == 3
    assert "could not be read" in out.err


@pytest.mark.parametrize("malformed", [
    [],
    {},
    {"created_at": "later"},
    {"created_at": True},
    {"created_at": float("nan")},
    {"created_at": float("inf")},
    {"created_at": 10**400},
], ids=["non-object", "missing-time", "string-time", "boolean-time",
        "nan-time", "infinite-time", "oversized-time"])
def test_an_unorderable_record_makes_an_earlier_delivery_unknown(tmp_path, capsys, malformed):
    home = _site(tmp_path, [_rec("delivered"), malformed])
    signal_store = _latch(home)

    code, out = _run(home, capsys)

    assert code == 3
    assert "1.json could not be read" in out.err
    assert signal_store.read(home, "moving-pin-delivery", now=NOW).state == "UNKNOWN"
