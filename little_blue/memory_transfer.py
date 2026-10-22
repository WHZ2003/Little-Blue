"""Portable, verified SQLite snapshots. Uses only the Python standard library."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile

FORMAT = 'little-blue-memory'
VERSION = 2
MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 612
COUNTS = ('memories', 'conversation', 'growth_versions', 'memory_changes', 'reminders', 'diaries')


def database_name(name):
    return name == 'memory.sqlite3' or bool(re.fullmatch(r'conversations/[a-f0-9]{64}\.sqlite3', name))


def harness_name(name):
    if not isinstance(name, str) or any(p in ('', '.', '..') for p in name.split('/')) or '\\' in name:
        return False
    return name in ('hermes/state.db', 'hermes/SOUL.md', 'hermes/memories/MEMORY.md', 'hermes/memories/USER.md') or bool(
        re.fullmatch(r'hermes/skills/[A-Za-z0-9_./-]+\.(md|py|json|yaml|yml|txt|sh|js|ts)', name))


def snapshot_harness(data_dir, staging):
    """Only durable knowledge; never config, auth, provider keys, logs or runtime locks."""
    root = data_dir / 'hermes'
    candidates = [root / 'state.db', root / 'SOUL.md', root / 'memories/MEMORY.md', root / 'memories/USER.md']
    if (root / 'skills').exists():
        candidates.extend(sorted((root / 'skills').rglob('*')))
    files = []
    total = 0
    for source in candidates:
        if not source.is_file():
            continue
        name = source.relative_to(data_dir).as_posix()
        if not harness_name(name):
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(data_dir):
            raise ValueError('Unsafe Hermes knowledge path')
        if len(files) >= 500 or source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Hermes knowledge exceeds backup bounds')
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'hermes/state.db':
            deadline = time.monotonic() + 60
            with closing(readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
                page_size = src.execute('PRAGMA page_size').fetchone()[0]
                def progress(status, remaining, pages):
                    if time.monotonic() > deadline or pages * page_size > 64 * 1024 * 1024:
                        raise ValueError('Hermes database snapshot exceeds bounds')
                src.backup(dst, pages=256, progress=progress)
                dst.execute('PRAGMA journal_mode=DELETE')
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Hermes database integrity check failed')
        else:
            shutil.copyfile(source, target)
        total += target.stat().st_size
        if total > 128 * 1024 * 1024:
            raise ValueError('Hermes knowledge archive too large')
        files.append({'path': name, 'bytes': target.stat().st_size, 'sha256': digest(target)})
    return files


def readonly(path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    connection.execute('PRAGMA query_only=ON')
    return connection


def describe(path):
    with closing(readonly(path)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'kv', 'memories', 'tasks'} <= tables:
            raise ValueError('Not a Little Blue memory database')
        return {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in COUNTS if table in tables}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export_memory(data_dir, output):
    data_dir, output = Path(data_dir).resolve(), Path(output).absolute()
    if output.exists():
        raise ValueError('Output already exists; choose a new archive filename')
    source = data_dir / 'memory.sqlite3'
    if not source.is_file():
        raise ValueError('Source memory.sqlite3 does not exist')
    sources = [source] + sorted((data_dir / 'conversations').glob('*.sqlite3'))
    if len(sources) > MAX_FILES:
        raise ValueError('Too many databases')
    for path in sources:
        if path.is_symlink() or not database_name(path.relative_to(data_dir).as_posix()):
            raise ValueError('Unexpected database path')
        if not path.resolve().is_relative_to(data_dir):
            raise ValueError('Database is outside the data directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        files = []
        total = 0
        for source in sources:
            name = source.relative_to(data_dir).as_posix()
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + 60
            page_size = 4096

            def progress(status, remaining, pages):
                if time.monotonic() > deadline:
                    raise TimeoutError('Database snapshot timed out')
                if total + pages * page_size > MAX_BYTES:
                    raise ValueError('Database exceeds snapshot size bound')

            with closing(readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
                page_size = src.execute('PRAGMA page_size').fetchone()[0]
                src.backup(dst, pages=256, progress=progress, sleep=.05)
                dst.execute('PRAGMA journal_mode=DELETE')
            total += target.stat().st_size
            if total > MAX_BYTES:
                raise ValueError('Memory archive exceeds 1 GiB uncompressed')
            files.append({'path': name, 'bytes': target.stat().st_size,
                          'sha256': digest(target), 'counts': describe(target)})
        harness_files = snapshot_harness(data_dir, staging)
        if len(files) + len(harness_files) > MAX_FILES or total + sum(f['bytes'] for f in harness_files) > MAX_BYTES:
            raise ValueError('Combined memory archive exceeds bounds')
        manifest = {'format': FORMAT, 'version': VERSION,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'databases': files,
                    'harness_files': harness_files,
                    'excluded': ['API keys', 'service tokens', 'WeChat login credentials', 'Bilibili login credentials',
                                 'browser profile', 'model weights', 'logs'],
                    'consistency': 'Each database is a consistent SQLite snapshot; scopes are captured sequentially.'}
        # Exclusive creation never overwrites an existing backup.
        created = False
        try:
            with output.open('xb') as stream:
                created = True
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                    for item in files + harness_files:
                        archive.write(staging / item['path'], item['path'])
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
    return manifest


def unpack_verified(archive_path, staging):
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [i.filename for i in entries]
        if len(entries) > MAX_FILES + 1 or len(names) != len(set(names)):
            raise ValueError('Duplicate or excessive archive entries')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 128 * 1024:
            raise ValueError('Missing or oversized manifest')
        if sum(i.file_size for i in entries) > MAX_BYTES + 128 * 1024:
            raise ValueError('Memory archive exceeds size limit')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or manifest.get('version') not in (1, VERSION):
            raise ValueError('Unsupported memory archive format/version')
        files = manifest.get('databases')
        harness_files = manifest.get('harness_files', []) if manifest.get('version') == 2 else []
        if not isinstance(harness_files, list) or len(harness_files) > 500:
            raise ValueError('Invalid Hermes manifest')
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise ValueError('Invalid database manifest')
        expected = set()
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not database_name(item['path']):
                raise ValueError('Invalid database path')
            if item['path'] in expected:
                raise ValueError('Duplicate database manifest entry')
            expected.add(item['path'])
        for item in harness_files:
            if not isinstance(item, dict) or not harness_name(item.get('path')) or item['path'] in expected:
                raise ValueError('Invalid Hermes knowledge path')
            expected.add(item['path'])
        if 'memory.sqlite3' not in expected or set(names) != expected | {'manifest.json'}:
            raise ValueError('Archive contents do not match manifest')
        for item in files + harness_files:
            info = archive.getinfo(item['path'])
            if info.file_size != item.get('bytes'):
                raise ValueError('Database size mismatch')
            target = staging / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if digest(target) != item.get('sha256'):
                raise ValueError('Database checksum mismatch')
            if item in files and describe(target) != item.get('counts'):
                raise ValueError('Database record counts mismatch')
            if item['path'] == 'hermes/state.db':
                with closing(readonly(target)) as db:
                    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('Hermes database integrity check failed')
        return manifest


def inspect_memory(archive_path):
    with tempfile.TemporaryDirectory() as temporary:
        return unpack_verified(archive_path, Path(temporary))


def prepare_restored_database(path):
    """Keep memories while preventing old permissions or deliveries from restarting."""
    with closing(sqlite3.connect(path)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        settings = json.loads(row[0]) if row else {}
        if not isinstance(settings, dict):
            raise ValueError('Invalid saved settings')
        settings.update(online=False, tools_enabled=False, perception=False, sprite_manifest='')
        db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('settings', json.dumps(settings, ensure_ascii=False)))
        # Connection state belongs to the old machine/account, not to the character.
        db.execute("DELETE FROM kv WHERE key LIKE 'wechat:%' OR key LIKE 'bridge_%' OR key='wechat_transport'")
        db.execute("INSERT OR REPLACE INTO kv VALUES('bridge_paused', 'true')")
        row = db.execute("SELECT value FROM kv WHERE key='bili_settings'").fetchone()
        if row:
            bili = json.loads(row[0]); bili.update(enabled=False, share_wechat=False, share_bili=False, share_dm=False)
            db.execute("UPDATE kv SET value=? WHERE key='bili_settings'", (json.dumps(bili),))
        if 'bili_shares' in tables:
            db.execute("UPDATE bili_shares SET status='cancelled' WHERE status='pending'")
            db.execute("UPDATE bili_shares SET status='unknown' WHERE status='queued'")
        if 'bili_mentions' in tables:
            db.execute("UPDATE bili_mentions SET status='cancelled' WHERE status='pending'")
            db.execute("UPDATE bili_mentions SET status='unconfirmed' WHERE status='sending'")
        if 'bili_dms' in tables:
            db.execute("UPDATE bili_dms SET status='cancelled' WHERE status='pending'")
            db.execute("UPDATE bili_dms SET status='unconfirmed' WHERE status='sending'")
        db.execute("UPDATE tasks SET status='interrupted' WHERE status IN ('pending','running','approval')")
        if 'reminders' in tables:
            db.execute('UPDATE reminders SET delivered=1 WHERE delivered=0')
        if 'diary_attempts' in tables:
            db.execute("UPDATE diary_attempts SET status='interrupted' WHERE status='running'")
        if 'conversation_inbox' in tables:
            db.execute("UPDATE conversation_inbox SET state='interrupted' WHERE state IN ('received','processing')")
        if 'bridge_inbox' in tables:
            db.execute("UPDATE bridge_inbox SET state='interrupted' WHERE state IN ('received','processing')")
        if 'bridge_outbox' in tables:
            db.execute("UPDATE bridge_outbox SET state='unknown' WHERE state IN ('pending','sending')")


def import_memory(archive_path, data_dir):
    destination = Path(data_dir).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; import into a NEW data directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.memory-import-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'data'
        staging.mkdir()
        manifest = unpack_verified(archive_path, staging)
        for item in manifest['databases']:
            prepare_restored_database(staging / item['path'])
        (staging / 'migration.json').write_text(json.dumps({
            'imported_at': datetime.now(timezone.utc).isoformat(),
            'source_archive_sha256': digest(Path(archive_path)),
            'source_manifest': manifest,
            'restore_policy': 'Offline; tools/perception disabled; channels unbound; old reminders inactive; no task replay.'
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        # Reserve the destination exclusively, including against a concurrent import.
        destination.mkdir()
        try:
            for item in staging.iterdir():
                item.rename(destination / item.name)
        except BaseException:
            # Preserve partial output for inspection; never remove a user's directory.
            raise RuntimeError('Import interrupted; do not use the partial destination')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description='Export, inspect, or import a portable Little Blue memory copy')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--data-dir', type=Path, default=Path(os.getenv('LITTLE_BLUE_DATA_DIR', 'data/core')))
    export.add_argument('--output', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--archive', type=Path, required=True)
    restore = commands.add_parser('import')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            result = export_memory(args.data_dir, args.output)
        elif args.command == 'inspect':
            result = inspect_memory(args.archive)
        else:
            result = import_memory(args.archive, args.data_dir)
        print(json.dumps({'ok': True, 'command': args.command, 'manifest': result}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError, RuntimeError) as error:
        print(f'Memory transfer failed: {error}', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
