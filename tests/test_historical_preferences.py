import copy
import sqlite3
import pytest
from little_blue.storage import Store
from little_blue.preferences import setup, snapshot
from tools.import_historical_preferences import import_review


@pytest.fixture
def historical(tmp_path):
    store = Store(tmp_path / 'history.sqlite3')
    from little_blue.companion import setup as companion_setup
    companion_setup(store)
    setup(store)
    with store.db:
        store.db.execute("INSERT INTO conversation(id,role,origin,text,created) VALUES(1,'user','wechat','以后长文完整发',100)")
        store.db.execute("INSERT INTO conversation(id,role,origin,text,created) VALUES(2,'assistant','wechat','以后长文完整发',101)")
    store.put('settings', {'proactive': False, 'unknown_preserved': 'value'})
    manifest = {'source': 'historical-review:test', 'expected_revision': 0, 'changes': [
        {'key': 'long_form_mode', 'value': 'whole', 'evidence': [{'id': 1, 'text': '以后长文完整发', 'created': 100}]}]}
    yield store, manifest
    store.close()


def test_dry_run_apply_replay_and_restart_preserve_other_data(historical):
    store, manifest = historical
    original = store.db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0]
    assert import_review(store.db, manifest)['imported'] == ['long_form_mode']
    assert snapshot(store)['revision'] == 0
    assert import_review(store.db, manifest, apply=True)['revision'] == 1
    assert import_review(store.db, manifest, apply=True)['replayed']
    assert snapshot(store)['records'][0]['source_at'] == 100
    assert store.db.execute('SELECT count(*) FROM conversation').fetchone()[0] == 2
    assert store.db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0] == original
    with sqlite3.connect(store.path) as reopened:
        assert reopened.execute('SELECT count(*) FROM shared_preferences').fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['assistant', 'text', 'timestamp', 'unsupported', 'settings', 'revision', 'duplicate'])
def test_invalid_manifest_is_atomic(historical, failure):
    store, manifest = historical
    bad = copy.deepcopy(manifest)
    change = bad['changes'][0]
    if failure == 'assistant': change['evidence'][0].update(id=2, created=101)
    elif failure == 'text': change['evidence'][0]['text'] = '编造的原话'
    elif failure == 'timestamp': change['evidence'][0]['created'] = 999
    elif failure == 'unsupported': change['key'] = 'close_browser'
    elif failure == 'settings': change.update(key='proactive', value=False)
    elif failure == 'revision': bad['expected_revision'] = 9
    elif failure == 'duplicate': bad['changes'].append(copy.deepcopy(change))
    with pytest.raises(ValueError): import_review(store.db, bad, apply=True)
    assert snapshot(store)['revision'] == 0
    assert store.db.execute('SELECT count(*) FROM preference_receipts').fetchone()[0] == 0


def test_newer_record_and_conflicting_replay_are_protected(historical):
    store, manifest = historical
    with store.db:
        store.db.execute('INSERT INTO shared_preferences VALUES(?,?,?,?,?,?)',
                         ('long_form_mode', '"split"', 'new', '新偏好', 200, 0))
    result = import_review(store.db, manifest, apply=True)
    assert result['skipped'] == ['long_form_mode']
    assert snapshot(store)['values']['long_form_mode'] == 'split'
    manifest['changes'][0]['value'] = 'split'
    with pytest.raises(ValueError, match='Conflicting'): import_review(store.db, manifest, apply=True)
