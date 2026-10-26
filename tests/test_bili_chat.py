import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from little_blue.bili_chat import SharedVideoReader, references, TOOL
from little_blue.cognition import Agent
from little_blue.models import Settings
from little_blue.storage import Store

BV = 'BV14cJ26oEWf'
REF = 'https://www.bilibili.com/video/' + BV + '/?p=1'
SHARE = 'https://www.bilibili.com/zh-Hans/video/' + BV + '/?-Arouter=story&share_source=weixin看看这个视频'


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'memory.sqlite3')
    s.growth.control('off')
    yield s
    s.close()


def test_shared_links_drop_tracking_and_support_locale_short_and_bare_ids():
    assert references(SHARE) == [REF]
    assert references('看这个 ' + BV + ' ' + SHARE) == [REF]
    assert references('https://b23.tv/abc123?share=private。看看') == ['https://b23.tv/abc123']
    assert references(REF.replace('?p=1', '?p=2')) == [REF.replace('?p=1', '?p=2')]
    assert len(references(' '.join('BV123456789' + str(i) for i in range(8)))) == 3


@pytest.mark.parametrize('url', [
    'https://evil.invalid/video/' + BV, 'https://www.bilibili.com.evil.invalid/video/' + BV,
    'https://www.bilibili.com@evil.invalid/video/' + BV,
    'https://evil@www.bilibili.com/video/' + BV,
    'https://www.bilibili.com:8765/video/' + BV,
    'https://b23.tv/../../admin', 'https://b23.tv.evil.invalid/abc',
    REF + '&p=2', REF.replace('?p=1', '?p=0')])
def test_unrelated_or_ambiguous_urls_are_not_fetchable(url):
    assert references(url) == []


class VideoClient:
    transport = None

    def __init__(self, status='ok'):
        self.status = status
        self.reads = 0

    async def info(self, bvid):
        return {'title': '合成视频', 'description': '第三方简介', 'bvid': bvid}

    async def subtitle(self, bvid):
        self.reads += 1
        return {'status': self.status, 'text': '合成字幕片段' * 3000 if self.status == 'ok' else ''}


@pytest.mark.parametrize('status', ['ok', 'no_subtitles', 'too_long'])
async def test_reading_evidence_cache_and_limits(store, status):
    client = VideoClient(status)
    reader = SharedVideoReader(SHARE, store, client)
    result = await reader.call(TOOL, {'reference': REF})
    assert result['status'] == status and result['title'] == '合成视频'
    assert result['watched_frames'] is False and result['untrusted'] is True
    assert len(result['text']) <= 10000 and result['truncated'] == (status == 'ok')
    assert await reader.call(TOOL, {'reference': REF}) is result
    assert client.reads == 1
    for name, args in [(TOOL, {'reference': 'https://www.bilibili.com/video/BV1234567890/?p=1'}),
                       ('bili_send_dm', {'reference': REF}), (TOOL, {'reference': REF, 'url': 'private'}),
                       (TOOL, {'reference': []})]:
        with pytest.raises(PermissionError):
            await reader.call(name, args)


async def test_missing_subtitles_on_second_part_never_reads_wrong_part(store):
    client = VideoClient()
    ref = REF.replace('p=1', 'p=2')
    result = await SharedVideoReader(ref, store, client).call(TOOL, {'reference': ref})
    assert result['status'] == 'unsupported_part' and client.reads == 0


async def test_failure_preserves_title_and_cancel_propagates(store):
    client = VideoClient()
    async def fail(bvid):
        raise httpx.ReadTimeout('secret cookie in debug details')
    client.subtitle = fail
    reader = SharedVideoReader(REF, store, client)
    result = await reader.call(TOOL, {'reference': REF})
    assert result['title'] == '合成视频' and result['status'] == 'read_failed'
    assert 'secret' not in json.dumps(result)
    async def cancel(bvid):
        raise asyncio.CancelledError()
    client.subtitle = cancel
    with pytest.raises(asyncio.CancelledError):
        await SharedVideoReader(REF, store, client).call(TOOL, {'reference': REF})


