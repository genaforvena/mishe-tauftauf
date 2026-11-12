"""decisions() reads only permission decision lines, and the tape cache stays honest."""

import pytest

from mishe_tauftauf import access, feed as feed_module
from mishe_tauftauf.feed import Feed
from tests.records_binary import pin_records_binary  # noqa: F401  (fixture)
from tests.tape_binary import pin_tape_binary  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("pin_tape_binary", "pin_records_binary")


def _forget_parsed_tape():
    # Drop the in-process parsed revision so the next read goes back to the tape.
    with feed_module._ENTRIES_CACHE_LOCK:
        feed_module._ENTRIES_CACHE.clear()


def _site_with_permissions(tmp_path):
    Feed(tmp_path).append_record("witness", "The checked producer result is available for review.",
                                 {"checked": True})
    access.request(tmp_path, "scope-req-a", "discover", "render", "wall.write", ["walls/discover.md"], "render cost")
    access.request(tmp_path, "scope-req-b", "discover", "render", "wall.read", ["walls/health.md"], "read")
    access.decide(tmp_path, "scope-req-a", "granted")
    access.decide(tmp_path, "scope-req-b", "granted")
    access.decide(tmp_path, "scope-req-b", "revoked")
    return tmp_path


def _full_read_decisions(home):
    state = {}
    for entry in Feed(home).entries():
        if entry.source == "operator/permissions" and (match := access.DECISION_RE.match(entry.body)):
            state[match.group(1)] = match.group(2)
    return state


def test_decisions_match_a_full_verified_read(tmp_path):
    home = _site_with_permissions(tmp_path)
    assert access.decisions(home) == _full_read_decisions(home) == {
        "scope-req-a": "granted", "scope-req-b": "revoked"}


def test_decisions_do_not_depend_on_an_unrelated_record_payload(tmp_path):
    home = _site_with_permissions(tmp_path)
    _forget_parsed_tape()
    next((home / "records").glob("*.json")).unlink()
    # A verifying reader still fails closed on the missing record...
    with pytest.raises(ValueError, match="record"):
        Feed(home).entries()
    # ...while the permission reader does not consume that payload.
    assert access.decisions(home) == {"scope-req-a": "granted", "scope-req-b": "revoked"}


def test_unverified_read_never_serves_a_verifying_reader(tmp_path):
    home = _site_with_permissions(tmp_path)
    _forget_parsed_tape()
    # Populate the cache through the opt-out path, then damage the record it skipped.
    assert access.decisions(home)["scope-req-a"] == "granted"
    next((home / "records").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="record"):
        Feed(home).entries()


def test_verified_cache_still_serves_the_opt_out_reader(tmp_path):
    home = _site_with_permissions(tmp_path)
    Feed(home).entries()  # verified read populates the cache
    assert access.decisions(home) == {"scope-req-a": "granted", "scope-req-b": "revoked"}
    assert Feed(home).entries(verify_records=False)[-1] == Feed(home).entries()[-1]
