"""Keep the coordinator's release-worktree store bounded.

``coordination/site_sync.py`` installs one detached worktree per green SHA at
``<core site>/releases/<sha>`` and nothing removed them: 371 of 373 were
referenced by nothing and the store grew ~0.12 GiB/day without bound (discover
wake 60974).  The rule here is deliberately conservative — a release is only
reclaimable when *no* live reference names it:

* a running process's environment,
* an installed or transient systemd unit (user and system),
* the core pin ``health/runtime-release.json``,
* the linked-site registry and every registered site's own pin.

Every blob is external text, so the extraction stays at the ``proc_table``
boundary and this module acts on the decoded names.  The registry is parsed by
its writer (``site_sync._load``) and passed in, so this module reads no JSON.
Non-worktree entries under ``releases/`` are never returned, so the two legacy
copies are an owner decision, not this module's.

Every release name is a commit on ``main``, so removal loses no history and is
reversed exactly with ``git worktree add --detach releases/<sha> <sha>``.  No
"keep the last K" margin is kept: the reference set above is exact, and git
history preserves provenance.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping

from .proc_table import release_names

DEFAULT_UNIT_ROOTS = (Path.home() / ".config/systemd/user", Path("/etc/systemd/user"))
"""Installed unit trees; a per-user and a system-wide tree, both owned by this host."""


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _environ_text(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _unit_names(roots: Iterable[Path]) -> set[str]:
    names: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (".service", ".conf"):
                names.update(release_names(_text(path)))
    return names


def _pin_names(home: Path) -> set[str]:
    """Release names the site's own pin file text carries."""
    return set(release_names(_text(home / "health" / "runtime-release.json")))


def live_release_names(home: Path, *, registry_rows: Iterable[Mapping[str, object]] = (),
                       proc_root: Path = Path("/proc"),
                       unit_roots: tuple[Path, ...] | None = None,
                       transient_root: Path | None = None) -> set[str]:
    """Every release directory name a live reference points at.

    ``registry_rows`` are the linked-site registry rows as its writer decoded
    them; the caller must pass them (an unreadable registry is then the caller's
    error) because this module interprets no JSON.  A reference source that
    cannot be read contributes nothing, so a caller must not treat an unreadable
    source as proof that a release is unreferenced.
    """
    home = home.resolve()
    names: set[str] = set()
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if entry.name.isdigit():
                names.update(release_names(_environ_text(entry / "environ")))
    roots = DEFAULT_UNIT_ROOTS if unit_roots is None else unit_roots
    transient = transient_root if transient_root is not None else Path(f"/run/user/{os.getuid()}/systemd/transient")
    names |= _unit_names((*roots, transient))
    names |= _pin_names(home)
    for row in registry_rows:
        for key in ("sha", "observed_sha"):
            value = row.get(key)
            if isinstance(value, str) and value:
                names.add(value)
        target = row.get("home")
        if isinstance(target, str) and target:
            names |= _pin_names(Path(target))
    return names


def release_worktrees(home: Path) -> dict[str, Path]:
    """Directory name -> path for every git worktree under the release store.

    A worktree's ``.git`` is a file; a plain copy has none, so the two legacy
    entries are never candidates.  ``git worktree remove`` is the final
    authority: a candidate that is not a registered worktree of the kernel is
    refused there, not deleted here.
    """
    store = home / "releases"
    found: dict[str, Path] = {}
    if not store.is_dir():
        return found
    for entry in sorted(store.iterdir()):
        if entry.is_dir() and (entry / ".git").is_file():
            found[entry.name] = entry
    return found


def reclaimable_releases(home: Path, *, protect: Iterable[str] = (), **kwargs) -> list[Path]:
    """Release worktrees no live reference names, sorted by name.

    ``protect`` names releases the caller knows are in use but that this reading
    cannot see yet, such as the green SHA a sync is installing. An empty store
    returns before any reference source is read, so an idle coordinator does no
    process or unit scan.
    """
    worktrees = release_worktrees(home)
    if not worktrees:
        return []
    live = live_release_names(home, **kwargs) | set(protect)
    return [path for name, path in worktrees.items() if name not in live]
