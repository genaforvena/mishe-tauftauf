"""run_check('health') must serialise on the same lock as the health producer (Witness 58cee7a3 objection)."""
import fcntl

import pytest

from mishe_tauftauf import checks, seed_culture_views
from mishe_tauftauf.checks import HealthPublishBusy, run_check


def test_lock_name_matches_health_producer():
    assert checks.HEALTH_REFRESH_LOCK == seed_culture_views.HEALTH_REFRESH_LOCK


def test_health_check_publishes_when_lock_free(tmp_path):
    path = run_check(tmp_path, "health", ["true"])
    assert path == tmp_path / "observations" / "health"
    assert "exit: 0" in path.read_text(encoding="utf-8")


def test_health_check_refused_while_lock_held(tmp_path):
    lock_path = tmp_path / "observations" / checks.HEALTH_REFRESH_LOCK
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(HealthPublishBusy):
            run_check(tmp_path, "health", ["true"])
    assert not (tmp_path / "observations" / "health").exists()


def test_other_slugs_do_not_take_health_lock(tmp_path):
    lock_path = tmp_path / "observations" / checks.HEALTH_REFRESH_LOCK
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = run_check(tmp_path, "witness", ["true"])
    assert path.name == "witness"
