import asyncio
import json
import time
import uuid

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer
from little_blue.core import Core
from little_blue.models import Settings
from little_blue.service import create_app, CORE_KEY
from little_blue.storage import Store

TOKEN = 'test-core-token-' * 4
HOST = 'a' * 32


@pytest.fixture
async def api(tmp_path):
    app = create_app(Store(tmp_path / 'core.db'), TOKEN)
    async with TestClient(TestServer(app)) as client:
        client.session.headers['Authorization'] = 'Bearer ' + TOKEN
        yield client, app[CORE_KEY]


async def rpc(client, op, **kwargs):
    response = await client.post('/rpc', json={'op': op, 'host': HOST, **kwargs})
    return response, await response.json()


async def claim(client):
    response, state = await rpc(client, 'sync', environment={'hour': 12})
    assert response.status == 200
    return state


async def test_auth_origin_and_schema_boundary(api):
    client, core = api
    assert (await client.get('/healthz', headers={'Authorization': ''})).status == 200
    assert (await client.get('/state', headers={'Authorization': ''})).status == 401
    assert (await client.get('/state', headers={'Origin': 'https://example.com'})).status == 403
    assert (await client.post('/rpc', json={'op': 'shell', 'host': HOST})).status == 400
    assert (await rpc(client, 'configure'))[0].status == 403
    await claim(client)
    assert (await rpc(client, 'configure', settings={'online': True, 'api_key': 'NEVER_ECHO'}))[0].status == 400
    response = await client.post('/rpc', json={'op': 'configure', 'host': HOST,
                                             'settings': {'api_key': 'NEVER_ECHO'}})
    assert 'NEVER_ECHO' not in await response.text()


async def test_identity_control_requires_host_and_rejects_core_edits(api):
    client, core = api
    assert (await rpc(client, 'identity'))[0].status == 403
    await claim(client)
    response, card = await rpc(client, 'identity')
    assert response.status == 200 and card['enabled']
    assert 'DeepSeek' in card['core']
    response, card = await rpc(client, 'identity', identity_action='off')
    assert response.status == 200 and not card['enabled']
    assert (await rpc(client, 'identity', identity_action='rename'))[0].status == 400
    assert (await rpc(client, 'identity', identity_action='undo', revision=0))[0].status == 400


async def test_memory_index_controls_require_host_and_preserve_source(api):
    client, core = api
    assert (await rpc(client, 'memory_index', memory_action='off'))[0].status == 403
    await claim(client)
    core.store.remember('喜欢蓝莓茶', 'semantic')
    response, status = await rpc(client, 'memory_index', memory_action='off')
    assert response.status == 200 and not status['enabled']
    assert (await rpc(client, 'memory_index', memory_action='erase'))[0].status == 400
    await rpc(client, 'memory_index', memory_action='rebuild')
    _, result = await rpc(client, 'search', text='蓝莓')
    assert result['rows'][0]['text'] == '喜欢蓝莓茶'


async def test_virtual_phone_rpc_requires_host_validates_and_prevents_replay(api):
    client, core = api
    command = {'action': 'take', 'app': '2048', 'revision': core.phone.data.revision}
    assert (await rpc(client, 'phone', phone=command))[0].status == 403
    await claim(client)
    await rpc(client, 'sync', environment={'hour': 12, 'phone_open': True})
    response, phone = await rpc(client, 'phone', phone=command)
    assert response.status == 200 and phone['owner'] == 'user'
    assert (await rpc(client, 'phone', phone=command))[0].status == 409
    assert (await rpc(client, 'phone', phone={'action': 'move', 'direction': 'shell', 'revision': phone['revision']}))[0].status == 400
    response, phone = await rpc(client, 'phone', phone={'action': 'auto_off', 'revision': phone['revision']})
    assert response.status == 200 and not phone['autonomous']
    assert core.store.get('phone')['autonomous'] is False
    assert not core.settings.online and not core.settings.tools_enabled
    _, phone = await rpc(client, 'phone', phone={'action': 'take', 'app': 'aquarium', 'revision': phone['revision']})
    response, error = await rpc(client, 'phone', phone={'action': 'add_fish', 'app': 'aquarium',
                                                      'species': 'whale', 'revision': phone['revision']})
    assert response.status == 400 and error['error'] == 'game_rule' and '收藏' in error['message']
    core.submit('/phone', origin='wechat')
    result = await core.job
    assert '自主最高' in result and '鱼缸' in result
    await rpc(client, 'disconnect')
    await asyncio.sleep(.35)
    assert core.phone.owner == 'none'


async def test_single_host_and_perception_revocation(api):
    client, core = api
    await claim(client)
    second = await client.post('/rpc', json={'op': 'sync', 'host': 'b' * 32, 'environment': {'hour': 12}})
    assert second.status == 409
    _, state = await rpc(client, 'sync', environment={'hour': 12, 'idle_seconds': 999, 'focused': True})
    assert state['environment']['idle_seconds'] is None
    assert not state['environment']['focused']


