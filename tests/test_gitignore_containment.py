"""The shared checkout must keep the reserved site-home namespace unstageable.

An impostor site home -- a directory named like a site home that is not one --
is invisible to Git, to the renderers and to every peer unless the checkout's
own ignore rule covers it. ``sense.site.ghost-homes`` reports such a tree once
it exists, but a report is not a containment rule: without the ignore rule the
typo'd tree can be staged and committed, and the loss is silent.

The earlier coverage wrote its own ``.mishe-*`` fixture into a tmp_path, so it
could not fail when the real checkout lost the rule: it exercised a fixture,
never the repository. This test reads the real checkout and asks Git which rule
decides the question, so it fails when the repository's own ignore file stops
covering the namespace.
"""

from pathlib import Path
import subprocess

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


def check_ignore(*paths: str) -> subprocess.CompletedProcess[str]:
    """Ask Git which ignore rule decides these paths, verbosely."""
    return subprocess.run(
        ["git", "-C", str(REPOSITORY), "check-ignore", "-v", "--no-index", *paths],
        capture_output=True, text=True, timeout=15)


@pytest.fixture(scope="module")
def checkout():
    if not (REPOSITORY / ".git").exists():
        pytest.skip("not a git checkout")
    return REPOSITORY


def test_reserved_site_home_namespace_is_ignored_by_the_real_checkout(checkout):
    """A path in the reserved namespace is decided by this repository's rule.

    The deciding rule must come from the checkout's own ignore file: a global
    excludes file would ignore the path on this host and nowhere else, so
    pinning that would pin the host, not the repository.
    """
    result = check_ignore(".mishe-impostor-home/walls/senses.md")

    assert result.returncode == 0, result.stdout + result.stderr
    source = result.stdout.split(":", 1)[0]
    assert Path(source).resolve() == (checkout / ".gitignore").resolve(), result.stdout


def test_an_ordinary_path_is_not_ignored_by_the_reserved_namespace_rule(checkout):
    """The reserved rule must stay narrow: an ordinary path is still stageable.

    A containment rule that matched everything would hide real work from the
    checkout, which is the opposite failure the rule exists to prevent.
    """
    result = check_ignore("src/ordinary-file.py")

    assert result.returncode == 1, result.stdout + result.stderr