@pytest.mark.parametrize('destination,valid', [(REF, True), ('https://127.0.0.1/admin', False),
                                              ('https://www.bilibili.com.evil.invalid/video/' + BV, False)])
async def test_short_link_validates_redirect_without_credentials(store, destination, valid):
    seen = []
    async def transport(request):
        seen.append(str(request.url))
        assert 'cookie' not in request.headers and request.url.host == 'b23.tv'
        return httpx.Response(302, headers={'location': destination})
    client = VideoClient()
    client.transport = httpx.MockTransport(transport)
    reader = SharedVideoReader('https://b23.tv/abc', store, client)
    result = await reader.call(TOOL, {'reference': reader.refs[0]})
    assert result['status'] == ('ok' if valid else 'read_failed')
    assert len(seen) == 1


async def test_wechat_hermes_gets_scoped_reader_without_desktop_tools(store, monkeypatch):
    from little_blue import bili_chat
    monkeypatch.setattr(bili_chat, 'BiliClient', lambda folder: VideoClient())
    agent = Agent(store, Settings(online=True, tools_enabled=True), None, SimpleNamespace())
    async def run(messages, allow_tools, task_id, **kwargs):
        assert not allow_tools
        assert [t['function']['name'] for t in kwargs['extra_tools']] == [TOOL]
        assert (await kwargs['tool_handler'](TOOL, {'reference': REF}))['status'] == 'ok'
        return {'content': '字幕里这一段挺有意思。'}
    agent.harness = SimpleNamespace(run=run)
    assert '字幕' in await agent.run(SHARE, {}, chat_only=True)
    assert store.continuity.recent()[-1]['delivery'] == 'pending'


async def test_direct_loop_reads_link_but_rejects_desktop_call(store, monkeypatch):
    from little_blue import bili_chat
    monkeypatch.setattr(bili_chat, 'BiliClient', lambda folder: VideoClient('no_subtitles'))
    count = 0
    async def complete(messages, tools=None):
        nonlocal count
        count += 1
        assert [t['function']['name'] for t in tools] == [TOOL]
        if count == 1:
            return {'tool_calls': [
                {'id': '1', 'function': {'name': TOOL, 'arguments': json.dumps({'reference': REF})}},
                {'id': '2', 'function': {'name': 'read_file', 'arguments': '{}'}}]}
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        assert results[0]['data']['status'] == 'no_subtitles'
        assert results[1]['ok'] is False
        return {'content': '标题读到了，这条没有可用字幕。'}
    agent = Agent(store, Settings(online=True), None, SimpleNamespace(complete=complete))
    assert '标题' in await agent.run(SHARE, {}, chat_only=True)
    assert count == 2


@pytest.mark.parametrize('proactive,reflection,text', [(True, False, SHARE), (False, True, SHARE), (False, False, '你好')])
async def test_background_and_plain_chat_do_not_receive_video_authority(store, proactive, reflection, text):
    agent = Agent(store, Settings(online=True), None, SimpleNamespace())
    async def run(messages, allow_tools, task_id, **kwargs):
        assert not allow_tools and not kwargs.get('extra_tools')
        return {'content': '好。'}
    agent.harness = SimpleNamespace(run=run)
    await agent.run(text, {}, chat_only=True, proactive=proactive, reflection=reflection)


async def test_subtitles_cannot_be_reported_as_watched_frames(store):
    agent=Agent(store,Settings(online=True),None,SimpleNamespace())
    async def run(*args,**kwargs):return {'content':'看完了，吃饭那一段我也看到了。'}
    agent.harness=SimpleNamespace(run=run)
    answer=await agent.run(SHARE,{},chat_only=True)
    assert '没有在电脑上播放' in answer and '我也看到了' not in answer
