"""Bounded iLink image upload, based on Tencent/openclaw-weixin's MIT protocol code."""
import asyncio
import base64
import hashlib
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from .memes import IMAGE_LIMIT, image_format
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CDN = 'https://novac2c.cdn.weixin.qq.com/c2c'


def upload_url(result, filekey):
    url = result.get('upload_full_url')
    if not url:
        param = result.get('upload_param')
        if not isinstance(param, str) or not param or len(param) > 16384:
            raise ValueError('图片上传参数无效')
        url = CDN + '/upload?' + urlencode({'encrypted_query_param': param, 'filekey': filekey})
    if not isinstance(url, str) or len(url) > 20000:
        raise ValueError('图片上传地址无效')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'novac2c.cdn.weixin.qq.com' or
            parsed.port not in (None, 443) or parsed.username or parsed.password or
            parsed.fragment or parsed.path != '/c2c/upload'):
        raise ValueError('图片上传地址不在微信 CDN 白名单中')
    return url


async def send_image(api, context, data, permitted=lambda: True):
    if (not context or not api.account or not api.account.enabled or not permitted()):
        raise PermissionError('图片发送已关闭或会话不可用')
    if (not isinstance(data, bytes) or not 12 <= len(data) <= IMAGE_LIMIT or not image_format(data)):
        raise ValueError('只发送已校验的本地 WebP / GIF 表情')
    key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    try:
        async with asyncio.timeout(80):
            result = await api.request('getuploadurl', {
                'filekey': filekey, 'media_type': 1, 'to_user_id': api.account.owner,
                'rawsize': len(data), 'rawfilemd5': hashlib.md5(data).hexdigest(),
                'filesize': len(encrypted), 'no_need_thumb': True, 'aeskey': key.hex()})
            if not permitted():
                raise PermissionError('图片发送已关闭')
            # No API authorization headers go to the CDN; no redirects or retries.
            async with api.http.stream('POST', upload_url(result, filekey), content=encrypted,
                                       headers={'Content-Type': 'application/octet-stream'}) as response:
                if response.status_code != 200:
                    raise RuntimeError('图片 CDN 上传失败')
                download = response.headers.get('x-encrypted-param', '')
                if not download or len(download) > 16384:
                    raise ValueError('图片 CDN 响应无效')
            if not permitted():
                raise PermissionError('图片发送已关闭')
            return await api.send_item(context, {'type': 2, 'image_item': {
                'media': {'encrypt_query_param': download,
                          # Official wire format is base64 of the ASCII hex key.
                          'aes_key': base64.b64encode(key.hex().encode()).decode(), 'encrypt_type': 1},
                'mid_size': len(encrypted)}})
    except (httpx.HTTPError, TimeoutError):
        raise RuntimeError('图片传输失败或超时；未自动重试') from None
