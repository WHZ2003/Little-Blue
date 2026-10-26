import asyncio
import httpx
import pytest
from little_blue.cognition import Agent, DeepSeekClient, ProactiveGate
from little_blue.models import Environment, Needs, Settings
from little_blue.storage import Store
from little_blue.tools import ToolRuntime
from little_blue.persona import SYSTEM_PERSONA


@pytest.fixture
def components(tmp_path):
    store = Store(tmp_path / 'pet.db')
    settings = Settings(online=True, tools_enabled=True, daily_tokens=100000)
    async def approve(*args):
        return True
    yield store, settings, ToolRuntime(store, settings, approve)
    store.close()


async def test_mock_deepseek_multi_step_tool_conversation(components):
    store, settings, tools = components
    requests = []
    def handler(request):
        import json
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {'role':'assistant','content':None,'tool_calls':[{'id':'one','type':'function',
                       'function':{'name':'get_time','arguments':'{}'}}]}
        else:
            assert body['messages'][-1]['role'] == 'tool'
            message = {'role':'assistant','content':'现在是下午，休息一下吧。'}
        return httpx.Response(200, json={'choices':[{'message':message}]})
    client = DeepSeekClient(store,settings,httpx.MockTransport(handler),api_key='test-key')
    agent = Agent(store,settings,tools,client)
    assert '下午' in await agent.run('现在几点', {}, task_id='task')
    assert len(requests) == 2
    assert store.tasks()[0]['status'] == 'succeeded'


async def test_offline_never_calls_transport(components):
    store, settings, tools = components
    settings.online = False
    def handler(request):
        raise AssertionError('No network allowed')
    client = DeepSeekClient(store,settings,httpx.MockTransport(handler),api_key='test')
    result = await Agent(store,settings,tools,client).run('你好',{})
    assert '离线' in result


@pytest.mark.parametrize('online', [False, True])
async def test_current_identity_survives_historical_nickname(components, online):
    store, settings, tools = components
    store.growth.control('off')  # This test isolates the conversation request.
    settings.online = online
    store.remember('大肥鱼是以前使用的名字。', 'semantic')
    requests = []
    def handler(request):
        import json
        body = json.loads(request.content)
        requests.append(body)
        assert body['messages'][0] == {'role': 'system', 'content': SYSTEM_PERSONA}
        assert '默认身份是 DeepSeek' in SYSTEM_PERSONA
        assert '不是你的正式名字' in SYSTEM_PERSONA
        assert '微信文字聊天' in body['messages'][1]['content']
        assert '大肥鱼是以前使用的名字' in body['messages'][2]['content']
        assert 'tools' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': '我是 DeepSeek。'}}]})
    client = DeepSeekClient(store, settings, httpx.MockTransport(handler), api_key='test')
    await Agent(store, settings, tools, client).run('大肥鱼，你是谁？', {}, chat_only=True)
    assert len(requests) == int(online)
    # Unconfirmed WeChat drafts are not durable relationship memories.
    assert store.continuity.recent()[-1]['delivery'] == 'pending'
    assert store.search('', 1)[0]['kind'] == 'semantic'


async def test_api_error_is_sanitized_and_task_failed(components):
    store, settings, tools = components
    client = DeepSeekClient(store,settings,httpx.MockTransport(lambda r: httpx.Response(401,text='secret')),api_key='test')
    with pytest.raises(RuntimeError, match='HTTP 401') as error:
        await Agent(store,settings,tools,client).run('hello',{})
    assert 'secret' not in str(error)
    assert store.tasks()[0]['status'] == 'failed'


