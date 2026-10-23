import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from little_blue.research import Research, PublicResolver, public_url, fetch, TOOLS
from little_blue.cognition import Agent
from little_blue.models import Settings
from little_blue.storage import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'memory.sqlite3')
    s.growth.control('off')
    yield s
    s.close()


@pytest.mark.parametrize('url', ['http://example.com', 'file:///etc/passwd', 'https://localhost/',
    'https://127.0.0.1/', 'https://100.100.100.200/', 'https://169.254.169.254/',
    'https://10.0.0.1/', 'https://[::1]/', 'https://user:pass@example.com/',
    'https://example.com:8765/', 'https://service.internal/', 'https://example.com\\@evil.com/'])
def test_private_destinations_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


@pytest.mark.parametrize('addresses,allowed', [(['8.8.8.8'], True), (['127.0.0.1'], False),
    (['8.8.8.8','192.168.1.1'], False), (['100.100.100.200'], False), ([], False)])
async def test_dns_results_used_by_connector_are_checked(monkeypatch, addresses, allowed):
    async def resolve(*args, **kwargs):
        return [{'host': ip} for ip in addresses]
    monkeypatch.setattr(aiohttp.resolver.ThreadedResolver, 'resolve', resolve)
    resolver = PublicResolver()
    try:
        if allowed:
            assert await resolver.resolve('public.example', 443) == [{'host':'8.8.8.8'}]
        else:
            with pytest.raises(ValueError):
                await resolver.resolve('public.example', 443)
    finally:
        await resolver.close()


async def test_redirect_to_metadata_is_blocked_before_request(monkeypatch):
    called = []
    class Response:
        status = 302
        headers = {'Location':'https://169.254.169.254/latest/meta-data/'}
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    class Session:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert isinstance(kwargs['cookie_jar'], aiohttp.DummyCookieJar)
            self.connector = kwargs['connector']
        async def __aenter__(self): return self
        async def __aexit__(self,*args): await self.connector.close()
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            called.append(url)
            return Response()
    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    with pytest.raises(ValueError):
        await fetch('https://example.com/')
    assert called == ['https://example.com/']


@pytest.mark.parametrize('target,allowed', [('https://example.edu/article',True),('https://169.254.169.254/',False)])
async def test_search_meta_redirect_is_followed_without_javascript(monkeypatch,target,allowed):
    called=[]
    class Response:
        status=200
        charset='utf-8'
        headers={'Content-Type':'text/html'}
        def __init__(self,url): self.url=url;self.content=self
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        def raise_for_status(self): pass
        async def iter_chunked(self,size):
            body=('<meta http-equiv="refresh" content="0;URL=\''+target+'\'"><script>throw dangerous()</script>'
                  if len(called)==1 else '<p>Actual article content</p>')
            yield body.encode()
    class Session:
        def __init__(self,**kwargs): self.connector=kwargs['connector']
        async def __aenter__(self): return self
        async def __aexit__(self,*a): await self.connector.close()
        def get(self,url,**kwargs): called.append(url);return Response(url)
    monkeypatch.setattr(aiohttp,'ClientSession',Session)
    if allowed:
        final,_,body=await fetch('https://www.so.com/link?m=example')
        assert final==target and 'Actual article' in body and len(called)==2
    else:
        with pytest.raises(ValueError): await fetch('https://www.so.com/link?m=example')
        assert len(called)==1


async def test_search_read_sources_scripts_and_cache(store):
    calls = []
    async def fetcher(url):
        calls.append(url)
        if urlsplit(url).hostname == 'www.bing.com':
            assert parse_qs(urlsplit(url).query)['q'] == ['水母 发光']
            return url, 'text/xml', '<rss><channel><item><title>Jellyfish</title><link>https://example.com/science</link><description>Snippet</description></item><item><link>https://127.0.0.1/</link></item></channel></rss>'
        return url, 'text/html', '<title>Science</title><script>secret-script</script><style>secret-style</style><p>'+('actual source text ' * 700)+'</p><a href="/next">Next</a>'
    r = Research(store, lambda: True, fetcher=fetcher)
    result = await r.call('research_search', {'query':'水母 发光','engine':'bing'})
    assert result['status']=='ok' and len(result['results'])==1 and result['untrusted']
    result = await r.call('research_read', {'url':result['results'][0]['url']})
    assert result['title']=='Science' and result['truncated'] and len(result['text'])==10000
    assert 'secret' not in result['text'] and result['links']==[{'url':'https://example.com/next','title':'Next'}]
    assert await r.call('research_read', {'url':result['url']}) is result
    assert len(calls)==2 and r.count==2


