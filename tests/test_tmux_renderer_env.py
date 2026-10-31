"""The pane command carries the site home so renderers never report it unset."""
from pathlib import Path

from mishe_tauftauf import tmux


def test_python_command_names_site_home(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    command = tmux._python_command(home, "--home", str(home), "pain", "watch", "field-review")
    assert f"MISHE_SEED_HOME={home.resolve()}" in command
    # The env assignment precedes the interpreter so it reaches the renderer process.
    assert command.index(f"MISHE_SEED_HOME={home.resolve()}") < command.index("-m")
    assert command[0] == "env"
    assert command[-3:] == ["pain", "watch", "field-review"]
