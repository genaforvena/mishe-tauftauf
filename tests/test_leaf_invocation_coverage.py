"""The reader-command coverage checker audits the served release, never a
stale or invented tree.

The one-off probes that produced the book's standing rule — name the command,
not only the practice — hardcoded a release path, so a committed copy would
have kept auditing a retired release's argparse tree while the pin moved. The
checker now resolves the audited tree from the site's served-release record
and refuses anything else. These tests pin that contract on fixtures: a
matching ``--src`` is accepted and named, a non-served ``--src`` is refused,
and a missing or malformed record reads UNKNOWN instead of silently auditing
the checkout tree the test happens to run beside.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_CHECKER = _CHECKOUT_ROOT / "tools" / "check_leaf_invocation_coverage.py"
_SHA = "0" * 39 + "1"

_FAKE_CLI = '''\
import argparse


def parser():
    p = argparse.ArgumentParser(prog="mishe-tauftauf")
    sub = p.add_subparsers()
    alpha = sub.add_parser("alpha")
    alpha_sub = alpha.add_subparsers()
    alpha_sub.add_parser("one")
    alpha_sub.add_parser("two")
    sub.add_parser("beta")
    return p
'''


def _release(tmp: Path) -> Path:
    src = tmp / "releases" / _SHA / "src" / "mishe_tauftauf"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cli.py").write_text(_FAKE_CLI, encoding="utf-8")
    return tmp / "releases" / _SHA


def _home(tmp: Path, release: Path, sha: str = _SHA) -> Path:
    home = tmp / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "runtime-release.json").write_text(
        json.dumps({"version": 1, "source": str(release), "sha": sha}),
        encoding="utf-8")
    return home


def _corpus(tmp: Path) -> Path:
    root = tmp / "book"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("run alpha one for the first command\n",
                                    encoding="utf-8")
    (root / "docs" / "guide.md").write_text("beta is the bare top\n",
                                            encoding="utf-8")
    return root


def _run(home: Path, root: Path, out: Path, src: Path | None = None
         ) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(_CHECKER), "--home", str(home),
            "--root", str(root), "--out", str(out)]
    if src is not None:
        argv += ["--src", str(src)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120,
                          cwd=str(_CHECKOUT_ROOT))


def test_audits_the_release_the_record_names(tmp_path: Path) -> None:
    release = _release(tmp_path)
    home = _home(tmp_path, release)
    root = _corpus(tmp_path)
    out = tmp_path / "coverage.json"

    done = _run(home, root, out)

    assert done.returncode == 0, done.stderr
    assert _SHA in done.stdout
    # 3 leaf invocations; 'alpha one' and 'beta' named, 'alpha two' not
    assert "leaf invocations 3 " in done.stdout
    assert "named 2; uncovered 1" in done.stdout
    assert "coverage rows, not failures" in done.stdout
    assert "alpha two" in done.stdout
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["served_sha"] == _SHA
    assert record["invocations_covered"] == 2
    assert record["strict_zero"] == ["alpha two"]


def test_a_src_that_is_the_served_release_is_accepted_and_named(
        tmp_path: Path) -> None:
    release = _release(tmp_path)
    home = _home(tmp_path, release)
    root = _corpus(tmp_path)
    out = tmp_path / "coverage.json"

    done = _run(home, root, out, src=release / "src")

    assert done.returncode == 0, done.stderr
    assert f"is the served release {_SHA}" in done.stdout


def test_refuses_a_src_that_is_not_the_served_release(tmp_path: Path) -> None:
    release = _release(tmp_path)
    other = _release(tmp_path / "elsewhere")
    home = _home(tmp_path, release)
    root = _corpus(tmp_path)
    out = tmp_path / "coverage.json"

    done = _run(home, root, out, src=other / "src")

    assert done.returncode == 2
    assert "REFUSED" in done.stderr
    assert str(other / "src") in done.stderr
    assert _SHA in done.stderr
    assert not out.exists()
    assert "leaf invocations" not in done.stdout


def test_a_missing_record_reads_unknown_not_the_checkout(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    root = _corpus(tmp_path)
    out = tmp_path / "coverage.json"

    done = _run(home, root, out)

    assert done.returncode == 2
    assert "UNKNOWN" in done.stderr
    assert not out.exists()
    assert "leaf invocations" not in done.stdout


def test_an_invalid_record_reads_unknown(tmp_path: Path) -> None:
    release = _release(tmp_path)
    home = _home(tmp_path, release, sha="deadbeef")
    root = _corpus(tmp_path)
    out = tmp_path / "coverage.json"

    done = _run(home, root, out)

    assert done.returncode == 2
    assert "UNKNOWN" in done.stderr
    assert "deadbeef" in done.stderr
    assert not out.exists()
