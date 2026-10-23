"""Authenticated Coopanion replication; no transport credentials or persona rewriting."""
import hashlib
import json
import time
from pathlib import PurePosixPath
from typing import Literal
from pydantic import Field, field_validator
from .models import Model


class Event(Model):
    key: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9:_-]+$')
    role: Literal['user', 'assistant']
    text: str = Field(min_length=1, max_length=24000)
    created: float = Field(gt=0, le=4102444800)


class MemoryFile(Model):
    path: str = Field(min_length=1, max_length=240)
    text: str = Field(max_length=120000)

    @field_validator('path')
    @classmethod
    def safe_path(cls, value):
        path = PurePosixPath(value)
        if ('\\' in value or ':' in value or path.is_absolute() or '..' in path.parts
                or any(p.startswith('.') for p in path.parts) or str(path) != value
                or path.parts[0] in ('shared', 'imported') or path.suffix.lower() != '.md'
                or path.name == 'CONSTITUTION.md'):
            raise ValueError('Not a shareable memory path')
        return value


class Sync(Model):
    client: str = Field(pattern=r'^[a-f0-9]{32}$')
    events: list[Event] = Field(default_factory=list, max_length=80)
    files: list[MemoryFile] = Field(default_factory=list, max_length=20)
    removed: list[str] = Field(default_factory=list, max_length=200)
    persona: str | None = Field(None, min_length=1, max_length=24000)
    after: int = Field(0, ge=0)
    memory_after: int = Field(0, ge=0)
    export: bool = True

    @field_validator('removed')
    @classmethod
    def safe_removed(cls, values):
        return [MemoryFile.safe_path(value) for value in values]