async def test_default_search_uses_result_cards_and_skips_ai_summaries(store):
    async def fetcher(url):
        assert urlsplit(url).hostname == 'www.so.com'
        body = '<div>AI generated answer that is not evidence</div><ul>'
        for target in ('https://example.edu/paper','https://ai.so.com/result','https://wenku.so.com/d/123'):
            body += '<li class="res-list"><h3 class="res-title"><a href="'+target+'">Actual <em>science</em></a></h3><span data-result-summary>Observed <em>excerpt</em>.</span></li>'
        return url, 'text/html', body+'</ul>'
    result = await Research(store, lambda: True, fetcher=fetcher).call('research_search', {'query':'science','engine':'360'})
    assert result['engine']=='360' and result['status']=='ok'
    assert result['results']==[{'title':'Actual science','url':'https://example.edu/paper','snippet':'Observed excerpt.'}]
    assert 'AI generated' not in json.dumps(result)


async def test_sogou_default_extracts_nested_cards_and_resolves_links(store):
    async def fetcher(url):
        assert urlsplit(url).hostname=='www.sogou.com'
        return url,'text/html','<div class="vrwrap">AI answer without a source</div><div class="vrwrap"><style>ignored</style><div><h3 class="vr-title"><a href="/link?url=one">Institute <em>research</em></a></h3><div><div class="fz-mid space-txt">Actual <em>excerpt</em>.</div></div></div></div>'
    result=await Research(store,lambda:True,fetcher=fetcher).call('research_search',{'query':'science'})
    assert result['results']==[{'title':'Institute research','snippet':'Actual excerpt.','url':'https://www.sogou.com/link?url=one'}]


def test_captcha_not_followed_and_plain_public_redirect_uses_tls():
    from little_blue.research import redirect_url,HumanVerificationRequired
    assert redirect_url('https://example.com/','http://example.edu/paper')=='https://example.edu/paper'
    with pytest.raises(HumanVerificationRequired):
        redirect_url('https://www.so.com/s','http://qcaptcha.so.com/?ret=example')
    with pytest.raises(HumanVerificationRequired):
        redirect_url('https://mp.weixin.qq.com/s/example','https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?token=private')


async def test_soft_404_is_not_article_evidence(store):
    async def fetcher(url):
        return url,'text/html','<title>Page Not Found | Institute</title><p>'+('Navigation links '*20)+'</p>'
    result=await Research(store,lambda:True,fetcher=fetcher).call('research_read',{'url':'https://example.edu/missing'})
    assert result['status']=='not_found'


async def test_page_reader_exposes_real_article_links_after_navigation(store):
    async def fetcher(url):
        return url,'text/html','<title>Institute news</title>'+''.join('<a href="/nav/'+str(i)+'/">Nav</a>' for i in range(60))+'<a href="/news/actual-science.html">A real interesting science article</a>'
    result=await Research(store,lambda:True,fetcher=fetcher).call('research_read',{'url':'https://example.edu/'})
    assert result['links'][0]=={'url':'https://example.edu/news/actual-science.html','title':'A real interesting science article'}