async def test_client_settles_reported_usage_and_retains_ambiguous_failure(components):
    store, settings, _ = components
    client = DeepSeekClient(store, settings, httpx.MockTransport(lambda r: httpx.Response(200, json={
        'choices': [{'message': {'content': '你好'}}],
        'usage': {'prompt_tokens': 45, 'completion_tokens': 5, 'total_tokens': 50}})), api_key='test')
    await client.complete([{'role': 'user', 'content': '你好'}])
    assert store.budget_usage() == {'requests': 1, 'reserved': 50}
    def fail(request):
        raise httpx.ReadTimeout('secret request details')
    client.transport = httpx.MockTransport(fail)
    with pytest.raises(httpx.ReadTimeout):
        await client.complete([{'role': 'user', 'content': '第二句'}])
    assert store.budget_usage()['requests'] == 2
    assert store.budget_usage()['reserved'] > 50


async def test_budget_rejection_happens_before_http_and_is_specific(components):
    from little_blue.errors import BudgetExceeded, public_failure
    store, settings, _ = components
    settings.daily_tokens = 1
    def forbidden(request):
        raise AssertionError('Budget-exhausted request reached network')
    client = DeepSeekClient(store, settings, httpx.MockTransport(forbidden), api_key='test')
    with pytest.raises(BudgetExceeded) as error:
        await client.complete([{'role': 'user', 'content': '你好'}])
    assert '本地 Token 预算拦截' in public_failure(error.value)
    assert store.budget_usage() == {'requests': 0, 'reserved': 0}
    assert 'secret' not in public_failure(httpx.ReadTimeout('secret'))


async def test_cancel_network_task_is_persisted(components):
    store, settings, tools = components
    entered = asyncio.Event()
    async def handler(request):
        entered.set()
        await asyncio.Event().wait()
    client = DeepSeekClient(store,settings,httpx.MockTransport(handler),api_key='test')
    job = asyncio.create_task(Agent(store,settings,tools,client).run('hello',{}))
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert store.tasks()[0]['status'] == 'cancelled'


async def test_proactive_cannot_invoke_tools(components):
    store, settings, tools = components
    message = {'tool_calls':[{'id':'one','function':{'name':'get_time','arguments':'{}'}}]}
    client = DeepSeekClient(store,settings,httpx.MockTransport(lambda r: httpx.Response(200,json={'choices':[{'message':message}]})),api_key='test')
    with pytest.raises(PermissionError):
        await Agent(store,settings,tools,client).run('say hi',{},proactive=True)


def test_proactive_cooldown_focus_and_rate_limit(components):
    store, settings, tools = components
    gate = ProactiveGate(store)
    gate.started = 0
    needs = Needs(social=90)
    env = Environment(idle_seconds=100)
    assert gate.eligible(1000,settings,env,needs)
    gate.claim(1000)
    assert not gate.eligible(1001,settings,env,needs)
    settings.dnd = True
    assert not gate.eligible(2000,settings,env,needs)
    settings.dnd = False
    env.focused = True
    assert not gate.eligible(2000,settings,env,needs)
    env.focused = False
    env.idle_seconds = 0
    assert not gate.eligible(2000,settings,env,needs)


def test_quiet_checkin_and_recent_user_suppression(components):
    store, settings, _ = components
    gate = ProactiveGate(store)
    gate.started = 100
    needs = Needs(social=10, boredom=10)
    env = Environment()
    assert not gate.eligible(3699, settings, env, needs)
    assert gate.eligible(3700, settings, env, needs)
    store.continuity.append('user', '补充一句', 'wechat', now=3690)
    needs.social = 95
    assert not gate.eligible(3700, settings, env, needs)
    assert gate.eligible(3780, settings, env, needs)


def test_proactive_cooldown_over_an_hour_survives_reload(components):
    store, settings, _ = components
    settings.proactive_cooldown = 7200
    settings.proactive_per_hour = 1
    gate = ProactiveGate(store)
    gate.started = 0
    gate.claim(1000)
    assert not gate.available(5000, settings, Environment())
    restored = ProactiveGate(store)
    restored.started = 0
    assert not restored.available(8199, settings, Environment())
    assert restored.available(8200, settings, Environment())
