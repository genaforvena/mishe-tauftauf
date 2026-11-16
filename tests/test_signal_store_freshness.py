"""The signal store's expiry sweep reads freshness through mishe-signal-freshness.

A configured binary is authoritative; a broken one fails closed to UNKNOWN. With no binary
configured at all, the pre-existing Python comparison remains the offline/test oracle.
"""

import pytest

from mishe_tauftauf import signal_store as store


@pytest.fixture
def offline(monkeypatch):
    """No binary configured anywhere: the isolated/test context, not a plant."""
    monkeypatch.delenv("MISHE_SIGNAL_FRESHNESS_BIN", raising=False)
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)


def _fake_bin(tmp_path, code):
    path = tmp_path / f"fresh-{code}.sh"
    path.write_text(f"#!/bin/sh\nexit {code}\n")
    path.chmod(0o755)
    return str(path)


def _register_ok(home, *, max_age=30.0, now=1000.0):
    store.register(home, "probe", max_age=max_age, subscribers=[], now=now)
    store.observe(home, "probe", "GREEN", now=now, source="t")


def test_configured_binary_fresh_keeps_ok(offline, tmp_path):
    home = tmp_path / "home"
    _register_ok(home)
    assert store.expire(home, now=1031.0, binary=_fake_bin(tmp_path, 0)) == []
    assert store.recorded(home, "probe")["state"] == store.OK


def test_configured_binary_expired_turns_unknown(offline, tmp_path):
    home = tmp_path / "home"
    _register_ok(home)
    assert store.expire(home, now=1031.0, binary=_fake_bin(tmp_path, 1)) == ["probe"]
    recorded = store.recorded(home, "probe")
    assert recorded["state"] == store.UNKNOWN
    assert recorded["reason"] == "expired after 30s without observation"


def test_configured_binary_unknown_fails_closed(offline, tmp_path):
    home = tmp_path / "home"
    _register_ok(home)
    assert store.expire(home, now=1031.0, binary=_fake_bin(tmp_path, 2)) == ["probe"]
    recorded = store.recorded(home, "probe")
    assert recorded["state"] == store.UNKNOWN
    assert "freshness verdict unavailable" in recorded["reason"]


def test_configured_but_missing_binary_fails_closed(offline, tmp_path):
    home = tmp_path / "home"
    _register_ok(home)
    assert store.expire(home, now=1001.0, binary=str(tmp_path / "absent")) == ["probe"]
    assert "freshness verdict unavailable" in store.recorded(home, "probe")["reason"]


def test_site_home_without_install_fails_closed(offline, tmp_path, monkeypatch):
    """A plant site home means a binary is expected: a missing install is UNKNOWN, not fresh."""
    home = tmp_path / "home"
    _register_ok(home)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path / "no-install"))
    assert store.expire(home, now=1001.0) == ["probe"]
    assert "freshness verdict unavailable" in store.recorded(home, "probe")["reason"]


def test_offline_without_binary_keeps_python_comparison(offline, tmp_path):
    home = tmp_path / "home"
    _register_ok(home)
    assert store.expire(home, now=1031.0) == ["probe"]
    recorded = store.recorded(home, "probe")
    assert recorded["state"] == store.UNKNOWN
    assert recorded["reason"] == "expired after 30s without observation"


def test_env_override_is_used(offline, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _register_ok(home)
    monkeypatch.setenv("MISHE_SIGNAL_FRESHNESS_BIN", _fake_bin(tmp_path, 0))
    assert store.expire(home, now=1031.0) == []