def setup(store):
    with store.lock, store.db:
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS companion_events(
          client TEXT, event_key TEXT, fingerprint TEXT NOT NULL, conversation_id INTEGER NOT NULL,
          PRIMARY KEY(client,event_key));
        CREATE TABLE IF NOT EXISTS companion_files(
          client TEXT, path TEXT, text TEXT NOT NULL, sha256 TEXT NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(client,path));
        CREATE TABLE IF NOT EXISTS companion_file_versions(
          client TEXT, path TEXT, sha256 TEXT, text TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(client,path,sha256));
        ''')


def profile(store):
    data = store.get('companion_profile')
    return data if isinstance(data, dict) and data.get('enabled') and data.get('persona') else None


def memory_context(store, query):
    if not profile(store):
        return []
    with store.lock:
        rows = [dict(r) for r in store.db.execute('SELECT path,text,updated FROM companion_files ORDER BY updated DESC')]
    words = query.lower().split()
    rows.sort(key=lambda r: (r['path'] == 'PARTNER.md', sum(w in r['text'].lower() for w in words), r['updated']), reverse=True)
    return [{'path': r['path'], 'text': r['text'][:4000], 'updated': r['updated']} for r in rows[:5]]


def history(store):
    """Offline desktop imports keep their original place in the shared timeline."""
    with store.lock:
        rows = store.db.execute('''SELECT c.* FROM conversation c
          LEFT JOIN conversation_inbox i ON i.message_id=c.id
          WHERE c.delivery IN ('legacy','delivered') AND COALESCE(i.state,'') != 'received'
          ORDER BY c.created DESC,c.id DESC LIMIT 40''').fetchall()
    return [dict(r) for r in reversed(rows)]


def exchange(store, command):
    cmd = Sync.model_validate(command)
    current = profile(store)
    # Single paired desktop: a different client cannot overwrite shared state.
    if current and current['client'] != cmd.client:
        raise PermissionError('Different paired desktop')
    if cmd.persona is not None and current and cmd.persona != current['persona']:
        raise ValueError('Persona is pinned; explicit migration is required to change it')
    if not current and cmd.persona is None:
        raise PermissionError('Pairing requires the current persona')
    now = time.time()
    with store.lock, store.db:
        # Use SQL in this transaction: Store.put would commit nested changes too early.
        if not current:
            current = {'enabled': True, 'client': cmd.client, 'persona': cmd.persona,
                       'sha256': hashlib.sha256(cmd.persona.encode()).hexdigest()}
            store.db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('companion_profile', json.dumps(current, ensure_ascii=False)))
        for event in cmd.events:
            digest = hashlib.sha256(json.dumps(event.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            old = store.db.execute('SELECT fingerprint FROM companion_events WHERE client=? AND event_key=?', (cmd.client, event.key)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError('Conflicting event replay')
                continue
            mid = store.db.execute('INSERT INTO conversation(role,origin,text,created,delivery) VALUES(?,?,?,?,?)',
                                  (event.role, 'desktop', event.text, event.created, 'legacy')).lastrowid
            store.db.execute('INSERT INTO companion_events VALUES(?,?,?,?)', (cmd.client, event.key, digest, mid))
            if event.role == 'user':
                prior = store.db.execute("SELECT value FROM kv WHERE key='last_user_at'").fetchone()
                value = max(float(prior[0]) if prior else 0, min(event.created, now))
                store.db.execute("INSERT OR REPLACE INTO kv VALUES('last_user_at',?)", (str(value),))
        for file in cmd.files:
            digest = hashlib.sha256(file.text.encode()).hexdigest()
            store.db.execute('INSERT OR IGNORE INTO companion_file_versions VALUES(?,?,?,?,?)',
                             (cmd.client, file.path, digest, file.text, now))
            store.db.execute('''INSERT INTO companion_files VALUES(?,?,?,?,?) ON CONFLICT(client,path)
              DO UPDATE SET text=excluded.text,sha256=excluded.sha256,updated=excluded.updated
              WHERE companion_files.sha256<>excluded.sha256''', (cmd.client, file.path, file.text, digest, now))
        for path in cmd.removed:
            if any(f.path == path for f in cmd.files):
                raise ValueError('File cannot be updated and removed in one batch')
            # Historical versions remain recoverable; deleted notes stop influencing chat.
            store.db.execute('DELETE FROM companion_files WHERE client=? AND path=?', (cmd.client, path))
        store.db.execute("INSERT OR REPLACE INTO kv VALUES('companion_last_seen',?)", (str(now),))
        response = {'accepted': [e.key for e in cmd.events], 'files': {f.path: hashlib.sha256(f.text.encode()).hexdigest() for f in cmd.files},
                    'removed': cmd.removed, 'persona_sha256': current['sha256'], 'wechat': None}
        if cmd.export:
            rows = store.db.execute('''SELECT c.*,e.client as companion_client,e.event_key as companion_key
              FROM conversation c LEFT JOIN companion_events e ON c.id=e.conversation_id
              WHERE c.id>? ORDER BY c.id LIMIT 500''', (cmd.after,)).fetchall()
            tail = store.db.execute('''SELECT c.*,e.client as companion_client,e.event_key as companion_key
              FROM conversation c LEFT JOIN companion_events e ON c.id=e.conversation_id ORDER BY c.id DESC LIMIT 100''').fetchall()
            memories = store.db.execute('''SELECT m.*,COALESCE(mm.status,'active') AS status FROM memories m
              LEFT JOIN memory_meta mm ON m.id=mm.memory_id WHERE m.id>? ORDER BY m.id LIMIT 500''', (cmd.memory_after,)).fetchall()
            response.update(conversation=[dict(r) for r in rows], tail=[dict(r) for r in reversed(tail)],
                            memories=[dict(r) for r in memories],
                            memory_status={str(r['id']): r['status'] for r in store.db.execute("SELECT m.id,COALESCE(mm.status,'active') AS status FROM memories m LEFT JOIN memory_meta mm ON m.id=mm.memory_id")},
                            diaries=[dict(r) for r in store.db.execute('SELECT * FROM diaries ORDER BY day')],
                            counts={t: store.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('conversation','memories','diaries')},
                            after=rows[-1]['id'] if rows else cmd.after,
                            memory_after=memories[-1]['id'] if memories else cmd.memory_after,
                            more=len(rows)==500 or len(memories)==500)
        return response


def install_routes(app, core):
    from aiohttp import web
    setup(core.store)
    async def sync(request):
        if core.handoff_requested:
            raise RuntimeError('Maintenance in progress')
        result = exchange(core.store, await request.json())
        result['wechat'] = {'state': core.wechat_state, 'ready': core.wechat_ready}
        return web.json_response(result)
    app.router.add_post('/companion/sync', sync)
