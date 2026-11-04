import json

from mishe_tauftauf import durable_migration as migration, intentions, owed, signal_store
from mishe_tauftauf.core.state import DispatchRecord, DispatchStore
from mishe_tauftauf.core.interfaces import UNKNOWN


def test_retirement_preserves_unknown_mail_future_work_and_red_owner(tmp_path):
    home = tmp_path
    (home / 'hold').touch()
    (home / 'charters').mkdir()
    for role in ('signals', 'senses'):
        (home / 'charters' / f'{role}.md').write_text(role)
    (home / 'walls').mkdir()
    (home / 'walls/signals.md').write_text('unresolved literal $(touch bad)')
    intentions.add(home, 'signals', 'later', due=500, note='check exact claim')
    DispatchStore(home / 'dispatch').write(DispatchRecord(role='signals', key='original', state=UNKNOWN))
    owed.touch_inbox(home, 'signals')
    signal_store.register(home, 'fault', max_age=10, subscribers=['signals'])
    signal_store.observe(home, 'fault', 'RED', now=10, source='fixture')
    report = migration.apply(home, {'signals': 'senses'}, archive=home / 'archive', now=20,
                             mail={'signals': [{'sequence': 42, 'body': 'unresolved request'}]})
    assert not (home / 'charters/signals.md').exists()
    assert not (home / 'dispatch/signals.json').exists()
    assert json.loads((home / 'archive/dispatch/signals.json').read_text())['state'] == 'unknown'
    assert signal_store.registry(home)['fault']['subscribers'] == ['senses']
    assert signal_store.read(home, 'fault', now=20).value == 'RED'
    assert not owed.cursor_path(home, 'senses').exists()
    assert 'inbox' in owed.check(home, 'senses', now=20).reasons
    assert any(i.get('due') == 500 for i in intentions.list_(home, 'senses'))
    assert report['signals']['mail'][0]['sequence'] == 42
    first = sorted((p.relative_to(home), p.read_bytes()) for p in home.rglob('*') if p.is_file())
    migration.apply(home, {'signals': 'senses'}, archive=home / 'archive', now=30, mail={})
    assert first == sorted((p.relative_to(home), p.read_bytes()) for p in home.rglob('*') if p.is_file())


def test_requires_hold_and_declared_successor(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        migration.apply(tmp_path, {'old': 'new'}, archive=tmp_path / 'archive', now=0, mail={})


def test_hold_does_not_authorize_retiring_a_live_dispatch(tmp_path):
    import os
    import pytest
    (tmp_path / 'hold').touch()
    (tmp_path / 'charters').mkdir()
    (tmp_path / 'charters/new.md').write_text('new')
    DispatchStore(tmp_path / 'dispatch').write(DispatchRecord(role='old', key='live', state=UNKNOWN, pid=os.getpid()))
    with pytest.raises(ValueError, match='live dispatch'):
        migration.apply(tmp_path, {'old': 'new'}, archive=tmp_path / 'archive', now=0, mail={})
    assert not (tmp_path / 'archive').exists()


def test_archive_must_not_alias_site_or_be_deleted_with_a_retired_store(tmp_path):
    import pytest
    (tmp_path / 'hold').touch()
    (tmp_path / 'charters').mkdir()
    (tmp_path / 'charters/new.md').write_text('new')
    (tmp_path / 'charters/old.md').write_text('old evidence')
    for archive in (tmp_path, tmp_path / 'intentions/old/archive', tmp_path / 'charters/archive'):
        with pytest.raises(ValueError, match='archive'):
            migration.apply(tmp_path, {'old': 'new'}, archive=archive, now=0, mail={})
        assert (tmp_path / 'charters/old.md').read_text() == 'old evidence'


def test_resume_after_partial_directory_archive_never_loses_remaining_files(tmp_path):
    import shutil
    (tmp_path / 'hold').touch()
    (tmp_path / 'charters').mkdir()
    (tmp_path / 'charters/new.md').write_text('new')
    intentions.add(tmp_path, 'old', 'one', due=100, note='first obligation')
    intentions.add(tmp_path, 'old', 'two', due=200, note='second obligation')
    archive = tmp_path / 'archive'
    (archive / 'intentions/old').mkdir(parents=True)
    shutil.copy2(tmp_path / 'intentions/old/one.json', archive / 'intentions/old/one.json')
    migration.apply(tmp_path, {'old': 'new'}, archive=archive, now=0, mail={})
    assert {i['id'] for i in intentions.list_(archive, 'old')} == {'one', 'two'}
    assert {i['id'] for i in intentions.list_(tmp_path, 'new')} >= {'inherited-old-one', 'inherited-old-two'}