async def test_life_runs_while_model_pending_and_cancel_persists(api):
    client, core = api
    await claim(client)
    await rpc(client, 'configure', settings={'online': True})
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    core.agent.client.complete = blocked
    task_id = uuid.uuid4().hex
    assert (await rpc(client, 'submit', text='hello', task_id=task_id))[0].status == 200
    await asyncio.wait_for(entered.wait(), 1)
    before = core.life.elapsed
    await asyncio.sleep(.6)
    assert core.life.elapsed > before + .25
    assert (await rpc(client, 'cancel'))[0].status == 200
    assert core.store.tasks()[0]['status'] == 'cancelled'
    assert core.current is None
    assert (await rpc(client, 'submit', text='hello', task_id=task_id))[0].status == 400


async def test_offline_memory_and_restart_state(tmp_path):
    path = tmp_path / 'core.db'
    core = Core(Store(path))
    core.claim_host(HOST, {'hour': 12})
    core.interact('pet')
    trust = core.life.needs.trust
    core.submit('你好')
    await core.job
    assert core.store.search('你好')
    core.store.task('interrupted', 'running', 'do not resume')
    await core.close()
    restored = Core(Store(path))
    assert restored.life.needs.trust == trust
    assert any(t['status'] == 'interrupted' for t in restored.store.tasks())
    assert restored.job is None
    await restored.close()


async def test_approval_revocation_and_no_side_effect(api):
    client, core = api
    await claim(client)
    await rpc(client, 'configure', settings={'tools_enabled': True})
    task = asyncio.create_task(core.tools.call('create_reminder', {'text': 'do not add', 'seconds': 1}))
    await asyncio.sleep(.01)
    state = await claim(client)
    request = state['pending'][0]
    assert request['kind'] == 'approval'
    await rpc(client, 'configure', settings={'tools_enabled': False})
    await rpc(client, 'resolve', token=request['token'], value=True)
    with pytest.raises(PermissionError):
        await task
    assert core.store.due_reminders(1e20) == []
    assert (await rpc(client, 'resolve', token=request['token'], value=True))[0].status == 400


async def test_windows_path_is_delegated_not_read_in_container(api):
    client, core = api
    await claim(client)
    await rpc(client, 'configure', settings={'tools_enabled': True})
    task = asyncio.create_task(core.tools.call('read_file', {'path': r'C:\approved.txt'}))
    await asyncio.sleep(.01)
    request = (await claim(client))['pending'][0]
    assert request['kind'] == 'host_tool'
    assert request['args']['path'] == r'C:\approved.txt'
    await rpc(client, 'resolve', token=request['token'], value='synthetic text')
    assert await task == 'synthetic text'


async def test_disconnect_cancels_approval_and_releases_drag(api):
    client, core = api
    await claim(client)
    core.interact('drag')
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await core.ask('approval', 'create_reminder', {}, 'test', 120)
    core.agent._run = blocked
    core.submit('test')
    await entered.wait()
    await rpc(client, 'disconnect')
    await asyncio.sleep(.3)
    assert not core.pending
    assert core.life.action != 'drag'
    assert core.store.tasks()[0]['status'] == 'cancelled'


async def test_reminders_wait_for_host_and_dnd(api):
    client, core = api
    core.store.add_reminder('test', time.time() - 1)
    await asyncio.sleep(.3)
    assert not core.events
    await claim(client)
    await rpc(client, 'configure', settings={'dnd': True})
    await asyncio.sleep(.3)
    assert not core.events
    await rpc(client, 'configure', settings={'dnd': False})
    await asyncio.sleep(.3)
    assert any(e['kind'] == 'reminder' for e in core.events)


async def test_real_agent_tool_loop_through_remote_approval(api):
    client, core = api
    await claim(client)
    await rpc(client, 'configure', settings={'online': True, 'tools_enabled': True})
    from little_blue.cognition import DeepSeekClient
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        message = {'content': 'done'} if len(requests) > 1 else {
            'content': None, 'tool_calls': [{'id': 'call', 'type': 'function', 'function': {
                'name': 'create_reminder', 'arguments': '{"text":"test","seconds":60}'}}]}
        return httpx.Response(200, json={'choices': [{'message': message}]})
    core.agent.client = DeepSeekClient(core.store, core.settings, httpx.MockTransport(handler), 'fake-key')
    await rpc(client, 'submit', text='remind me', task_id=uuid.uuid4().hex)
    for _ in range(50):
        if core.pending:
            break
        await asyncio.sleep(.01)
    request = (await claim(client))['pending'][0]
    await rpc(client, 'resolve', token=request['token'], value=True)
    await asyncio.wait_for(core.job, 1)
    assert core.store.tasks()[0]['status'] == 'succeeded'
    assert len(requests) == 2
    assert core.store.due_reminders(1e20)[0]['text'] == 'test'
