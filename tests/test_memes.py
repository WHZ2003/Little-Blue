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


def test_gate_limits_restart_and_switch(core):
    gate = MemeGate(core.store, 'owner')
    assert gate.claim('087', automatic=True, now=10000)
    assert not gate.claim('087', automatic=True, now=11000)  # no repeated automatic picture
    assert not gate.claim('010', automatic=True, now=10001)
    assert not gate.claim('010', automatic=True, now=10059)
    assert gate.claim('010', automatic=True, now=10060)
    for i in range(2, 8):
        assert gate.claim('087' if i % 2 == 0 else '010', automatic=True, now=10000 + i * 60)
    assert not gate.claim('123', automatic=True, now=10480)
    restored = MemeGate(core.store, 'owner')
    assert not restored.claim('123', automatic=True, now=10480)
    generation = restored.generation()
    restored.set_enabled(False)
    assert restored.generation() > generation and not restored.enabled()
    assert not restored.claim('010', automatic=True, now=16000)
    assert restored.claim('010', now=16000)  # explicit sends remain possible
    assert not restored.claim('010', now=16001)


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


def test_automatic_hour_window_expires_and_manual_limits_still_apply(core):
    gate = MemeGate(core.store, 'owner')
    for i in range(8):
        assert gate.claim('901' if i % 2 == 0 else '902', automatic=True, now=10000 + i * 60)
    assert not gate.claim('903', automatic=True, now=10500)
    assert gate.claim('903', automatic=True, now=13600)
    # All pictures retain the separate 10/hour and 10s guard.
    assert not gate.claim('904', now=13609)
    assert gate.claim('904', now=13610)


@pytest.mark.parametrize('url', ['http://novac2c.cdn.weixin.qq.com/c2c/upload',
    'https://evil.test/c2c/upload', 'https://novac2c.cdn.weixin.qq.com.evil.test/c2c/upload',
    'https://user:pass@novac2c.cdn.weixin.qq.com/c2c/upload',
    'https://novac2c.cdn.weixin.qq.com:444/c2c/upload',
    'https://novac2c.cdn.weixin.qq.com/c2c/download'])
def test_cdn_whitelist(url):
    with pytest.raises(ValueError):
        upload_url({'upload_full_url': url}, 'filekey')


async def test_real_wire_crypto_and_headers(catalog):
    calls = []
    plaintext = catalog.read('087')
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
