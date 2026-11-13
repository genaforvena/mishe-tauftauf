"""The site CLI wrappers must work from any working directory.

2026-10-31: bin/mishe exported a PYTHONPATH relative to the repo root, so every
systemd alert router (no WorkingDirectory) failed `ModuleNotFoundError` and the
alerts about failed monitors never reached Health.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from mishe_tauftauf.runtime_source import refresh_cli

REPO = Path(__file__).resolve().parents[1]


def load_check():
    spec = importlib.util.spec_from_file_location("cli_wrapper_cwd", REPO / "checks" / "cli-wrapper-cwd.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_release(root: Path) -> Path:
    release = root / "release"
    (release / "src" / "mishe_tauftauf").mkdir(parents=True)
    (release / "src" / "mishe_tauftauf" / "__init__.py").write_text("")
    (release / "src" / "mishe_tauftauf" / "__main__.py").write_text("print('ok')\n")
    return release


def test_refresh_cli_writes_absolute_root_even_from_a_relative_release(tmp_path, monkeypatch):
    release = make_release(tmp_path)
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    wrapper = home / "bin" / "mishe"
    wrapper.write_text('#!/bin/sh\nexport PYTHONPATH=/old/src${PYTHONPATH:+:$PYTHONPATH}\nexec /usr/bin/python3 -m mishe_tauftauf "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    refresh_cli(home, Path("release"))                       # relative, as a pin tool once passed it
    line = wrapper.read_text().splitlines()[1]
    assert f"PYTHONPATH={release.resolve() / 'src'}" in line
    out = subprocess.run([str(wrapper)], cwd="/", capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    assert out.returncode == 0 and out.stdout.strip() == "ok"   # works from /


def test_detector_flags_a_cwd_dependent_wrapper_and_passes_a_good_one(tmp_path):
    check = load_check()
    release = make_release(tmp_path)
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    good = f'#!/bin/sh\nexport PYTHONPATH={release / "src"}\nexec /usr/bin/python3 -m mishe_tauftauf "$@"\n'
    for name in ("mishe", "mishe-tauftauf"):
        (home / "bin" / name).write_text(good)
        (home / "bin" / name).chmod(0o755)
    assert check.evaluate(home)[0] == 0
    (home / "bin" / "mishe").write_text(good.replace(str(release / "src"), "release/src"))
    code, lines = check.evaluate(home)
    assert code == 2 and "mishe" in lines[0]
    (home / "bin" / "mishe").unlink()
    assert check.evaluate(home)[0] == 3                        # a missing wrapper is UNKNOWN, never OK


def test_wrapper_text_without_home_is_unchanged_three_lines(tmp_path):
    from mishe_tauftauf.release_boundary import wrapper_text
    text = wrapper_text(tmp_path / "rel", "/usr/bin/python3")
    assert text.count("\n") == 3 and "MISHE_SEED_HOME" not in text


def test_wrapper_exports_its_own_site_so_a_bare_run_from_anywhere_resolves_it(tmp_path):
    from mishe_tauftauf.release_boundary import wrapper_text
    release = tmp_path / "release"
    (release / "src" / "mishe_tauftauf").mkdir(parents=True)
    (release / "src" / "mishe_tauftauf" / "__init__.py").write_text("")
    (release / "src" / "mishe_tauftauf" / "__main__.py").write_text(
        "import os\nprint(os.environ.get('MISHE_SEED_HOME', 'UNSET'))\n")
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    wrapper = home / "bin" / "mishe"
    wrapper.write_text(wrapper_text(release, sys.executable, home=home))
    wrapper.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME")}
    out = subprocess.run([str(wrapper)], cwd="/", capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout.strip() == str(home.resolve())


def test_refresh_cli_keeps_the_site_export_line_and_repins_pythonpath(tmp_path, monkeypatch):
    from mishe_tauftauf.release_boundary import wrapper_text
    release = make_release(tmp_path)
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    wrapper = home / "bin" / "mishe"
    wrapper.write_text(wrapper_text(tmp_path / "old", sys.executable, home=home))
    refresh_cli(home, release)
    lines = wrapper.read_text().splitlines()
    assert len(lines) == 4
    assert lines[1].startswith("export PYTHONPATH=") and str(release.resolve() / "src") in lines[1]
    assert lines[2] == f"export MISHE_SEED_HOME={home.resolve()}"
