"""Canonical wall settlement and idle process rotation safety."""
import pytest

from mishe_tauftauf import seed
from mishe_tauftauf.feed import Feed
from tests.test_publication_integration import clear_fixture
from tests.test_mind_choice import wake


def settled(home):
    attempt = wake(home, 'witness')
    note = home / 'notes.md'
    note.write_text('Investigated missing evidence. Next: check the resolver response.')
    seed.yield_wake(home, 'witness', attempt, note, result='blocked')
    return attempt, note


def test_yield_writes_wall_and_handoff_without_completion_claim(tmp_path):
    attempt, note = settled(tmp_path)
    assert (tmp_path / 'walls/witness.md').read_text() == note.read_text()
    assert (tmp_path / 'handoffs/witness.md').read_text() == note.read_text()
    entries = Feed(tmp_path).entries()
    assert not any(e.body.startswith('[work]') for e in entries)
    assert 'transport receipt' in entries[-1].body
    assert seed._state(tmp_path, 'witness')[1] is None
    assert seed._state(tmp_path, 'witness')[2] == attempt


def test_yield_retries_identical_notes_without_duplicate_receipt(tmp_path):
    attempt, note = settled(tmp_path)
    before = Feed(tmp_path).read_bytes()
    seed.yield_wake(tmp_path, 'witness', attempt, note, result='blocked')
    assert Feed(tmp_path).read_bytes() == before


def test_yield_refuses_conflicting_retry_notes(tmp_path):
    attempt, note = settled(tmp_path)
    before = Feed(tmp_path).read_bytes()
    note.write_text('Conflicting revised account.')
    with pytest.raises(ValueError, match='differ|conflict|match'):
        seed.yield_wake(tmp_path, 'witness', attempt, note, result='blocked')
    assert Feed(tmp_path).read_bytes() == before
    assert 'Investigated missing' in (tmp_path / 'walls/witness.md').read_text()


def test_yield_wrong_wake_has_no_effect(tmp_path):
    attempt = wake(tmp_path, 'witness')
    note = tmp_path / 'notes.md'
    note.write_text('Plan the next bounded investigation.')
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='not pending'):
        seed.yield_wake(tmp_path, 'witness', attempt + 1, note)
    assert Feed(tmp_path).read_bytes() == before
    assert not (tmp_path / 'walls/witness.md').exists()


def test_clear_busy_mind_does_not_rotate(tmp_path, monkeypatch):
    settled(tmp_path)
    state = clear_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(seed, '_mind_idle', lambda *args: False)
    before = Feed(tmp_path).read_bytes()
    assert 'busy' in seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 0
    assert Feed(tmp_path).read_bytes() == before


def test_clear_requires_observed_pid_change(tmp_path, monkeypatch):
    settled(tmp_path)
    state = clear_fixture(tmp_path, monkeypatch)
    original = seed._tmux
    def unchanged(*args):
        result = original(*args)
        if args[-1] == '#{pane_pid}':
            result.stdout = b'101'
        return result
    monkeypatch.setattr(seed, '_tmux', unchanged)
    with pytest.raises(ValueError, match='rotate|rotation|process'):
        seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 1
    assert not any(e.body.startswith('seed clear ') for e in Feed(tmp_path).entries())
    with pytest.raises(ValueError):
        seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 1


def test_clear_repeated_receipt_does_not_rotate_twice(tmp_path, monkeypatch):
    settled(tmp_path)
    state = clear_fixture(tmp_path, monkeypatch)
    seed.clear(tmp_path, 'session', 'witness')
    before = Feed(tmp_path).read_bytes()
    seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 1
    assert Feed(tmp_path).read_bytes() == before
