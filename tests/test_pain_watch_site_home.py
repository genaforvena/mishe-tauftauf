"""A pain-watch renderer binds the site home it renders, even when its launcher omitted the env."""
import pytest

from pathlib import Path

from mishe_tauftauf import cli


@pytest.fixture
def unbound(monkeypatch):
    # setenv then delenv records the variable, so teardown removes any value the code under test set.
    monkeypatch.setenv("MISHE_SEED_HOME", "placeholder")
    monkeypatch.delenv("MISHE_SEED_HOME")
    return monkeypatch


def test_unset_env_takes_renderer_home(tmp_path: Path, unbound) -> None:
    home = tmp_path / "site"
    home.mkdir()
    cli._bind_renderer_home(home)
    assert cli.os.environ["MISHE_SEED_HOME"] == str(home.resolve())


def test_existing_binding_is_kept_so_mismatch_stays_visible(tmp_path: Path, monkeypatch) -> None:
    bound = tmp_path / "bound"
    other = tmp_path / "other"
    bound.mkdir()
    other.mkdir()
    monkeypatch.setenv("MISHE_SEED_HOME", str(bound))
    cli._bind_renderer_home(other)
    assert cli.os.environ["MISHE_SEED_HOME"] == str(bound)


def test_pain_watch_binds_home_before_rendering(tmp_path: Path, unbound) -> None:
    home = tmp_path / "site"
    home.mkdir()
    seen = {}

    def stop(*_args, **_kwargs):
        seen["env"] = cli.os.environ.get("MISHE_SEED_HOME")
        raise KeyboardInterrupt

    unbound.setattr(cli, "compose_frame", stop)
    args = type("A", (), {"slug": "field-review", "home": home, "timeout": None, "interval": 5.0})()
    assert cli.cmd_pain_watch(args) == 0
    assert seen["env"] == str(home.resolve())
