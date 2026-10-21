import asyncio
import json
import time

import pytest

from little_blue.core import Core
from little_blue.storage import Store
from browser_service.service import Browser, Operation, allowed, create_app


@pytest.mark.parametrize('url', ['http://en.wikipedia.org/wiki/Fish', 'https://127.0.0.1',
    'https://wikipedia.org.evil.test/', 'https://user:pass@en.wikipedia.org/',
    'file:///etc/passwd', 'https://en.wikipedia.org:8443/', 'https://localhost/', 'data:text/html,hi'])
def test_browser_url_policy_rejects_outside_scope(url):
    assert not allowed(url)


def test_browser_url_policy_allows_only_publication_domains():
    assert allowed('https://zh.wikipedia.org/wiki/鲸')
    assert allowed('https://science.nasa.gov/earth/')
    assert allowed('https://oceanservice.noaa.gov/facts/')


async def test_takeover_invalidates_queued_agent_action_and_old_abort():
    browser = Browser()
    await browser.execute(Operation(action='auto', version=0), human=True)
    await browser.execute(Operation(action='manual', version=1), human=True)
    with pytest.raises(ValueError):
        await browser.execute(Operation(action='visit', version=1, url='https://en.wikipedia.org/wiki/Fish'))
    with pytest.raises(PermissionError):
        await browser.execute(Operation(action='abort', version=2))
    with pytest.raises(PermissionError):
        await browser.execute(Operation(action='visit', version=2, url='https://en.wikipedia.org/wiki/Fish'))
    assert browser.mode == 'manual'


async def test_pause_cancels_current_browser_work():
    browser = Browser()
    browser.mode = 'auto'
    browser.active = asyncio.create_task(asyncio.sleep(99))
    task = browser.active
    await browser.execute(Operation(action='pause', version=0), human=True)
    assert task.cancelled() and browser.mode == 'pause' and browser.version == 1


async def test_browser_auth_ticket_is_single_use_and_mutations_need_origin():
    from aiohttp.test_utils import TestClient, TestServer
    class FakeBrowser(Browser):
        async def start(self):
            pass
        async def close(self):
            pass
    browser = FakeBrowser()
    async with TestClient(TestServer(create_app(browser, 's' * 40))) as client:
        assert (await client.get('/internal/status')).status == 401
        assert (await client.get('/api/status')).status == 401
        response = await client.post('/internal/ticket', headers={'Authorization': 'Bearer ' + 's'*40})
        ticket = (await response.json())['url'].split('ticket=')[1]
        response = await client.get('/?ticket=' + ticket, allow_redirects=False)
        assert response.status == 302
        cookie = response.cookies['browser_session'].value
        assert (await client.get('/?ticket=' + ticket, allow_redirects=False)).status == 401
        headers = {'Host': '127.0.0.1:8766', 'Cookie': 'browser_session=' + cookie}
        assert (await client.get('/api/status', headers=headers)).status == 200
        assert (await client.post('/api/control', headers=headers, json={'action':'auto','version':0})).status == 403
        headers.update(Origin='http://127.0.0.1:8766', **{'X-Browser-Control':'1'})
        assert (await client.post('/api/control', headers=headers, json={'action':'manual','version':0})).status == 200


@pytest.fixture
def core(tmp_path):
    value = Core(Store(tmp_path / 'memory.db'))
    value.settings.online = True
    yield value
    value.store.close()


def ready(core):
    runtime = core.browser
    runtime.started = time.time() - 100
    runtime.state = {'mode':'auto','version':1,'seeds':['https://en.wikipedia.org/wiki/Whale']}
    core.life.needs.curiosity = 80
    return runtime


def test_browser_gates_and_persistent_visit_budget(core):
    browser = ready(core)
    now = time.time()
    assert browser.eligible(now)
    core.settings.dnd = True
    assert not browser.eligible(now)
    core.settings.dnd = False
    core.store.put('last_user_at', now)
    assert not browser.eligible(now)
    core.store.put('last_user_at', 0)
    core.store.put('browser_visits', [now-100])
    assert not browser.eligible(now)  # A restart cannot bypass the 20-minute interval.
    core.store.put('browser_visits', [now-200, now-100])
    assert not browser.eligible(now)
    core.store.put('browser_visits', [now-4000-i for i in range(8)])
    assert not browser.eligible(now)


def test_calm_pet_can_read_occasionally_but_sleep_and_low_energy_win(core):
    from little_blue.models import Action
    browser = ready(core)
    now = time.time()
    core.life.needs.curiosity = core.life.needs.boredom = 5
    assert browser.eligible(now)
    core.store.put('browser_visits', [now - 4000])
    assert not browser.eligible(now)
    core.store.put('browser_visits', [now - 7300])
    assert browser.eligible(now)
    core.life.needs.energy = 20
    assert not browser.eligible(now)
    core.life.needs.energy = 80
    core.life.override, core.life.override_until = Action.SLEEP, float('inf')
    assert not browser.eligible(now)


async def test_only_actual_browser_observations_become_memory(core):
    browser = ready(core)
    calls = []
    async def request(path, data=None):
        calls.append(data)
        return {'title':'Whale', 'url':data['url'], 'text':'A real page excerpt. '*8,
                'observed_at':time.time(), 'links':[{'title':'Ocean', 'url':'https://en.wikipedia.org/wiki/Ocean'}]}
    class Client:
        async def complete(self, messages, schemas):
            return {'content':'{"link":0}'}
    core.agent.client = Client()
    browser.request = request
    await browser.explore()
    assert len(calls) == 2
    assert len(core.store.search('A real page excerpt')) == 2
    assert core.store.get('browser_experience')['url'].endswith('/Ocean')
    assert core.store.tasks()[0]['status'] == 'succeeded'
    assert not core.store.continuity.recent()  # No pretend user messages or unsolicited delivery.


async def test_failed_navigation_never_becomes_an_experience(core):
    browser = ready(core)
    async def fail(*args, **kwargs):
        raise TimeoutError()
    browser.request = fail
    await browser.explore()
    assert core.store.get('browser_experience') is None and not core.store.search('')
    assert core.store.tasks()[0]['status'] == 'failed'


async def test_chat_interrupts_browsing_and_only_version_scoped_abort(core):
    browser = ready(core)
    entered = asyncio.Event()
    calls = []
    async def request(path, data=None):
        calls.append(data)
        if data['action'] == 'visit':
            entered.set()
            await asyncio.Event().wait()
        return {}
    browser.request = request
    job = asyncio.create_task(browser.explore())
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert calls[-1] == {'action':'abort','version':1}
    assert core.store.tasks()[0]['status'] == 'cancelled'
    assert not core.store.search('')
