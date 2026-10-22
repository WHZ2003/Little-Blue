from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from little_blue.memory_transfer import export_memory, import_memory, inspect_memory
from little_blue.storage import Store


def test_hermes_knowledge_survives_copy_without_credentials(source, tmp_path):
    folder, store = source
    home = folder / 'hermes'
    (home / 'memories').mkdir(parents=True)
    (home / 'skills/example').mkdir(parents=True)
    (home / 'memories/USER.md').write_text('Prefers blue notebooks.')
    (home / 'SOUL.md').write_text('Existing companion identity.')
    (home / 'skills/example/SKILL.md').write_text('A reusable synthetic workflow.')
    (home / '.env').write_text('API_KEY=do-not-copy')
    (home / 'config.yaml').write_text('secret: do-not-copy')
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE messages(text TEXT)')
        db.execute("INSERT INTO messages VALUES('synthetic history')")
    archive = tmp_path / 'hermes.zip'
    manifest = export_memory(folder, archive)
    assert len(manifest['harness_files']) == 4
    target = tmp_path / 'restored'
    import_memory(archive, target)
    assert (target / 'hermes/memories/USER.md').read_text() == 'Prefers blue notebooks.'
    assert (target / 'hermes/SOUL.md').read_text() == 'Existing companion identity.'
    assert not (target / 'hermes/.env').exists()
    assert not (target / 'hermes/config.yaml').exists()
    with sqlite3.connect(target / 'hermes/state.db') as db:
        assert db.execute('SELECT text FROM messages').fetchone()[0] == 'synthetic history'


@pytest.fixture
def source(tmp_path):
    folder = tmp_path / 'source'
    store = Store(folder / 'memory.sqlite3')
    store.remember('用户喜欢蓝莓茶。', 'semantic')
    store.continuity.append('user', '今天完成了答辩。', 'desktop')
    store.put('settings', {'online': True, 'tools_enabled': True, 'perception': True, 'model': 'deepseek-flash'})
    store.put('phone', {'fish': ['test-fish']})
    store.put('bridge_binding', {'account': 'old-device'})
    store.put('wechat:old-bot:cursor', 'old-session-cursor')
    store.add_reminder('旧提醒', 1)
    store.task('old-task', 'running', '测试')
    (folder / 'wechat-account.json').write_text('{"token":"SECRET-LOGIN"}')
    (folder / '.env').write_text('DEEPSEEK_API_KEY=SECRET-KEY')
    yield folder, store
    store.close()


def rows(path, table):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def test_live_wal_snapshot_roundtrip_and_restore_policy(source, tmp_path):
    folder, store = source
    # Leave the writer open and committed data in WAL during export.
    assert (folder / 'memory.sqlite3-wal').exists()
    archive = tmp_path / 'copy.zip'
    manifest = export_memory(folder, archive)
    assert manifest['databases'][0]['counts']['memories'] == 1
    assert inspect_memory(archive) == manifest
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {'manifest.json', 'memory.sqlite3'}
        assert b'SECRET-LOGIN' not in zipped.read('memory.sqlite3')
        assert b'SECRET-KEY' not in zipped.read('memory.sqlite3')
    destination = tmp_path / 'restored'
    import_memory(archive, destination)
    for table in ('memories', 'conversation', 'growth_state', 'growth_sources', 'memory_meta'):
        assert rows(folder / 'memory.sqlite3', table) == rows(destination / 'memory.sqlite3', table)
    restored = Store(destination / 'memory.sqlite3')
    try:
        assert restored.search('蓝莓')[0]['text'] == '用户喜欢蓝莓茶。'
        assert restored.get('phone') == {'fish': ['test-fish']}
        assert restored.get('settings')['online'] is False
        assert restored.get('settings')['tools_enabled'] is False
        assert restored.get('settings')['perception'] is False
        assert restored.get('bridge_binding') is None
        assert restored.get('wechat:old-bot:cursor') is None
        assert restored.db.execute('SELECT status FROM tasks').fetchone()[0] == 'interrupted'
        assert not restored.due_reminders(9999999999)
    finally:
        restored.close()
    assert store.get('settings')['online'] is True
    assert store.db.execute('SELECT status FROM tasks').fetchone()[0] == 'running'
    assert not (destination / 'wechat-account.json').exists()


def test_scoped_memories_stay_separate(source, tmp_path):
    folder, store = source
    scope = Store(folder / 'conversations' / ('a' * 64 + '.sqlite3'))
    scope.remember('群聊自己的内容')
    scope.close()
    archive = tmp_path / 'scoped.zip'
    export_memory(folder, archive)
    target = tmp_path / 'scoped-copy'
    import_memory(archive, target)
    assert len(list((target / 'conversations').glob('*.sqlite3'))) == 1
    assert '群聊自己的内容' not in str(rows(target / 'memory.sqlite3', 'memories'))


def rewrite_archive(source, target, mutate):
    with zipfile.ZipFile(source) as zipped:
        entries = {name: zipped.read(name) for name in zipped.namelist()}
    mutate(entries)
    with zipfile.ZipFile(target, 'w') as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)


@pytest.mark.parametrize('damage', ['checksum', 'traversal', 'version', 'counts', 'missing', 'duplicate'])
def test_rejects_invalid_archives_without_creating_destination(source, tmp_path, damage):
    folder, store = source
    good, bad = tmp_path / 'good.zip', tmp_path / 'bad.zip'
    export_memory(folder, good)
    def mutate(entries):
        manifest = json.loads(entries['manifest.json'])
        if damage == 'checksum':
            manifest['databases'][0]['sha256'] = '0' * 64
        elif damage == 'traversal':
            entries['../outside.sqlite3'] = b'bad'
        elif damage == 'version':
            manifest['version'] = 999
        elif damage == 'counts':
            manifest['databases'][0]['counts']['memories'] = 999
        elif damage == 'missing':
            entries.pop('memory.sqlite3')
        elif damage == 'duplicate':
            manifest['databases'].append(manifest['databases'][0])
        entries['manifest.json'] = json.dumps(manifest).encode()
    rewrite_archive(good, bad, mutate)
    target = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError):
        import_memory(bad, target)
    assert not target.exists()
    assert not (tmp_path / 'outside.sqlite3').exists()


def test_never_overwrites_existing_data_or_backup(source, tmp_path):
    folder, store = source
    archive = tmp_path / 'copy.zip'
    export_memory(folder, archive)
    original = archive.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        export_memory(folder, archive)
    assert archive.read_bytes() == original
    with pytest.raises(ValueError, match='already exists'):
        import_memory(archive, folder)
    assert store.search('蓝莓')


def test_corrupt_sqlite_rejected_even_with_matching_hash(source, tmp_path):
    folder, store = source
    good, bad = tmp_path / 'good.zip', tmp_path / 'bad.zip'
    export_memory(folder, good)
    def mutate(entries):
        data = b'not a database'
        entries['memory.sqlite3'] = data
        manifest = json.loads(entries['manifest.json'])
        manifest['databases'][0].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        entries['manifest.json'] = json.dumps(manifest).encode()
    rewrite_archive(good, bad, mutate)
    with pytest.raises(sqlite3.DatabaseError):
        import_memory(bad, tmp_path / 'bad-copy')
    assert not (tmp_path / 'bad-copy').exists()
