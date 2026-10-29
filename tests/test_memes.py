import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from little_blue.cognition import Agent
from little_blue.core import Core
from little_blue.memes import MemeCatalog, MemeGate
from little_blue.models import Settings
from little_blue.storage import Store
from little_blue.wechat import Account, ILink, WeChatChannel
from little_blue.wechat_media import upload_url


@pytest.fixture
def catalog(tmp_path):
    data = b'RIFF' + b'\x04\x00\x00\x00' + b'WEBP' + b'synthetic-image-fixture'
    root = tmp_path / 'memes'
    (root / 'community').mkdir(parents=True)
    (root / 'community/087.webp').write_bytes(data)
    item = {'id': '087', 'title': '大笑', 'auto_context': '闲聊玩笑', 'bytes': len(data),
            'git_blob_sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
    (root / 'catalog.json').write_text(json.dumps({'items': [item]}))
    return MemeCatalog(root)


@pytest.fixture
def core(tmp_path):
    c = Core(Store(tmp_path / 'memories.db'))
    yield c
    c.store.close()


def account():
    return Account(token='SECRET_TOKEN', owner='bound-owner', bot='bot')


def test_catalog_integrity_paths_and_selector(catalog):
    assert catalog.available()[0]['id'] == '087'
    assert catalog.extract('哈哈。[[meme:087]]') == ('哈哈。', '087')
    for text in ('[[meme:../secret]]', '[[meme:https://evil.test]]',
                 '[[meme:087]][[meme:087]]', '[[meme:999]]', '示例 [[meme:087]] 不是发送'):
        clean, ident = catalog.extract(text)
        assert ident is None
        assert '[[meme:' not in clean
    with pytest.raises(ValueError):
        catalog.read('../secret')
    (catalog.root / 'community/087.webp').write_bytes(b'changed')
    assert catalog.available() == []
    with pytest.raises(ValueError):
        catalog.read('087')
    assert MemeCatalog(catalog.root / 'missing').prompt() == ''


@pytest.mark.parametrize('marker', ['[表情:大笑]', '[表情：大笑]', '【表情：大笑】', '[[表情:大笑]]', '[meme:087]'])
def test_caption_markers_resolve_only_to_verified_assets(catalog, marker):
    assert catalog.extract('哈哈。\n'+marker) == ('哈哈。', '087')
    assert catalog.extract('示例 '+marker+' 不是发图')[1] is None
    (catalog.root / 'community/087.webp').write_bytes(b'corrupt')
    assert catalog.extract('哈哈。'+marker) == ('哈哈。', None)


@pytest.mark.parametrize('marker', ['[表情:没有这张图]', '[表情:../secret]', '[表情:https://evil.test]',
                                   '[表情:大笑][[meme:087]]', '[表情:大笑', '[[meme:087'])
def test_bad_placeholders_never_leak_as_chat_text(catalog, marker):
    assert catalog.extract('正常文字。\n'+marker) == ('正常文字。', None)


def test_ambiguous_caption_cannot_select_a_random_image(catalog):
    (catalog.root / 'community/088.webp').write_bytes(catalog.read('087'))
    catalog.items['088'] = dict(catalog.items['087'], id='088')
    assert catalog.extract('哈哈。[表情:大笑]') == ('哈哈。', None)
    assert catalog.extract('哈哈。[[meme:087]]') == ('哈哈。', '087')


def test_only_explicit_meme_request_gets_fallback(catalog):
    data = catalog.read('087')
    (catalog.root/'community/907.webp').write_bytes(data)
    catalog.items['907'] = dict(catalog.items['087'],id='907',title='你好呀')
    assert catalog.requested('发一张大笑的表情') == '087'
    assert catalog.requested('刚刚发表情有问题，你再试试现在是不是正常了') == '907'
    assert catalog.requested('发一张表情') == '907'
    for text in ('为什么发表情有问题', '我回来了', '不要发一张表情', '发一张不存在的表情', '请解释怎么发一张表情', '为什么要发一张大笑的表情'):
        assert catalog.requested(text) is None


def test_gate_limits_restart_and_switch(core):
    gate = MemeGate(core.store, 'owner')
    assert gate.claim('087', automatic=True, now=10000)
    assert not gate.claim('010', automatic=True, now=10001)
    assert not gate.claim('087', automatic=True, now=10009)
    assert gate.claim('087', automatic=True, now=10010)  # Same image may fit a later turn.
    restored = MemeGate(core.store, 'owner')
    assert not restored.claim('123', automatic=True, now=10019)
    assert restored.claim('123', automatic=True, now=10020)
    generation = restored.generation()
    restored.set_enabled(False)
    assert restored.generation() > generation and not restored.enabled()
    assert not restored.claim('010', automatic=True, now=16000)
    assert restored.claim('010', now=16000)  # explicit sends remain possible
    assert not restored.claim('010', now=16001)


def test_shortlist_distinguishes_affectionate_joke_food_and_distress(catalog):
    original = catalog.read('087')
    for ident in ('936', '939', '909', '916'):
        (catalog.root / 'community' / (ident + '.webp')).write_bytes(original)
        catalog.items[ident] = dict(catalog.items['087'], id=ident)
    assert [i['id'] for i in catalog.suggestions('饿了，我能吃口你吗')] == ['936']
    assert [i['id'] for i in catalog.suggestions('写饿了')] == ['939']
    assert {i['id'] for i in catalog.suggestions('我好难受，别笑了', avoid_teasing=True)} == {'909', '916'}
    assert catalog.prompt('不用回复') == ''
    (catalog.root / 'community/939.webp').write_bytes(b'broken')
    assert catalog.suggestions('写饿了') == []


def test_generated_assets_use_local_whitelist_and_integrity(catalog):
    original = catalog.read('087')
    folder = catalog.root / 'generated'
    folder.mkdir()
    (folder / '901.webp').write_bytes(original)
    catalog.items['901'] = dict(catalog.items['087'], id='901', source='generated')
    assert catalog.extract('让我得意一下。[[meme:901]]') == ('让我得意一下。', '901')
    assert catalog.read('901') == original
    catalog.items['901']['source'] = '../community'
    with pytest.raises(ValueError):
        catalog.read('901')
    catalog.items['901']['source'] = 'generated'
    (folder / '901.webp').write_bytes(original + b'altered')
    assert all(i['id'] != '901' for i in catalog.available())


def test_no_hourly_quota_including_legacy_history_and_repeated_images(core):
    gate = MemeGate(core.store, 'owner')
    core.store.put(gate.key, [{'id': '901', 'auto': True, 'time': 10000 + i * 10} for i in range(10)])
    assert not gate.claim('902', automatic=True, now=10099)
    for i in range(120):
        assert gate.claim('901', automatic=i % 2 == 0, now=10100 + i * 10)
    assert len(core.store.get(gate.key)) == 1  # Bounded technical state, no hourly counter.
    assert not gate.claim('904', now=11300 - 1)
    assert gate.claim('904', now=11300)


def test_burst_guard_survives_real_reopen(tmp_path):
    path = tmp_path / 'gate.db'
    store = Store(path)
    assert MemeGate(store, 'owner').claim('901', automatic=True, now=10000)
    store.close()
    restored = Store(path)
    try:
        gate = MemeGate(restored, 'owner')
        assert not gate.claim('901', automatic=True, now=10009)
        assert gate.claim('901', automatic=True, now=10010)
    finally:
        restored.close()


@pytest.mark.parametrize('url', ['http://novac2c.cdn.weixin.qq.com/c2c/upload',
    'https://evil.test/c2c/upload', 'https://novac2c.cdn.weixin.qq.com.evil.test/c2c/upload',
    'https://user:pass@novac2c.cdn.weixin.qq.com/c2c/upload',
    'https://novac2c.cdn.weixin.qq.com:444/c2c/upload',
    'https://novac2c.cdn.weixin.qq.com/c2c/download'])
def test_cdn_whitelist(url):
    with pytest.raises(ValueError):
        upload_url({'upload_full_url': url}, 'filekey')


@pytest.mark.parametrize('fmt', ['webp', 'gif'])
async def test_real_wire_crypto_and_headers(catalog, fmt):
    calls = []
    plaintext = catalog.read('087') if fmt == 'webp' else b'GIF89a' + b'synthetic-gif-fixture'
    state = {}
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/getuploadurl'):
            body = json.loads(request.content)
            assert body['to_user_id'] == 'bound-owner'
            assert body['media_type'] == 1 and body['no_need_thumb']
            assert body['rawfilemd5'] == hashlib.md5(plaintext).hexdigest()
            state.update(body)
            return httpx.Response(200, json={'upload_param': 'upload private/+token'})
        if request.url.host == 'novac2c.cdn.weixin.qq.com':
            assert 'authorization' not in request.headers
            assert 'X-WECHAT-UIN' not in request.headers
            key = bytes.fromhex(state['aeskey'])
            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            padded = decryptor.update(request.content) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            assert unpadder.update(padded) + unpadder.finalize() == plaintext
            assert len(request.content) == state['filesize']
            return httpx.Response(200, headers={'x-encrypted-param': 'download-private'}, content=b'')
        body = json.loads(request.content)['msg']
        assert body['to_user_id'] == 'bound-owner' and body['context_token'] == 'ctx'
        item = body['item_list'][0]
        assert item['type'] == 2
        assert base64.b64decode(item['image_item']['media']['aes_key']).decode() == state['aeskey']
        assert item['image_item']['media']['encrypt_query_param'] == 'download-private'
        assert item['image_item']['mid_size'] == state['filesize']
        return httpx.Response(200, json={'ret': 0})
    api = ILink(account(), httpx.MockTransport(handle))
    try:
        await api.send_image('ctx', plaintext)
        assert len(calls) == 3
    finally:
        await api.close()


@pytest.mark.parametrize('failure', ['redirect', 'timeout', 'missing_header', 'revoke'])
async def test_upload_errors_never_send_or_retry_and_are_sanitized(catalog, failure):
    calls = []
    allowed = True
    def handle(request):
        nonlocal allowed
        calls.append(request)
        if request.url.path.endswith('/getuploadurl'):
            return httpx.Response(200, json={'upload_param': 'SECRET_UPLOAD'})
        assert request.url.host == 'novac2c.cdn.weixin.qq.com'
        if failure == 'timeout':
            raise httpx.ReadTimeout('SECRET_UPLOAD', request=request)
        if failure == 'revoke':
            allowed = False
            return httpx.Response(200, headers={'x-encrypted-param': 'download'})
        return httpx.Response(302 if failure == 'redirect' else 200,
                              headers={'location': 'https://evil.test'})
    api = ILink(account(), httpx.MockTransport(handle))
    try:
        with pytest.raises((RuntimeError, PermissionError, ValueError)) as error:
            await api.send_image('ctx', catalog.read('087'), lambda: allowed)
        assert 'SECRET' not in str(error.value)
        assert len(calls) == 2
    finally:
        await api.close()


async def test_agent_selects_valid_meme_without_tool_or_marker_in_memory(core, catalog):
    core.settings.online = True
    class Client:
        async def complete(self, messages, tools):
            assert tools is None
            assert any('[[meme:' in m['content'] for m in messages if m['role'] == 'system')
            return {'content': '哈哈！[[meme:087]]'}
    agent = Agent(core.store, core.settings, None, Client())
    agent.memes = catalog
    assert await agent.run('讲个玩笑', {}, chat_only=True) == '哈哈！'
    assert agent.meme_id == '087'
    assert core.store.db.execute("SELECT decision FROM audit WHERE tool='meme_choice' ORDER BY id DESC LIMIT 1").fetchone()[0] == 'selected:087'
    assert all('[[meme:' not in m['text'] for m in core.store.search(''))
    assert '[[meme:' not in core.store.continuity.recent()[-1]['text']


async def test_turn_off_then_on_during_model_reply_drops_old_selection(core, catalog):
    core.settings.online = True
    gate = MemeGate(core.store, 'owner')
    class Client:
        async def complete(self, messages, tools):
            gate.set_enabled(False)
            gate.set_enabled(True)
            return {'content': '哈哈！[[meme:087]]'}
    agent = Agent(core.store, core.settings, None, Client())
    agent.memes = catalog
    assert await agent.run('你好', {}, chat_only=True) == '哈哈！'
    assert agent.meme_id is None


class FakeMedia:
    def __init__(self):
        self.texts, self.images = [], []
        self.fail = False

    async def send(self, context, text):
        self.texts.append(text)

    async def send_image(self, context, data, permitted):
        assert permitted()
        if self.fail:
            raise RuntimeError('PRIVATE_UPLOAD_ERROR')
        self.images.append(data)


@pytest.mark.parametrize('caption,ident,reply', [('欢迎回来','915','到家就好～'), ('我在这儿','906','饭慢慢吃，我在这儿待着。')])
async def test_screenshot_regression_sends_image_instead_of_placeholder(core, catalog, caption, ident, reply):
    from little_blue.companion import setup, exchange
    image = catalog.read('087')
    (catalog.root / 'community' / (ident+'.webp')).write_bytes(image)
    catalog.items[ident] = dict(catalog.items['087'], id=ident, title=caption)
    setup(core.store)
    exchange(core.store, {'client':'e'*32,'persona':'keep current persona'})
    core.settings.online = True
    core.settings.wechat_batch_seconds = .01
    class Client:
        async def complete(self, messages, tools):
            return {'content':reply+'\n[表情:'+caption+']'}
    core.agent.client = Client()
    api = FakeMedia()
    channel = WeChatChannel(core, account(), api)
    channel.memes = core.agent.memes = catalog
    task = asyncio.create_task(channel.consume())
    try:
        channel.queue.put_nowait(('我回来了','ctx'))
        async with asyncio.timeout(3):
            while not api.images:
                await asyncio.sleep(.01)
        assert api.texts == [reply]
        assert api.images == [image]
        assert all(len(t)<=30 and '\n' not in t for t in api.texts)
        assert all('[表情' not in r['text'] for r in core.store.continuity.recent())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_explicit_test_request_cannot_claim_a_picture_without_selection(core, catalog):
    from little_blue.companion import setup, exchange
    data = catalog.read('087')
    (catalog.root/'community/907.webp').write_bytes(data)
    catalog.items['907'] = dict(catalog.items['087'],id='907',title='你好呀')
    setup(core.store);exchange(core.store, {'client':'e'*32,'persona':'unchanged'})
    core.settings.online=True;core.settings.wechat_batch_seconds=.01
    class Client:
        async def complete(self, messages, tools):
            return {'content':'你看这张能不能出来。'}
    core.agent.client=Client()
    api=FakeMedia();channel=WeChatChannel(core,account(),api)
    channel.memes=core.agent.memes=catalog
    task=asyncio.create_task(channel.consume())
    try:
        channel.queue.put_nowait(('刚刚发表情有问题，你再试试现在是不是正常了','ctx'))
        async with asyncio.timeout(3):
            while not api.images:await asyncio.sleep(.01)
        assert api.texts==['你看这张能不能出来。'] and api.images==[data]
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


async def test_channel_command_and_failure_fallback(core, catalog):
    api = FakeMedia()
    channel = WeChatChannel(core, account(), api)
    channel.memes = catalog
    assert await channel.meme_control('/memes off', 'ctx')
    assert not channel.meme_gate.enabled()
    assert not await channel.send_meme('ctx', '087', True, channel.meme_gate.generation())
    api.fail = True
    assert not await channel.send_meme('ctx', '087')
    assert len(api.images) == 0
    assert '未确认送达' in api.texts[-1]
    assert 'PRIVATE' not in ''.join(api.texts)
    assert not core.store.search('')


async def test_chat_text_delivered_even_when_image_fails(core, catalog):
    api = FakeMedia()
    api.fail = True
    channel = WeChatChannel(core, account(), api)
    channel.memes = core.agent.memes = catalog
    core.settings.online = True
    class Client:
        async def complete(self, messages, tools):
            return {'content': '哈哈。[[meme:087]]'}
    core.agent.client = Client()
    task = asyncio.create_task(channel.consume())
    try:
        channel.queue.put_nowait(('讲个玩笑', 'ctx'))
        async with asyncio.timeout(2):
            while not api.texts or not channel.image_job or not channel.image_job.done():
                await asyncio.sleep(.01)
        assert api.texts == ['哈哈。']
        assert core.store.tasks()[0]['status'] == 'succeeded'
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancel_image_keeps_channel_consumer_alive(core, catalog):
    entered = asyncio.Event()
    class Waiting(FakeMedia):
        async def send_image(self, context, data, permitted):
            entered.set()
            await asyncio.Event().wait()
    channel = WeChatChannel(core, account(), Waiting())
    channel.memes = catalog
    task = asyncio.create_task(channel.consume())
    try:
        channel.queue.put_nowait(('/meme 087', 'ctx'))
        await entered.wait()
        channel.cancel_generation += 1
        channel.image_job.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        channel.queue.put_nowait(('/status', 'ctx'))
        async with asyncio.timeout(2):
            while not channel.api.texts:
                await asyncio.sleep(.01)
        assert 'DeepSeek' in channel.api.texts[0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
