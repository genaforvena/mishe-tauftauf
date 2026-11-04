"""Held-site role retirement with explicit successors and preserved obligations.

No prose interpretation, mail acceptance, dispatch completion or engine launch.
Archive is also the restart journal: interruption can safely rerun the operation.
"""
from __future__ import annotations

import shutil
import tempfile
import os
from pathlib import Path

from . import intentions, owed, roles, signal_store
from .core.dispatch_record import valid_role, process_alive, read_record_fields
from .state_files import read_json, write_json


def _archive(source: Path, saved: Path) -> None:
    """Resume incomplete directory copies; publish each exact file atomically.

    A conflicting archive is evidence to reconcile, never permission to delete
    the source. An interrupted file copy leaves only an unpublished temporary.
    """
    if source.is_symlink() or saved.is_symlink():
        raise ValueError(f'archive refuses symlink: {source}')
    if source.is_dir():
        saved.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _archive(child, saved / child.name)
        shutil.copystat(source, saved)
    elif source.is_file():
        if saved.exists():
            if (not saved.is_file() or source.read_bytes() != saved.read_bytes()
                    or source.stat().st_mode & 0o777 != saved.stat().st_mode & 0o777):
                raise ValueError(f'{source} changed after archive; reconcile without overwriting it')
            return
        saved.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=saved.parent, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copy2(source, temporary)
            with temporary.open('rb') as handle:
                os.fsync(handle.fileno())
            temporary.replace(saved)
            directory = os.open(saved.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def apply(home, successors: dict, *, archive: Path, now: float, mail: dict) -> dict:
    home, archive = Path(home), Path(archive)
    if not (home / 'hold').is_file():
        raise ValueError('role migration requires a held site')
    site, saved_root = home.resolve(), archive.resolve()
    active_stores = {'charters', 'walls', 'dispatch', 'intentions', 'cursors', 'inbox', 'engines', 'signals'}
    if (saved_root == site or not saved_root.is_relative_to(site)
            or saved_root.relative_to(site).parts[0] in active_stores):
        raise ValueError('archive must be a strict site descendant outside active stores')
    for old, new in successors.items():
        if not valid_role(old) or not valid_role(new) or old == new or new in successors:
            raise ValueError('each retired role needs a distinct surviving successor')
        if new not in roles.declared(home):
            raise ValueError(f'successor {new} has no charter')
        dispatch = home / 'dispatch' / f'{old}.json'
        if dispatch.exists() and process_alive(read_record_fields(dispatch)['pid']):
            raise ValueError(f'{old} still has a live dispatch; hold does not stop an in-flight engine')
    report_path = archive / 'ownership.json'
    report = read_json(report_path) or {}
    for old, new in successors.items():
        if old not in report:
            report[old] = {'successor': new, 'mail': mail.get(old, []), 'state': 'unresolved',
                           'evidence': str(archive), 'recorded_at': now}
            write_json(report_path, report)  # durable before removing any active input
        elif report[old]['successor'] != new:
            raise ValueError('existing successor differs; reconcile before migration')
        for name in (f'charters/{old}.md', f'walls/{old}.md', f'dispatch/{old}.json',
                     f'intentions/{old}', f'cursors/{old}.json', f'inbox/{old}', f'engines/{old}.json'):
            source, saved = home / name, archive / name
            if source.exists():
                _archive(source, saved)
        for item in intentions.list_(archive, old):
            identity = f'inherited-{old}-{item["id"]}'
            if not any(i['id'] == identity for i in intentions.list_(home, new)):
                intentions.add(home, new, identity, note=f'From {old}: {item["note"]}; original {archive}',
                               due=item.get('due'), when=item.get('when'), spontaneous=item.get('spontaneous', False))
        identity = f'recover-{old}'
        if not any(i['id'] == identity for i in intentions.list_(home, new)):
            intentions.add(home, new, identity, due=now,
                           note=f'Reconcile {old} obligations, original UNKNOWN effects and unread mail: {report_path}. '
                           'Establish actual effects; never replay or call them completed by migration.')
            owed.touch_inbox(home, new)
        registry = signal_store.registry(home)
        changed = False
        for entry in registry.values():
            if old in entry.get('subscribers', []):
                entry['subscribers'] = sorted({new if r == old else r for r in entry['subscribers']})
                changed = True
        if changed:
            write_json(home / 'signals/registry.json', registry)
        # Only after work, evidence and successor triggers exist may old triggers go.
        for name in (f'charters/{old}.md', f'walls/{old}.md', f'dispatch/{old}.json',
                     f'intentions/{old}', f'inbox/{old}', f'engines/{old}.json'):
            source = home / name
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink(missing_ok=True)
    return report
