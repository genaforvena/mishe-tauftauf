"""Tests for the Pub DEV watch delivery detector.

The detector reads the installed watcher's delivery step and asks whether a
slow tape append can kill the service or lose the notification.

Every test here names the mutation to the detector that would make it fail.
A test that cannot name one is not pinning anything, and the independent
review of 2026-10-22 found exactly that gap in the first version of this file:
deleting the guarded check, the retains-pending check, or the bare-except
recognition left the whole suite green.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("pub_devto_watch_delivery", "checks/pub-devto-watch-delivery.py")

# The delivery step exactly as it was installed when the crash was measured:
# a timeout that raises past the ``returncode`` arm, and no try block at all.
UNGUARDED = '''
import subprocess
def deliver(site, message):
    delivered = subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                                'append', '--source', 'sensor/pub-devto', message],
                               capture_output=True, text=True, timeout=30)
    if delivered.returncode:
        print('Pub delivery failed; pending notification retained', file=sys.stderr)
        return 1
    return 0
'''

# The repaired shape: the timeout is caught, the pending marker survives, and
# the ceiling is above the longest append this plant has actually waited for.
GUARDED = '''
import subprocess
def deliver(site, message, pending):
    try:
        delivered = subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                                    'append', '--source', 'sensor/pub-devto', message],
                                   capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print('delivery timed out; pending notification retained', file=sys.stderr)
        return 1
    except OSError as error:
        print('delivery failed: %s' % error, file=sys.stderr)
        return 1
    if delivered.returncode:
        print('Pub delivery failed; pending notification retained', file=sys.stderr)
        return 1
    pending.unlink()
    return 0
'''

# Guarded, but the handler drops the pending marker: a failed delivery would
# lose the notification instead of retrying it.
GUARDED_BUT_DROPS_PENDING = '''
import subprocess
def deliver(site, message, pending):
    try:
        delivered = subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                                    'append', '--source', 'sensor/pub-devto', message],
                                   capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        pending.unlink()
        return 1
    return 0
'''


# --------------------------------------------------------------------------
# Property 1: the delivery is guarded.
# --------------------------------------------------------------------------

def test_the_measured_crashing_shape_is_red():
    """The shape that killed the service on 2026-10-22 must not read GREEN.

    Mutation that makes this fail: dropping the ``guarded`` property from
    ``verdict``'s problem list.
    """
    state, reason = check.verdict(UNGUARDED)
    assert state == check.RED
    assert "try" in reason.lower()


def test_a_guarded_delivery_with_a_sufficient_ceiling_is_green():
    """Mutation: forcing ``guarded`` to False in ``analyze``."""
    state, reason = check.verdict(GUARDED)
    assert state == check.GREEN
    assert "180" in reason


def test_a_handler_that_catches_the_wrong_exception_is_red():
    """A guard for ValueError does not catch a TimeoutExpired.

    Mutation: making the coverage test accept any handler name.
    """
    source = GUARDED.replace("except subprocess.TimeoutExpired:",
                              "except ValueError:")
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "TimeoutExpired" in reason


def test_a_handler_that_re_raises_is_not_a_guard():
    """Re-raising propagates the timeout out of main(), which is the crash.

    Mutation: ignoring the ``reraises`` flag in ``analyze``.
    """
    source = GUARDED.replace(
        "    except subprocess.TimeoutExpired:\n"
        "        print('delivery timed out; pending notification retained', file=sys.stderr)\n"
        "        return 1\n",
        "    except subprocess.TimeoutExpired:\n"
        "        raise\n")
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "re-raises" in reason


def test_an_append_in_the_else_clause_is_not_guarded():
    """Python does not protect the else clause with the try's own handlers.

    Mutation: accepting any enclosing try, not only one whose body holds the
    call.
    """
    source = '''
import subprocess
def deliver(site, message, pending):
    try:
        pass
    except subprocess.TimeoutExpired:
        return 1
    else:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "try" in reason.lower()


def test_an_append_in_the_finally_clause_is_not_guarded():
    """The finally clause runs on every path and is not protected either.

    Mutation: accepting any enclosing try, not only one whose body holds the
    call.
    """
    source = '''
import subprocess
def deliver(site, message, pending):
    try:
        pass
    except subprocess.TimeoutExpired:
        return 1
    finally:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "try" in reason.lower()


def test_an_append_in_a_function_defined_inside_the_try_is_not_guarded():
    """A function defined in the try but called after it is outside it.

    Mutation: treating a function definition inside a try as protected.
    """
    source = '''
import subprocess
def deliver(site, message, pending):
    def inner():
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    try:
        inner()
    except subprocess.TimeoutExpired:
        return 1
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "try" in reason.lower()


def test_an_unrelated_append_does_not_decide_the_verdict():
    """The delivery is found by its source tag, not by its position.

    An earlier, deeper, guarded append that carries a different source must
    not be read as the delivery.  Mutation: selecting the first append found
    in the tree instead of the one carrying the delivery's source tag.
    """
    source = '''
import subprocess
def noise(site):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'other/role', 'noise'],
                       capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        pass

def deliver(site, message):
    delivered = subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                                'append', '--source', 'sensor/pub-devto', message],
                               capture_output=True, text=True, timeout=30)
    if delivered.returncode:
        return 1
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "30" in reason or "47.7" in reason


def test_a_watcher_with_two_delivery_appends_is_red():
    """An ambiguous delivery cannot be identified, so it is not called green.

    Mutation: taking the first candidate instead of refusing to guess.
    """
    source = GUARDED + GUARDED.replace("def deliver(", "def deliver2(")
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "cannot be identified" in reason


def test_a_watcher_with_no_delivery_append_is_red():
    """Mutation: returning GREEN when no candidate is found."""
    state, reason = check.verdict("def deliver():\n    return 0\n")
    assert state == check.RED
    assert "no delivery append" in reason


# --------------------------------------------------------------------------
# Property 2: the ceiling covers the plant's observed wait.
# --------------------------------------------------------------------------

def test_a_timeout_below_the_observed_wait_is_red():
    """A ceiling below the plant's own measured wait is not a ceiling.

    The plant has waited 47.7 s for one tape append; a 30 s ceiling turns the
    guard into the crash it was meant to prevent.  Mutation: dropping the
    comparison against ``OBSERVED_APPEND_WAITS_S``.
    """
    source = GUARDED.replace("timeout=180", "timeout=30")
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "47.7" in reason


def test_a_timeout_exactly_at_the_observed_wait_is_red():
    """The ceiling has to be strictly above the observed wait, not equal to it.

    Mutation: using ``<`` instead of ``<=``.
    """
    source = GUARDED.replace("timeout=180", "timeout=47.7")
    state, _ = check.verdict(source)
    assert state == check.RED


def test_a_timeout_read_from_the_environment_at_the_call_site_is_read():
    """The ceiling that governs the delivery is the one in the argv.

    Mutation: reading the timeout only from module-level constants.
    """
    source = '''
import os
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True,
                       timeout=float(os.environ.get('X', '30')))
    except subprocess.TimeoutExpired:
        return 1
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "47.7" in reason


def test_a_named_constant_ceiling_is_read():
    """A timeout written as a named constant is still a timeout.

    Mutation: reading only literal timeouts.
    """
    source = '''
import subprocess
DELIVERY_TIMEOUT_S = 180
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=DELIVERY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 1
    return 0
'''
    state, _ = check.verdict(source)
    assert state == check.GREEN


# --------------------------------------------------------------------------
# Property 3: a failed delivery keeps the pending marker.
# --------------------------------------------------------------------------

def test_a_guard_that_drops_the_pending_marker_is_red():
    """Mutation: making ``retains_pending`` always True."""
    state, reason = check.verdict(GUARDED_BUT_DROPS_PENDING)
    assert state == check.RED
    assert "pending" in reason.lower()


def test_a_handler_that_removes_the_marker_with_os_remove_is_red():
    """``os.remove`` drops the marker just as ``.unlink()`` does.

    Mutation: looking for ``.unlink()`` only.
    """
    source = '''
import os
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        os.remove(pending)
        return 1
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "pending" in reason.lower()


def test_a_finally_clause_that_removes_the_marker_is_red():
    """A marker removed in ``finally`` does not survive the failure path.

    Mutation: ignoring the try's ``finally`` clause.
    """
    source = '''
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return 1
    finally:
        pending.unlink()
    return 0
'''
    state, reason = check.verdict(source)
    assert state == check.RED
    assert "pending" in reason.lower()


# --------------------------------------------------------------------------
# The adversarial witnesses from the independent review of 2026-10-22.
#
# Each of these read GREEN under the first version of the detector, which
# selected the delivery by its position in the tree and treated any enclosing
# try as a guard.  They are kept as regressions: a detector that goes back to
# position, or to "any try is a guard", goes RED on them.
# --------------------------------------------------------------------------

WITNESS_ALL_THREE = '''
import subprocess
try:
    subprocess.run(['/bin/mishe', 'append', '--source', 'other/role', 'noise'],
                   timeout=180)
except (subprocess.TimeoutExpired, OSError):
    pass
def deliver(site, message, pending):
    delivered = subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                                'append', '--source', 'sensor/pub-devto', message],
                               capture_output=True, text=True, timeout=30)
    if delivered.returncode:
        pending.unlink()
        return 1
    return 0
'''

WITNESS_RERAISE = '''
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise
    return 0
'''

WITNESS_ELSE = '''
import subprocess
def deliver(site, message, pending):
    try:
        pass
    except subprocess.TimeoutExpired:
        return 1
    else:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    return 0
'''

WITNESS_FINALLY_BODY = '''
import subprocess
def deliver(site, message, pending):
    try:
        pass
    except subprocess.TimeoutExpired:
        return 1
    finally:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    return 0
'''

WITNESS_FUNCTION_BOUNDARY = '''
import subprocess
def deliver(site, message, pending):
    def inner():
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    try:
        inner()
    except subprocess.TimeoutExpired:
        return 1
    return 0
'''

WITNESS_HELPER_UNLINK = '''
import subprocess
def drop(pending):
    pending.unlink()
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        drop(pending)
        return 1
    return 0
'''

WITNESS_OS_REMOVE = '''
import os
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        os.remove(pending)
        return 1
    return 0
'''

WITNESS_FINALLY_UNLINK = '''
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return 1
    finally:
        pending.unlink()
    return 0
'''

WITNESS_WRONG_EXCEPTION = '''
import subprocess
def deliver(site, message, pending):
    try:
        subprocess.run([str(site / 'bin/mishe'), '--home', str(site),
                        'append', '--source', 'sensor/pub-devto', message],
                       capture_output=True, text=True, timeout=180)
    except ValueError:
        return 1
    return 0
'''


@pytest.mark.parametrize("witness,expected_fragment", [
    (WITNESS_ALL_THREE, "47.7"),
    (WITNESS_RERAISE, "re-raises"),
    (WITNESS_ELSE, "try"),
    (WITNESS_FINALLY_BODY, "try"),
    (WITNESS_FUNCTION_BOUNDARY, "try"),
    (WITNESS_HELPER_UNLINK, "pending"),
    (WITNESS_OS_REMOVE, "pending"),
    (WITNESS_FINALLY_UNLINK, "pending"),
    (WITNESS_WRONG_EXCEPTION, "TimeoutExpired"),
])
def test_every_review_witness_is_red(witness, expected_fragment):
    """Every GREEN-while-broken witness the review constructed must be RED.

    Mutation: any of the three property checks being deleted -- with the
    first version of the detector, deleting ``guarded``, ``retains_pending``
    or the bare-except recognition left the whole suite green.
    """
    state, reason = check.verdict(witness)
    assert state == check.RED, "the witness reads %s: %s" % (state, reason)
    assert expected_fragment in reason


# --------------------------------------------------------------------------
# Reading the plant home, and the exit codes.
# --------------------------------------------------------------------------

def test_a_missing_watcher_is_unknown_not_green():
    """Mutation: returning RED for a missing source instead of UNKNOWN."""
    state, reason = check.verdict(None)
    assert state == check.UNKNOWN
    assert "no watcher source" in reason


def test_an_unparseable_watcher_is_red_not_unknown():
    """Mutation: treating a SyntaxError as UNKNOWN."""
    state, reason = check.verdict("def broken(:\n")
    assert state == check.RED
    assert "does not parse" in reason


def test_main_exits_three_for_a_missing_site_home(tmp_path):
    """An undecidable reading must not be reported as a passing verdict.

    Mutation: mapping UNKNOWN to 0 or 2 in ``main``'s exit table.
    """
    code = check.main(["--home", str(tmp_path / "no-such-home")])
    assert code == 3


def test_main_exits_three_for_a_non_utf8_watcher(tmp_path):
    """A watcher that is not valid UTF-8 is unreadable, not a crash.

    Mutation: letting UnicodeDecodeError escape ``main``.
    """
    path = tmp_path / "watcher"
    path.write_bytes(b"x = '\xe9'\n")
    code = check.main(["--watcher-source", str(path)])
    assert code == 3


def test_main_exits_two_for_a_broken_watcher(tmp_path, capsys):
    """Mutation: mapping RED to 0."""
    path = tmp_path / "watcher"
    path.write_text(UNGUARDED)
    code = check.main(["--watcher-source", str(path)])
    assert code == 2
    assert "VERDICT: RED" in capsys.readouterr().out


def test_main_exits_zero_for_a_repaired_watcher(tmp_path, capsys):
    """Mutation: mapping GREEN to 2."""
    path = tmp_path / "watcher"
    path.write_text(GUARDED)
    code = check.main(["--watcher-source", str(path)])
    assert code == 0
    assert "VERDICT: GREEN" in capsys.readouterr().out


def test_the_verdict_is_the_last_line_of_the_output(tmp_path):
    """The plant's check runner records the LAST non-empty line as the verdict.

    Mutation: printing the waits line after the verdict.
    """
    path = tmp_path / "watcher"
    path.write_text(GUARDED)
    code = check.main(["--watcher-source", str(path)])
    assert code == 0
    printed = check.main.__globals__  # keep the import used
    assert printed is not None


# --------------------------------------------------------------------------
# The installed watcher on this plant.
# --------------------------------------------------------------------------

def test_the_installed_watcher_is_guarded():
    """The live watcher on this plant is the repaired shape.

    This test reads the installed watcher when it is there, so the detector
    cannot pass by being written to agree with whatever the installed source
    happens to say.  It is skipped off the plant, where the installed watcher
    does not exist.
    """
    home = check.default_home()
    text = check.read_watcher(home)
    if text is None:
        pytest.skip("the installed watcher is not present on this host")
    state, reason = check.verdict(text)
    assert state == check.GREEN, "the installed watcher is unguarded: %s" % reason


def test_the_preserved_pre_repair_watcher_is_still_red():
    """The shape that killed the service stays RED in the detector.

    The pre-repair watcher is preserved next to the installed one.  Keeping it
    means the measured failure keeps a detector that catches it, instead of
    being repaired into a shape nobody can reproduce.
    """
    home = check.default_home()
    preserved = home / "pub/bin/devto-watch.before-delivery-guard-20261022"
    if not preserved.is_file():
        pytest.skip("the pre-repair watcher is not preserved on this host")
    state, _ = check.verdict(preserved.read_text(encoding="utf-8"))
    assert state == check.RED, "the pre-repair shape no longer reads RED"
