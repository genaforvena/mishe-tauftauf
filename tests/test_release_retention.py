"""The release store's reference guard: what may be reclaimed, and what may not.

The store itself is a fixture: three detached worktrees under
``<home>/releases``. Every reference source is injected (process root, unit
roots, transient root, registry rows) so the guard is exercised without reading
the host.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mishe_tauftauf import release_retention


def _store(tmp_path: Path) -> tuple[Path, list[str]]:
    """A core site whose release store holds one worktree per committed release."""
    kernel = tmp_path / "kernel"
    kernel.mkdir()
    subprocess.run(["git", "-C", str(kernel), "init", "-q"], check=True)
    (kernel / "src").mkdir()
    (kernel / "src/x.py").write_text("x")
    shas: list[str] = []
    for index in range(3):
        (kernel / "src/x.py").write_text(f"x{index}")
        subprocess.run(["git", "-C", str(kernel), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(kernel), "-c", "user.name=t", "-c", "user.email=t@e",
                        "commit", "-qm", f"release {index}"], check=True)
        shas.append(subprocess.check_output(
            ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip())
    home = kernel / ".mishe-tauftauf"
    (home / "health").mkdir(parents=True)
    for sha in shas:
        subprocess.run(["git", "-C", str(kernel), "worktree", "add", "--detach",
                        str(home / "releases" / sha), sha], check=True, capture_output=True)
    return home, shas


def _sources(tmp_path: Path, units: Path) -> dict:
    """The injected reference sources: no live process, only the fixture units."""
    return {"proc_root": tmp_path / "proc", "unit_roots": (units,),
            "transient_root": tmp_path / "no-transient"}


def test_a_unit_reference_is_live_and_losing_it_makes_the_release_reclaimable(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    referenced, *unreferenced = shas
    unit = units / "example.service"
    unit.write_text(f"Environment=PYTHONPATH={home / 'releases' / referenced}/src\n")

    sources = _sources(tmp_path, units)
    assert referenced in release_retention.live_release_names(home, **sources)
    assert [p.name for p in release_retention.reclaimable_releases(home, **sources)] == sorted(unreferenced)

    # Controlled failure: the same release flips to reclaimable when its only
    # reference is removed, so a "reclaimable" verdict is a tested negative.
    unit.unlink()
    assert [p.name for p in release_retention.reclaimable_releases(home, **sources)] == sorted(shas)


def test_the_core_pin_and_a_registered_sha_protect_their_releases(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    pinned, registered, _ = shas
    (home / "health/runtime-release.json").write_text(json.dumps(
        {"version": 1, "source": str(home / "releases" / pinned), "sha": pinned}))
    rows = [{"home": str(tmp_path / "other"), "session": "other", "sha": registered}]

    live = release_retention.live_release_names(home, registry_rows=rows, **_sources(tmp_path, units))
    assert {pinned, registered} <= live


def test_a_registered_site_pin_protects_its_release(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    other = tmp_path / "other" / ".mishe-tauftauf"
    (other / "health").mkdir(parents=True)
    (other / "health/runtime-release.json").write_text(json.dumps(
        {"version": 1, "source": str(home / "releases" / shas[0]), "sha": shas[0]}))
    rows = [{"home": str(other), "session": "other", "sha": shas[1]}]

    assert shas[0] in release_retention.live_release_names(
        home, registry_rows=rows, **_sources(tmp_path, units))


def test_a_pin_naming_its_release_by_directory_protects_that_release(tmp_path: Path) -> None:
    """A pin whose release directory is not a bare SHA is still protected.

    The operator's core activation pins ``releases/core-20261028-bb04f44``:
    the directory name carries a date and a short SHA, so the text scan that
    matches ``releases/<40-hex>`` finds nothing and the pruner removed the
    very release the pin named. On 2026-10-29 the coordinator logged
    "pruned 1 release worktrees" and the pinned tree vanished; every resident
    unit then failed with "runtime pin invalid: runtime source unavailable".
    """
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    named = "core-20261028-bb04f44"
    release = home / "releases" / named
    subprocess.run(["git", "-C", str(home.parent), "worktree", "add", "--detach",
                    str(release), shas[0]], check=True, capture_output=True)
    (home / "health/runtime-release.json").write_text(json.dumps(
        {"version": 1, "source": str(release), "sha": shas[0]}))

    sources = _sources(tmp_path, units)
    assert named in release_retention.live_release_names(home, **sources)
    assert named not in [p.name for p in release_retention.reclaimable_releases(home, **sources)]

    # Controlled failure: a release no pin names is still reclaimable, so the
    # protection above is a tested positive and not a blanket exemption.
    assert shas[1] in [p.name for p in release_retention.reclaimable_releases(home, **sources)]


def test_a_live_process_environment_protects_its_release(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242/environ").write_bytes(
        f"PYTHONPATH={home / 'releases' / shas[0]}/src".encode())

    live = release_retention.live_release_names(
        home, proc_root=proc, unit_roots=(units,), transient_root=tmp_path / "none")
    assert shas[0] in live
    assert [p.name for p in release_retention.reclaimable_releases(
        home, proc_root=proc, unit_roots=(units,), transient_root=tmp_path / "none")] == sorted(shas[1:])



def test_a_placeholder_release_name_in_a_unit_protects_nothing(tmp_path: Path) -> None:
    """Only a full 40-hex release name counts, so a template path protects nothing."""
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    (units / "example.service").write_text("Environment=PYTHONPATH=/srv/releases/<sha>/src\n")

    assert release_retention.live_release_names(home, **_sources(tmp_path, units)) == set()
    assert [p.name for p in release_retention.reclaimable_releases(
        home, **_sources(tmp_path, units))] == sorted(shas)


def test_a_legacy_directory_that_is_not_a_worktree_is_never_reclaimable(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    legacy = home / "releases" / "4af625a"
    legacy.mkdir()
    (legacy / "src").mkdir()

    assert "4af625a" not in release_retention.release_worktrees(home)
    assert "4af625a" not in [p.name for p in release_retention.reclaimable_releases(
        home, **_sources(tmp_path, units))]


def test_protect_names_are_kept_even_when_nothing_references_them(tmp_path: Path) -> None:
    home, shas = _store(tmp_path)
    units = tmp_path / "units"
    units.mkdir()

    kept = release_retention.reclaimable_releases(
        home, protect={shas[0]}, **_sources(tmp_path, units))
    assert [p.name for p in kept] == sorted(shas[1:])
