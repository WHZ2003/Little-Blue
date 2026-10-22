import asyncio
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_blue.core import Core
from little_blue.diary import ZONE, parse_command
from little_blue.memory_transfer import prepare_restored_database
from little_blue.service import CORE_KEY, create_app
from little_blue.storage import Store
from little_blue.wechat import Account, WeChatChannel


@pytest.fixture
def core(tmp_path):
    c = Core(Store(tmp_path / 'memory.sqlite3'))
    c.settings.online = True
    yield c
    c.store.close()


def seed(core, text='今天终于把代码调通了', stamp=None, delivery='legacy', role='user'):
    return core.store.continuity.append(role, text, 'wechat', now=stamp, delivery=delivery)


class Client:
    def __init__(self, content='今天他把代码调通了，我也跟着开心。很喜欢听他讲这些小事。'):
        self.calls = []
        self.content = content

    async def complete(self, messages):
        self.calls.append(messages)
        return {'content': self.content}


async def write(core, day=None):
    ident = core.diary.start(day)
    return await core.job if ident else core.diary.read(day or core.diary.today())


async def test_grounded_idempotent_private_entry_and_restart(core):
    mid = seed(core)
    seed(core, '失败的回复不能成为经历', delivery='failed', role='assistant')
    seed(core, '/diary write')
    seed(core, '日记 · 2026-10-22\n旧日记正文', role='assistant', delivery='delivered')
    client = Client(); core.agent.client = client
    before = core.store.db.execute('SELECT COUNT(*) FROM conversation').fetchone()[0]
    entry = await write(core)
    assert entry['sources'] == [{'type': 'conversation', 'id': mid, 'role': 'user', 'text': '今天终于把代码调通了'}]
    assert entry['body'] == client.content and core.current is None
    assert not core.store.search('') and not core.store.semantic()
    assert core.store.db.execute('SELECT COUNT(*) FROM conversation').fetchone()[0] == before
    assert await write(core) == entry and len(client.calls) == 1
    assert not core.events
    from little_blue.diary import Diary
    assert Diary(core).read()['body'] == entry['body']
    prepare_restored_database(core.store.path)
    assert core.diary.read()['body'] == entry['body']


async def test_failure_or_cancel_preserves_original_and_serializes(core):
    seed(core); core.agent.client = Client()
    original = await write(core)
    seed(core, '后来又吃了蛋糕')
    with pytest.raises(ValueError, match='五分钟'):
        core.diary.start()
    with core.store.db:
        core.store.db.execute('UPDATE diary_attempts SET attempted=0')
    core.agent.client = Client('')
    assert 'error' in await write(core)
    assert core.diary.read()['body'] == original['body']
    with core.store.db:
        core.store.db.execute('UPDATE diary_attempts SET attempted=0')
    started = asyncio.Event()
    class Slow:
        async def complete(self, messages):
            started.set()
            await asyncio.Event().wait()
    core.agent.client = Slow()
    core.diary.start(); await started.wait()
    with pytest.raises(RuntimeError):
        core.submit('另一个任务')
    await core.cancel()
    assert core.current is None and core.diary.read()['body'] == original['body']
    assert core.diary.rows('SELECT status FROM diary_attempts')[0]['status'] == 'cancelled'


async def test_hermes_uses_evidence_only_and_no_tools(core):
    seed(core)
    class Harness:
        async def run(self, messages, tools, ident, *, private_memory):
            assert not tools and not private_memory
            assert json.loads(messages[-1]['content'])['sources'][0]['role'] == 'user'
            return {'content': '今天有一件开心的小事，他说代码终于调通了。'}
    core.agent.harness = Harness()
    assert '代码' in (await write(core))['body']


async def test_daily_schedule_dst_and_once_only(core):
    # New York's fall DST day has 25 hours, with both repeated 01:30 hours included.
    day = '2025-11-02'
    for fold in (0, 1):
        stamp = datetime(2025, 11, 2, 1, 30, tzinfo=ZoneInfo(ZONE), fold=fold).timestamp()
        seed(core, f'重复小时 {fold}', stamp)
    seed(core, '明天', datetime(2025,11,3,tzinfo=ZoneInfo(ZONE)).timestamp())
    assert len(core.diary.evidence(day)) == 2
    client = Client(); core.agent.client = client
    now = datetime(2025,11,2,23,1,tzinfo=ZoneInfo(ZONE)).timestamp()
    core.diary.tick(now); await core.job
    assert core.diary.read(day)
    core.diary.next_check = 0
    core.diary.tick(now+3600)
    assert len(client.calls) == 1
    assert day in core.store.get('diary_auto_days')


