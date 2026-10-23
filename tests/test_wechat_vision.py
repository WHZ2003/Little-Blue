import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from little_blue.cognition import DeepSeekClient
from little_blue.core import Core
from little_blue.storage import Store
from little_blue.wechat import Account, ILink, WeChatChannel
from little_blue.wechat_vision import download_image, reference, normalize_image


def png():
    out = io.BytesIO()
    Image.new('RGB', (32, 48), 'blue').save(out, 'PNG')
    return out.getvalue()


def image_item(key=None):
    return {'type': 2, 'image_item': {'media': {'encrypt_query_param': 'private-cdn-reference'},
                                    **({'aeskey': key.hex()} if key else {})}}


@pytest.mark.parametrize('encoding', ['hex', 'base64', 'base64hex', 'plain'])
async def test_official_key_formats_and_private_download(encoding):
    key = b'0123456789abcdef'
    raw = png()
    pad = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    padded = pad.update(raw) + pad.finalize()
    data = enc.update(padded) + enc.finalize()
    item = image_item(key)
    if encoding != 'hex':
        item['image_item'].pop('aeskey')
        if encoding != 'plain':
            value = key if encoding == 'base64' else key.hex().encode()
            item['image_item']['media']['aes_key'] = base64.b64encode(value).decode()
        else:
            data = raw
    def handler(request):
        assert request.url.host == 'novac2c.cdn.weixin.qq.com'
        assert request.url.params['encrypted_query_param'] == 'private-cdn-reference'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=data)
    api = ILink(transport=httpx.MockTransport(handler))
    try:
        result = await download_image(api, item)
        with Image.open(io.BytesIO(result)) as picture:
            assert picture.format == 'JPEG' and picture.size == (32, 48)
    finally:
        await api.close()


@pytest.mark.parametrize('url', ['http://novac2c.cdn.weixin.qq.com/c2c/download',
    'https://127.0.0.1/c2c/download', 'https://evil.example/c2c/download',
    'https://novac2c.cdn.weixin.qq.com.evil.example/c2c/download',
    'https://user@novac2c.cdn.weixin.qq.com/c2c/download'])
def test_reject_non_cdn_references(url):
    item = image_item()
    item['image_item']['media']['full_url'] = url
    with pytest.raises(ValueError):
        reference(item)


@pytest.mark.parametrize('status,data', [(302, b''), (200, b'x' * (8 * 1024 * 1024 + 1)),
                                        (200, b'not an image')], ids=['redirect', 'oversize', 'invalid'])
async def test_bad_download_is_not_model_input(status, data):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=data, headers={'location': 'http://127.0.0.1'})
    api = ILink(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises((ValueError, OSError)):
            await download_image(api, image_item())
        assert len(calls) == 1
    finally:
        await api.close()


async def test_image_tokens_not_base64_length_and_usage_settled(tmp_path):
    store = Store(tmp_path / 'budget.db')
    core = Core(store)
    core.settings.online = True
    core.settings.daily_tokens = 10000
    image_url = 'data:image/jpeg;base64,' + 'a' * 200000
    def handler(request):
        assert json.loads(request.content)['messages'][0]['content'][0]['image_url']['url'] == image_url
        assert 4096 < store.budget_usage()['reserved'] < 6000
        return httpx.Response(200, json={'choices': [{'message': {'content': '蓝色'}}],
                                      'usage': {'prompt_tokens': 550, 'completion_tokens': 50, 'total_tokens': 600}})
    client = DeepSeekClient(store, core.settings, httpx.MockTransport(handler), 'fake')
    try:
        await client.complete([{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_url}}]}])
        assert store.budget_usage()['reserved'] == 600
    finally:
        store.close()


class API:
    def __init__(self, fail=False):
        self.sent = []
        self.updates = asyncio.Queue()
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(500 if fail else 200, content=png())))

    async def request(self, *args):
        return await self.updates.get()

    async def send(self, context, text):
        self.sent.append((context, text))

    async def close(self):
        await self.http.aclose()


def inbound(ident=1):
    return {'message_id': ident, 'from_user_id': 'owner', 'message_type': 1,
            'message_state': 2, 'context_token': 'context', 'item_list': [image_item()]}


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('failed', [False, True])
async def test_image_only_poll_to_chat_and_persisted_observation(tmp_path, failed):
    core = Core(Store(tmp_path / 'chat.db'))
    core.settings.online = True
    core.settings.wechat_batch_seconds = .01
    core.store.growth.control('off')
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            calls.append(messages)
            if isinstance(messages[-1]['content'], list):
                assert messages[-1]['content'][1]['type'] == 'image_url'
                return {'content': '夜晚街道上有红色交通灯。'}
            assert '夜晚街道' in messages[-1]['content']
            return {'content': '路口灯都亮了，刚到家吗？'}
    core.agent.client = Client()
    api = API(failed)
    channel = WeChatChannel(core, Account(token='fake', owner='owner', bot='bot'), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(), inbound()]})
        await wait_for(lambda: api.sent and not channel.processing)
        assert len(api.sent) == 1
        assert len(calls) == (0 if failed else 2)
        rows = core.store.continuity.recent()
        assert len(rows) == (1 if failed else 2)
        if failed:
            assert '没读成功' in api.sent[0][1]
        else:
            assert '图片自动识别结果' in rows[0]['text']
            assert rows[-1]['delivery'] == 'delivered'
        assert not channel.incoming_images
        assert 'private-cdn-reference' not in json.dumps(rows)
        assert 'data:image' not in json.dumps(rows)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await core.memory.close()
        core.store.close()


async def test_cancel_during_vision_never_sends_or_updates_photo(tmp_path):
    core = Core(Store(tmp_path / 'cancel.db'))
    core.settings.online = True
    core.settings.wechat_batch_seconds = .01
    entered = asyncio.Event()
    class Client:
        async def complete(self, messages, schemas):
            entered.set()
            await asyncio.Event().wait()
    core.agent.client = Client()
    api = API()
    channel = WeChatChannel(core, Account(token='fake', owner='owner', bot='bot'), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound()]})
        await asyncio.wait_for(entered.wait(), 3)
        cancel = inbound(2)
        cancel['item_list'] = [{'type': 1, 'text_item': {'text': '/cancel'}}]
        api.updates.put_nowait({'msgs': [cancel]})
        await wait_for(lambda: api.sent and not channel.processing)
        assert len(api.sent) == 1 and '取消' in api.sent[0][1]
        assert '图片自动识别结果' not in core.store.continuity.recent()[0]['text']
        assert not channel.incoming_images
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await core.memory.close()
        core.store.close()
