"""Deterministic fixtures for ``sense.site.ghost-homes``.

The detector names site-home literals that reached a checkout root instead of
a site home. Two name shapes reach it: the reserved ``.mishe-`` prefix, and an
unexpanded site-home shell variable such as ``$MISHE_SEED_HOME``. A reserved
name that is not a directory is named as a stray: it cannot become a site
home, so the verdict does not move, but the sample must still say so.

These fixtures live in their own file because the shared
``tests/test_discovery.py`` carries other roles' uncommitted work; a reader can
check this coverage without touching a peer-dirty path.
"""

from pathlib import Path


def _checkout(tmp_path: Path) -> Path:
    """A checkout root holding this site's own home."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".mishe-tauftauf").mkdir()
    return checkout


def test_ghost_site_homes_detects_a_literal_site_home_variable_directory(tmp_path):
    """A directory named after an unexpanded ``$MISHE_SEED_HOME`` is a site-home
    literal: it carries no reserved prefix, so a prefix rule never sees it,
    and the ignore rule does not cover it either, so Git stages it."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    literal = checkout / "$MISHE_SEED_HOME"
    (literal / "walls").mkdir(parents=True)
    (literal / "walls" / "genome.md").write_text("wall text\n", encoding="utf-8")

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "drift"
    assert reading["ghosts"] == ["$MISHE_SEED_HOME"]
    assert reading["files"] == {"$MISHE_SEED_HOME": 1}
    assert "$MISHE_SEED_HOME" in reading["sample"]


def test_ghost_site_homes_detects_the_plant_home_variable_too(tmp_path):
    """The other site-home variable is the same shape and is detected the same
    way; the rule is the literal variable, not one spelling of it."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    literal = checkout / "$MISHE_TAUFTAUF_HOME"
    literal.mkdir()

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "drift"
    assert reading["ghosts"] == ["$MISHE_TAUFTAUF_HOME"]


def test_ghost_site_homes_leaves_ordinary_root_directories_alone(tmp_path):
    """A checkout root holds many directories that are not site homes; none of
    them may be reported, or the detector is noise."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    for name in ("docs", "src", "tools", "artifacts", "coordination"):
        (checkout / name).mkdir()

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "verified"
    assert reading["ghosts"] == []
    assert "ghost-homes=0" in reading["sample"]


def test_ghost_site_homes_names_a_reserved_non_directory_and_keeps_the_verdict(tmp_path):
    """A reserved name that is not a directory cannot become a site home, so the
    verdict does not move -- but the sample must change when it appears and
    when it is removed, so a reader cannot take the sample as covering names
    it never mentioned."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    stray = checkout / ".mishe-tauftauf-notes.txt"
    stray.write_text("note text\n", encoding="utf-8")

    with_stray = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert with_stray["state"] == "verified"
    assert with_stray["ghosts"] == []
    assert with_stray["stray"] == [".mishe-tauftauf-notes.txt"]
    assert "stray=.mishe-tauftauf-notes.txt" in with_stray["sample"]

    stray.unlink()
    without_stray = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert without_stray["state"] == with_stray["state"] == "verified"
    assert without_stray["stray"] == []
    assert "stray=" not in without_stray["sample"]
    assert without_stray["sample"] != with_stray["sample"]


def test_ghost_site_homes_names_a_literal_variable_file_as_a_stray(tmp_path):
    """The literal variable can also land as a file. It is a stray name, not a
    site home: the verdict stays put and the name is still published."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    (checkout / "$MISHE_SEED_HOME").write_text("note text\n", encoding="utf-8")

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "verified"
    assert reading["ghosts"] == []
    assert reading["stray"] == ["$MISHE_SEED_HOME"]
    assert "stray=$MISHE_SEED_HOME" in reading["sample"]


def test_ghost_site_homes_is_unknown_when_the_checkout_enumeration_is_truncated(
        tmp_path, monkeypatch):
    """A truncated enumeration that examined no ghost tree cannot claim a clean
    zero, so the reading is UNKNOWN and says what it could not establish."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    for index in range(4):
        (checkout / f"dir{index}").mkdir()
    monkeypatch.setattr(discovery, "GHOST_SCAN_LIMIT", 2)

    reading = discovery._ghost_site_homes(checkout / ".mishe-tauftauf")

    assert reading["state"] == "unknown"
    assert "ghost absence not established" in reading["sample"]
