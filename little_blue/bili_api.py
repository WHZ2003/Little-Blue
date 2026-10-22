"""Bilibili reading and explicitly configured owner mentions.

Posting stays in the host scheduler, never in the model's tool list.
See bili-LICENSE.txt for upstream attribution.
"""
import asyncio
import html
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
from pydantic import Field
from .models import Model

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
HEADERS = {'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'}


class CredentialData(Model):
    sessdata: str = Field(min_length=1, max_length=4096)
    bili_jct: str = Field('', max_length=256)
    buvid3: str = Field('', max_length=256)
    dedeuserid: str = Field('', max_length=64, pattern=r'^[0-9]*$')


def save_credential(path, raw):
    data = CredentialData.model_validate(raw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(data.model_dump_json()); f.flush(); os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)


def read_credential(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError('Invalid Bilibili credential file')
    return CredentialData.model_validate_json(path.read_bytes())


def clean(value, limit=300):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value)))[:limit]


class BiliClient:
    def __init__(self, folder, transport=None):
        self.path = Path(folder) / 'bili-credential.json'
        self.transport = transport
        self.login_key = None
        self.login_deadline = 0
        self.login_state = 'idle'
        self.initialized = False

    def bound(self):
        try:
            return read_credential(self.path) is not None
        except (ValueError, OSError):
            return False

    def credential(self):
        from bilibili_api import Credential, select_client, request_settings
        if not self.initialized:
            select_client('httpx')
            request_settings.set_timeout(12)
            request_settings.set('trust_env', False)
            request_settings.set_wbi_retry_times(1)
            self.initialized = True
        data = read_credential(self.path)
        return Credential(**data.model_dump()) if data else Credential()

    async def mention_profile(self, uid):
        from bilibili_api import user
        profile = await user.User(uid, credential=self.credential()).get_user_info()
        if int(profile['mid']) != uid:
            raise ValueError('Mention identity mismatch')
        return {'uid':uid, 'name':profile['name']}

    async def dm_query(self, sender, receiver):
        # Use the signing implementation from our pinned upstream dependency.
        from bilibili_api.utils.network import _enc_wbi, get_wbi_mixin_key
        return _enc_wbi({'w_sender_uid':sender, 'w_receiver_id':receiver},
                        await get_wbi_mixin_key(self.credential()))

    async def send_dm(self, text, uid, permitted=None):
        if not 0 < uid < 2**63 or not isinstance(text,str) or not 1 <= len(text) <= 500:
            raise ValueError('Invalid fixed-recipient private message')
        cred = read_credential(self.path)
        if not cred or not cred.bili_jct or not cred.dedeuserid or str(uid) == cred.dedeuserid:
            raise ValueError('Private message credential or recipient unavailable')
        sender = int(cred.dedeuserid)
        params = await self.dm_query(sender, uid)
        cookies = {'SESSDATA':cred.sessdata, 'bili_jct':cred.bili_jct, 'DedeUserID':cred.dedeuserid}
        if cred.buvid3: cookies['buvid3'] = cred.buvid3
        headers = {**HEADERS, 'Referer':'https://message.bilibili.com/', 'Origin':'https://message.bilibili.com'}
        async with httpx.AsyncClient(headers=headers, cookies=cookies, trust_env=False,
                                     timeout=20, transport=self.transport, follow_redirects=False) as client:
            if permitted is not None and not permitted():
                raise asyncio.CancelledError()
            # A single POST. An uncertain receipt must never trigger a resend.
            response = await client.post('https://api.vc.bilibili.com/web_im/v1/web_im/send_msg', params=params, data={
                'msg[sender_uid]':str(sender), 'msg[receiver_id]':str(uid), 'msg[receiver_type]':'1',
                'msg[msg_type]':'1', 'msg[msg_status]':'0', 'msg[content]':json.dumps({'content':text},ensure_ascii=False),
                'msg[dev_id]':str(uuid.uuid4()).upper(), 'msg[new_face_version]':'0',
                'msg[timestamp]':str(int(time.time())), 'from_filework':'0', 'build':'0', 'mobi_app':'web',
                'csrf':cred.bili_jct, 'csrf_token':cred.bili_jct})
            response.raise_for_status()
            body = response.json()
        if body.get('code') != 0:
            return {'accepted':False, 'code':body.get('code')}
        key = str((body.get('data') or {}).get('msg_key',''))
        if not key.isdigit() or int(key) <= 0:
            raise ValueError('Missing private message receipt')
        return {'accepted':True, 'msg_key':key}

    async def dm_history(self, uid):
        from bilibili_api import session
        return await session.fetch_session_msgs(uid, self.credential())

    async def verify_dm(self, uid, msg_key, text):
        cred = read_credential(self.path)
        if not cred or not cred.dedeuserid:
            raise ValueError('Private message credential unavailable')
        history = await self.dm_history(uid)
        for message in history.get('messages') or []:
            if (str(message.get('msg_key')) != str(msg_key)
                    or str(message.get('sender_uid')) != cred.dedeuserid
                    or str(message.get('receiver_id')) != str(uid) or message.get('msg_type') != 1):
                continue
            content = message.get('content')
            if isinstance(content,str):
                content = json.loads(content)
            return isinstance(content,dict) and content.get('content') == text
        return False

    async def post_mention(self, bvid, text, uid, name, permitted=None):
        if (not re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid) or not 0 < uid < 2**63
                or not re.fullmatch(r'[^@\s]{1,40}', name)
                or not text.startswith('@' + name + ' ') or text.count('@') != 1
                or not 1 <= len(text) <= 400):
            raise ValueError('Invalid fixed-recipient comment')
        cred = read_credential(self.path)
        if not cred or not cred.bili_jct or str(uid) == cred.dedeuserid:
            raise ValueError('Comment credential or recipient unavailable')
        meta = await self.info(bvid)
        # Exactly one POST, without library retries or redirect-following.
        cookies = {'SESSDATA':cred.sessdata, 'bili_jct':cred.bili_jct}
        if cred.buvid3: cookies['buvid3'] = cred.buvid3
        async with httpx.AsyncClient(headers=HEADERS, cookies=cookies, trust_env=False,
                                     timeout=20, transport=self.transport, follow_redirects=False) as client:
            if permitted is not None and not permitted():
                raise asyncio.CancelledError()
            r = await client.post('https://api.bilibili.com/x/v2/reply/add', data={
                'oid':str(meta['aid']), 'type':'1', 'plat':'1', 'message':text,
                'csrf':cred.bili_jct, 'at_name_to_mid':json.dumps({name:uid},ensure_ascii=False)})
            r.raise_for_status()
            payload = r.json()
        if payload.get('code') != 0:
            return {'accepted':False, 'code':payload.get('code')}
        rpid = str(payload.get('data',{}).get('rpid',''))
        if not rpid.isdigit() or int(rpid) <= 0:
            raise ValueError('Missing comment receipt')
        return {'accepted':True, 'rpid':rpid, 'aid':meta['aid']}

    async def public_comment_page(self, aid):
        from bilibili_api import comment
        self.credential()  # Configure the HTTP backend, but send no account credential.
        return await comment.get_comments_lazy(aid, comment.CommentResourceType.VIDEO,
                                               order=comment.OrderType.TIME, credential=None)

    async def comment_root(self, aid, rpid, authenticated=False):
        params = {'type':1, 'oid':aid, 'root':rpid, 'pn':1, 'ps':20}
        if authenticated:
            from bilibili_api import Api
            return await Api(url='https://api.bilibili.com/x/v2/reply/reply', method='GET',
                             credential=self.credential()).update_params(**params).result
        body, _ = await self.json_get('https://api.bilibili.com/x/v2/reply/reply', params=params)
        if body.get('code') == 12006:
            return {}  # Not accessible in this view; does not prove deletion.
        if body.get('code') != 0:
            raise ValueError('Public comment lookup failed')
        return body.get('data') or {}

    async def verify_mention(self, aid, rpid, uid):
        result = {'status':'visibility_unconfirmed', 'public_visible':None,
                  'author_visible':None, 'comment_state':None, 'mention_recognized':None,
                  'notification_delivered':None, 'errors':[]}
        def match(rows):
            return next((r for r in rows if str(r.get('rpid')) == str(rpid)), None)
        public = None
        try:
            page = await self.public_comment_page(aid)
            public = match(page.get('replies') or [])
        except Exception as error:
            result['errors'].append('public_page:' + type(error).__name__)
        if public is None:
            try:
                page = await self.comment_root(aid, rpid)
                public = match([page.get('root') or {}])
                result['public_visible'] = False
            except Exception as error:
                result['errors'].append('public_root:' + type(error).__name__)
        if public is not None and not public.get('invisible') and public.get('state') == 0:
            result['public_visible'] = True
            result['comment_state'] = 0
            result['mention_recognized'] = any(str(m.get('mid')) == str(uid)
                for m in public.get('content',{}).get('members') or [])
            result['status'] = 'visible' if result['mention_recognized'] else 'visible_without_mention'
            return result
        try:
            page = await self.comment_root(aid, rpid, authenticated=True)
            own = match([page.get('root') or {}])
            result['author_visible'] = own is not None
            if own is not None:
                result['comment_state'] = own.get('state')
                result['mention_recognized'] = any(str(m.get('mid')) == str(uid)
                    for m in own.get('content',{}).get('members') or [])
                if result['public_visible'] is False:
                    result['status'] = {17:'owner_only', 11:'pending_review'}.get(own.get('state'), 'not_public')
        except Exception as error:
            result['errors'].append('author_root:' + type(error).__name__)
        return result

    async def json_get(self, url, *, params=None):
        async with httpx.AsyncClient(headers=HEADERS, trust_env=False, timeout=15,
                                     transport=self.transport, follow_redirects=False) as client:
            async with client.stream('GET', url, params=params) as r:
                r.raise_for_status()
                chunks = bytearray()
                async for chunk in r.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2 * 1024 * 1024:
                        raise ValueError('Bilibili response exceeds limit')
                return json.loads(chunks), r.cookies

    async def login_start(self):
        body, _ = await self.json_get('https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
                                      params={'source': 'main-fe-header'})
        data = body['data']
        parsed = urlsplit(data['url'])
        if parsed.scheme != 'https' or parsed.hostname not in ('passport.bilibili.com', 'passport.biligame.com', 'account.bilibili.com'):
            raise ValueError('Unexpected QR destination')
        self.login_key = data['qrcode_key']
        self.login_deadline = time.monotonic() + 180
        self.login_state = 'waiting'
        return {'state': self.login_state, 'url': data['url'], 'expires_in': 180}

    async def login_poll(self):
        if not self.login_key:
            return self.login_state
        if time.monotonic() >= self.login_deadline:
            self.login_key = None
            self.login_state = 'expired'
            return self.login_state
        body, cookies = await self.json_get('https://passport.bilibili.com/x/passport-login/web/qrcode/poll',
            params={'qrcode_key': self.login_key, 'source': 'main-fe-header'})
        code = body.get('data', {}).get('code')
        self.login_state = {86101: 'waiting', 86090: 'scanned', 86038: 'expired', 0: 'ok'}.get(code, 'unknown')
        if code == 0:
            # Credentials come from Set-Cookie, never the redirect URL.
            names = {'SESSDATA': 'sessdata', 'bili_jct': 'bili_jct', 'buvid3': 'buvid3', 'DedeUserID': 'dedeuserid'}
            raw = {names[c.name]: c.value for c in cookies.jar if c.name in names}
            if not raw.get('sessdata'):
                self.login_state = 'failed'
            else:
                try:
                    if not raw.get('buvid3'):
                        spi, _ = await self.json_get('https://api.bilibili.com/x/frontend/finger/spi')
                        raw['buvid3'] = spi.get('data', {}).get('b_3', '')
                except (httpx.HTTPError, ValueError):
                    pass
                save_credential(self.path, raw)
            self.login_key = None
        elif code == 86038:
            self.login_key = None
        return self.login_state

    async def check(self):
        if not self.bound():
            return False
        async with asyncio.timeout(20):
            return await self.credential().check_valid()

    async def search(self, keyword):
        from bilibili_api import Api
        # Same WBI search endpoint used by the plugin, with the user's credential.
        result = await Api(url='https://api.bilibili.com/x/web-interface/wbi/search/type',
            method='GET', wbi=True, credential=self.credential()).update_params(
                keyword=keyword, search_type='video', page=1, order='totalrank').result
        return [{'bvid': r['bvid'], 'title': clean(r.get('title', '')), 'author': clean(r.get('author', ''), 80),
                 'description': clean(r.get('description', ''), 300), 'published': r.get('pubdate', 0),
                 'url': 'https://www.bilibili.com/video/' + r['bvid'] + '/'}
                for r in result.get('result', [])[:12] if re.fullmatch(r'BV[0-9A-Za-z]{10}', r.get('bvid', ''))]

    async def info(self, bvid):
        from bilibili_api import video
        info = await video.Video(bvid=bvid, credential=self.credential()).get_info()
        return {'bvid': bvid, 'title': clean(info.get('title', '')), 'author': clean(info.get('owner', {}).get('name', ''), 80),
                'description': clean(info.get('desc', ''), 1200), 'duration': info.get('duration', 0),
                'cid': info.get('cid') or (info.get('pages') or [{}])[0].get('cid'),
                'parts': len(info.get('pages', [])), 'aid': info['aid'],
                'url': 'https://www.bilibili.com/video/' + bvid + '/'}

    async def subtitle(self, bvid):
        from bilibili_api import video
        info = await self.info(bvid)
        if not info['cid']:
            return {'status': 'no_subtitles', **info}
        subs = await video.Video(bvid=bvid, credential=self.credential()).get_subtitle(cid=info['cid'])
        tracks = subs.get('subtitles') or []
        if not tracks:
            return {'status': 'no_subtitles', **info}
        target = next((s for s in tracks if s.get('lan') in ('ai-zh', 'zh-CN', 'zh')), tracks[0])
        url = target.get('subtitle_url', '')
        if url.startswith('//'):
            url = 'https:' + url
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password
                or not parsed.hostname or not (parsed.hostname.endswith('.hdslb.com') or parsed.hostname.endswith('.bilibili.com'))):
            raise ValueError('Unexpected subtitle host')
        # Separate HTTP client: never send account cookies to a subtitle CDN.
        body, _ = await self.json_get(url)
        text = '\n'.join(str(r.get('content', '')) for r in body.get('body', []))
        status = 'ok' if 30 <= len(text.strip()) <= 24000 else ('too_long' if len(text) > 24000 else 'no_subtitles')
        return {**info, 'status': status, 'language': target.get('lan', ''), 'part': 1,
                'text': text if status == 'ok' else '', 'characters': len(text)}

    async def comments(self, bvid):
        from bilibili_api import comment
        info = await self.info(bvid)
        result = await comment.get_comments(oid=info['aid'], type_=comment.CommentResourceType.VIDEO,
            page_index=1, order=comment.OrderType.LIKE, credential=self.credential())
        return {'bvid': bvid, 'comments': [{'text': clean(r.get('content', {}).get('message', ''), 500),
                                           'likes': r.get('like', 0)} for r in (result.get('replies') or [])[:5]]}