async def test_no_evidence_offline_and_future_never_call_provider(core):
    client = Client(); core.agent.client = client
    with pytest.raises(ValueError, match='记录'):
        core.diary.start()
    seed(core)
    with pytest.raises(ValueError):
        core.diary.start('2999-01-01')
    core.settings.online = False
    with pytest.raises(ValueError, match='关闭'):
        core.diary.start()
    assert not client.calls


async def test_wechat_write_read_and_list_without_external_delivery(core):
    seed(core); core.agent.client = Client()
    class API:
        def __init__(self): self.sent = []
        async def send(self, context, text): self.sent.append(text)
    api = API(); channel = WeChatChannel(core, Account(token='test',owner='owner',bot='bot'),api)
    await channel.consume_one(('写日记', 'context', None))
    assert api.sent[-1].startswith('日记 · ')
    await channel.consume_one(('/diary list', 'context', None))
    assert core.diary.today() in api.sent[-1]
    core.settings.online = False
    await channel.consume_one(('看看日记', 'context', None))
    assert api.sent[-1].startswith('日记 · ') and len(core.agent.client.calls) == 1
    await channel.consume_one(('关闭日记', 'context', None))
    assert not core.diary.status()['enabled']


async def test_authenticated_api_and_handoff(tmp_path):
    token = 'test-token-' * 4
    app = create_app(Store(tmp_path/'api.db'), token, client=Client())
    async with TestClient(TestServer(app)) as api:
        assert (await api.post('/diary', json={'action':'read'})).status == 401
        api.session.headers['Authorization'] = 'Bearer '+token
        assert (await api.post('/diary', json={'action':'read'},headers={'Origin':'https://invalid.test'})).status == 403
        c = app[CORE_KEY]; c.settings.online = True; seed(c)
        r = await api.post('/diary',json={'action':'write'})
        assert r.status == 200 and (await r.json())['task_id']
        await c.job
        r = await api.post('/diary',json={'action':'read'})
        assert (await r.json())['entry']['body']
        assert (await api.post('/diary',json={'action':'read','day':'2026-02-30'})).status == 400
        c.handoff_requested = True
        assert (await api.post('/diary',json={'action':'write'})).status == 409
        assert (await api.post('/diary',json={'action':'read'})).status == 200


def test_commands_are_explicit():
    assert parse_command('今天看日记的时候笑了') is None
    assert parse_command('看看日记') == ('read', None)
    assert parse_command('日记 2026-10-22') == ('read', '2026-10-22')


async def test_cancelled_manual_diary_does_not_stop_wechat_consumer(core):
    seed(core)
    started = asyncio.Event()
    class Slow:
        async def complete(self, messages):
            started.set()
            await asyncio.Event().wait()
    core.agent.client = Slow()
    task = asyncio.create_task(core.diary.command('write'))
    await started.wait()
    await core.cancel()
    assert '取消' in await task
    assert core.diary.read() is None


async def test_disable_stops_automatic_diary_and_failure_is_not_auto_replayed(core):
    seed(core)
    started = asyncio.Event()
    class Slow:
        async def complete(self, messages):
            started.set()
            await asyncio.Event().wait()
    core.agent.client = Slow()
    core.diary.start(automatic=True); await started.wait()
    await core.diary.command('off')
    assert core.current is None and not core.diary.status()['enabled']
    assert core.diary.read() is None


async def test_portable_archive_preserves_diary_and_sources(core, tmp_path):
    from pathlib import Path
    from little_blue.memory_transfer import export_memory, import_memory
    seed(core); core.agent.client = Client()
    original = await write(core)
    archive = tmp_path/'diary.zip'
    export_memory(Path(core.store.path).parent, archive)
    target = tmp_path/'restored'
    import_memory(archive, target)
    restored = Core(Store(target/'memory.sqlite3'))
    try:
        assert restored.diary.read() == original
        assert not restored.settings.online
    finally:
        restored.store.close()
