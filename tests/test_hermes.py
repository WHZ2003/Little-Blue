import asyncio
from types import SimpleNamespace

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_blue.hermes import HermesBridge
from little_blue.models import Settings
from little_blue.service import create_app, CORE_KEY
from little_blue.storage import Store


def test_turn_guidance_stays_after_delivered_history():
    from little_blue.hermes import conversation_parts
    messages = [{'role': 'system', 'content': 'identity'},
                {'role': 'system', 'content': 'context'},
                {'role': 'user', 'content': 'old user'},
                {'role': 'assistant', 'content': 'old reply'},
                {'role': 'system', 'content': 'current turn guidance'},
                {'role': 'user', 'content': 'current user'}]
    system, history = conversation_parts(messages)
    assert system == 'identity\n\ncontext'
    assert history == messages[2:-1]
    # A first conversation still has all its system rules and no invented history.
    assert conversation_parts(messages[:2] + messages[-1:]) == ('identity\n\ncontext', [])


async def test_last_model_call_is_reserved_for_answer(tmp_path,monkeypatch):
    monkeypatch.setenv('LITTLE_BLUE_HERMES_URL','http://hermes:8787')
    store=Store(tmp_path/'memory.sqlite3')
    calls=[]
    async def complete(messages,tools):
        calls.append((messages,tools))
        assert tools is None and messages[-1]['role']=='system'
        return {'content':'Only the verified results.'}
    bridge=HermesBridge(SimpleNamespace(store=store,settings=Settings(online=True),client=SimpleNamespace(complete=complete)))
    bridge.active={'calls':4}
    result=await bridge.complete({'messages':[{'role':'user','content':'research'}],'tools':[{'type':'function'}]})
    assert result['choices'][0]['message']['content']=='Only the verified results.' and len(calls)==1
    with pytest.raises(RuntimeError): await bridge.complete({'messages':[]})
    store.close()


def test_worker_refreshes_stale_identity_without_losing_previous_card(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('dafeiyu_worker', Path(__file__).parents[1] / 'deploy/hermes/worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    soul = tmp_path / 'SOUL.md'
    soul.write_text('previous app persona', encoding='utf-8')
    worker.refresh_persona(tmp_path, 'current app persona')
    assert soul.read_text(encoding='utf-8') == 'current app persona'
    assert (tmp_path / 'SOUL.before-app-refresh.md').read_text(encoding='utf-8') == 'previous app persona'
    worker.refresh_persona(tmp_path, 'next app persona')
    assert soul.read_text(encoding='utf-8') == 'next app persona'
    assert (tmp_path / 'SOUL.before-app-refresh.md').read_text(encoding='utf-8') == 'previous app persona'
    assert not (tmp_path / '.SOUL.app.tmp').exists()


async def test_proxy_authority_expires_and_never_uses_core_token(tmp_path, monkeypatch):
    monkeypatch.setenv('LITTLE_BLUE_HERMES_URL', 'http://hermes:8787')
    app = create_app(Store(tmp_path / 'core.db'), 'core-token-' * 5)
    core = app[CORE_KEY]
    core.settings.online = True
    called = []
    class Client:
        async def complete(self, messages, tools):
            called.append(messages)
            return {'role': 'assistant', 'content': 'ok'}
    core.agent.client = Client()
    bridge = core.agent.harness
    bridge.active = {'token': 'a' * 64, 'calls': 0, 'tools': {}}
    body = {'messages': [{'role': 'user', 'content': 'test'}]}
    async with TestClient(TestServer(app)) as client:
        for token in ('', 'core-token-' * 5):
            r = await client.post('/harness/v1/chat/completions', headers={'Authorization': 'Bearer ' + token}, json=body)
            assert r.status == 401
        headers = {'Authorization': 'Bearer ' + 'a' * 64}
        r = await client.post('/harness/v1/chat/completions', headers=headers, json=body)
        assert r.status == 200 and len(called) == 1
        r = await client.post('/harness/tool', headers=headers, json={'name': 'dafeiyu_read_file', 'arguments': {'path': '/etc/passwd'}})
        assert r.status == 403
        core.store.continuity.append('assistant', 'blue notebook confirmed', 'wechat', delivery='delivered')
        core.store.continuity.append('assistant', 'blue notebook draft', 'wechat', delivery='pending')
        r = await client.post('/harness/tool', headers=headers, json={'name': 'dafeiyu_memory_search', 'arguments': {'query': 'blue notebook'}})
        assert r.status == 200
        assert [row['text'] for row in (await r.json())['data']['conversation']] == ['blue notebook confirmed']
        bridge.active['private_memory'] = False
        r = await client.post('/harness/tool', headers=headers, json={'name': 'dafeiyu_memory_search', 'arguments': {'query': 'private'}})
        assert r.status == 403
        bridge.active['calls'] = 5
        r = await client.post('/harness/v1/chat/completions', headers=headers, json=body)
        assert r.status == 429 and len(called) == 1
        bridge.active = None
        r = await client.post('/harness/v1/chat/completions', headers=headers, json=body)
        assert r.status == 401


async def test_cancel_revokes_before_worker_cancel_and_preserves_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('LITTLE_BLUE_HERMES_URL', 'http://hermes:8787')
    token_file = tmp_path / 'token'
    token_file.write_text('synthetic-control-token')
    monkeypatch.setenv('LITTLE_BLUE_HERMES_TOKEN_FILE', str(token_file))
    store = Store(tmp_path / 'memory.db')
    agent = SimpleNamespace(store=store, settings=Settings(online=True), tools=None)
    bridge = HermesBridge(agent)
    started = asyncio.Event()
    cancelled = []
    async def handle(request):
        if request.url.path == '/run':
            import json
            payload = json.loads(request.content)
            assert [t['function']['name'] for t in payload['tools']] == ['dafeiyu_memory_search']
            started.set()
            await asyncio.Event().wait()
        assert request.url.path == '/cancel'
        assert bridge.active is None
        cancelled.append(True)
        return httpx.Response(200, json={'ok': True})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    task = asyncio.create_task(bridge.run([{'role': 'system', 'content': 'companion'}, {'role': 'user', 'content': 'hello'}], False, 'a' * 32))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled and bridge.active is None
    store.close()