async def test_disabling_research_cancels_active_core_job(tmp_path):
    from little_blue.core import Core
    core=Core(Store(tmp_path/'core.sqlite3'), client=SimpleNamespace())
    core.settings.online=True;core.settings.research_enabled=True
    started=asyncio.Event();cancelled=asyncio.Event()
    async def run(*args,**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    core.agent.run=run
    core.submit('查资料',origin='wechat')
    await started.wait()
    await core.configure({**core.settings.model_dump(),'research_enabled':False})
    assert cancelled.is_set() and not core.settings.research_enabled
    await core.close()


async def test_limits_failures_revocation_and_cancel(store):
    enabled = True
    async def fail(url): raise RuntimeError('secret request data')
    r = Research(store, lambda: enabled, proactive=True, fetcher=fail)
    for query in ('first', 'second'):
        result = await r.call('research_search', {'query':query})
        assert result['status']=='failed' and 'secret' not in json.dumps(result)
    assert (await r.call('research_search', {'query':'third'}))['status']=='limit'
    enabled = False
    with pytest.raises(PermissionError):
        await r.call('research_search', {'query':'first'})
    async def cancel(url): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await Research(store, lambda: True, fetcher=cancel).call('research_search', {'query':'cancel'})
    import time
    store.put('research_requests', [time.time()] * 120)
    assert (await Research(store, lambda: True, fetcher=fail).call('research_search', {'query':'new'}))['status']=='limit'


async def test_search_limit_preserves_capacity_for_reading(store):
    async def fetcher(url): return url,'text/plain','Actual source text. '*5
    r=Research(store,lambda:True,fetcher=fetcher)
    await r.call('research_search',{'query':'one'})
    await r.call('research_search',{'query':'two'})
    assert (await r.call('research_search',{'query':'three'}))['status']=='limit'
    assert (await r.call('research_read',{'url':'https://example.com/article'}))['status']=='ok'
    assert r.count==3


async def test_weather_disambiguation_units_and_local_time(store):
    requests = []
    async def fetcher(url):
        requests.append(url)
        if 'geocoding' in url:
            data = {'results': [{'name':'Boston','admin1':region,'country_code':'US','latitude':42.36,'longitude':-71.06}
                                for region in ('Massachusetts','New York')]}
        else:
            assert parse_qs(urlsplit(url).query)['timezone']==['auto']
            data = {'timezone':'America/New_York','current':{'time':'2026-10-22T12:00','temperature_2m':15},
                    'current_units':{'temperature_2m':'°C'},'daily':{'time':['2026-10-22']}}
        return url, 'application/json', json.dumps(data)
    r = Research(store, lambda: True, fetcher=fetcher)
    assert (await r.call('research_weather', {'city':'Boston','country':'US'}))['status']=='ambiguous_location'
    assert len(requests)==1
    result = await r.call('research_weather', {'city':'Boston','country':'US','region':'Massachusetts'})
    assert result['status']=='ok' and result['timezone']=='America/New_York'
    assert result['current_units']['temperature_2m']=='°C' and 'source' in result


@pytest.mark.parametrize('enabled,proactive,reflection', [(True,False,False),(True,True,False),(True,False,True),(False,False,False)])
async def test_hermes_research_authority_and_wechat_scope(store, enabled, proactive, reflection):
    settings = Settings(online=True, research_enabled=enabled, tools_enabled=True)
    agent = Agent(store, settings, None, SimpleNamespace())
    async def run(messages, allow_tools, task_id, **kwargs):
        assert not allow_tools
        names = {t['function']['name'] for t in kwargs.get('extra_tools', [])}
        expected = {t['function']['name'] for t in TOOLS} if enabled and not reflection else set()
        assert names == expected
        if names:
            settings.research_enabled = False
            with pytest.raises(PermissionError):
                await kwargs['tool_handler']('research_search', {'query':'public topic'})
            with pytest.raises(PermissionError):
                await kwargs['tool_handler']('read_file', {'path':'private'})
        return {'content':'好。'}
    agent.harness = SimpleNamespace(run=run)
    await agent.run('今天波士顿冷吗', {}, chat_only=True, proactive=proactive, reflection=reflection)


async def test_direct_model_loop_uses_actual_research_results(store, monkeypatch):
    from little_blue import cognition
    calls = []
    async def research(name, args):
        calls.append((name,args))
        return {'status':'ok','results':[{'title':'Source','url':'https://example.com','snippet':'Actual snippet'}]}
    monkeypatch.setattr(cognition, 'Research', lambda *a,**k: SimpleNamespace(call=research))
    async def complete(messages, tools=None):
        assert {t['function']['name'] for t in tools} == {t['function']['name'] for t in TOOLS}
        if not calls:
            return {'tool_calls':[{'id':'1','function':{'name':'research_search','arguments':'{"query":"science"}'}}]}
        assert 'Actual snippet' in messages[-1]['content']
        return {'content':'查到资料了。'}
    agent = Agent(store, Settings(online=True,research_enabled=True), None, SimpleNamespace(complete=complete))
    assert '查到' in await agent.run('帮我查资料', {}, chat_only=True)
    assert len(calls)==1
