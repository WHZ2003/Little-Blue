"""Opt-in iLink text channel. Protocol reference: Tencent/openclaw-weixin.

Independent Python implementation; no OpenClaw runtime or third-party relay.
Credentials never appear in model context, public state, URLs or error text.
"""
import argparse
import asyncio
import base64
from collections import deque
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
import uuid

import httpx
from pydantic import BaseModel, Field, field_validator
from .memes import MemeCatalog, MemeGate

BASE = 'https://ilinkai.weixin.qq.com'
LOG = logging.getLogger(__name__)


def api_base(value):
    p = urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or
            not p.hostname.endswith('.weixin.qq.com') or p.username or p.password or
            p.port not in (None, 443) or p.path not in ('', '/') or p.query or p.fragment):
        raise ValueError('微信接口地址无效')
    return value.rstrip('/')


class Account(BaseModel):
    token: str = Field(min_length=1, max_length=8192, repr=False)
    owner: str = Field(min_length=1, max_length=256, repr=False)
    bot: str = Field(min_length=1, max_length=256)
    base: str = BASE
    enabled: bool = True
    revision: int = 0

    @field_validator('base')
    @classmethod
    def check_base(cls, value):
        return api_base(value)


class SessionExpired(RuntimeError):
    pass


class ILink:
    def __init__(self, account=None, transport=None):
        self.account = account
        self.http = httpx.AsyncClient(transport=transport, trust_env=False,
                                     follow_redirects=False, timeout=35)

    async def request(self, endpoint, body=None, *, base=None, params=None, authenticated=True):
        url = api_base(base or (self.account.base if self.account else BASE))
        headers = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str(0x02040A)}
        if body is not None:
            headers.update({'AuthorizationType': 'ilink_bot_token',
                            'X-WECHAT-UIN': base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
            if authenticated:
                if self.account is None:
                    raise PermissionError('微信未绑定')
                headers['Authorization'] = 'Bearer ' + self.account.token
                body = body | {'base_info': {'channel_version': '2.4.10', 'bot_agent': 'DaFeiYu/0.1.0'}}
        try:
            async with asyncio.timeout(40):
                async with self.http.stream('POST' if body is not None else 'GET',
                        url + '/ilink/bot/' + endpoint, json=body, params=params, headers=headers) as response:
                    if response.status_code != 200:
                        raise RuntimeError('微信 HTTP 请求失败')
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2 * 1024 * 1024:
                            raise ValueError('微信响应过大')
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError('微信响应无效')
            if result.get('ret') == -14 or result.get('errcode') == -14:
                raise SessionExpired('微信登录已过期，请重新扫码')
            if result.get('ret', 0) != 0 or result.get('errcode', 0) != 0:
                raise RuntimeError('微信服务拒绝请求')
            return result
        except (httpx.HTTPError, TimeoutError, json.JSONDecodeError):
            raise RuntimeError('微信网络请求失败或超时') from None

    async def send(self, context, text):
        return await self.send_item(context, {'type': 1, 'text_item': {'text': text[:2000]}})

    async def send_image(self, context, data, permitted=lambda: True):
        from .wechat_media import send_image
        return await send_image(self, context, data, permitted)

    async def send_item(self, context, item):
        if not context or not self.account or not self.account.enabled:
            raise PermissionError('微信会话不可用')
        # No configurable recipient: only the person who scanned the QR code.
        return await self.request('sendmessage', {'msg': {
            'from_user_id': '', 'to_user_id': self.account.owner,
            'client_id': 'dafeiyu-' + uuid.uuid4().hex,
            'message_type': 2, 'message_state': 2, 'context_token': context,
            'item_list': [item]}})

    async def close(self):
        await self.http.aclose()


class WeChatChannel:
    def __init__(self, core, account, api=None):
        self.core, self.account = core, account
        self.api = api or ILink(account)
        self.key = 'wechat:' + hashlib.sha256((account.bot + account.owner).encode()).hexdigest()
        self.queue = asyncio.Queue(maxsize=20)
        self.context = None  # Require a fresh incoming message after every restart.
        self.context_time = 0
        self.last_event = core.sequence
        self.sent = deque(core.store.get(self.key + ':sent', []), maxlen=20)
        self.notifications = deque(maxlen=20)
        self.stopping = False
        self.memes = MemeCatalog()
        self.meme_gate = MemeGate(core.store, self.key)
        self.image_job = None
        self.cancel_generation = 0
        self.held = None
        self.processing = False
        self.poll_paused = False
        self.accepted_mid = None

    def accept(self, message):
        if (not isinstance(message, dict) or message.get('from_user_id') != self.account.owner or
                message.get('group_id') or message.get('message_type') != 1 or
                message.get('message_state', 2) != 2 or message.get('delete_time_ms')):
            return None
        context = message.get('context_token')
        message_id = message.get('message_id')
        if (not isinstance(context, str) or not context or len(context) > 16384 or
                type(message_id) not in (int, str) or not str(message_id) or len(str(message_id)) > 128):
            return None
        ident = str(message_id)
        items = message.get('item_list', [])
        if not isinstance(items, list):
            return None
        texts = [item.get('text_item', {}).get('text', '') for item in items
                 if isinstance(item, dict) and item.get('type') == 1 and isinstance(item.get('text_item'), dict)]
        text = '\n'.join(t for t in texts if isinstance(t, str)).strip()[:2000]
        self.accepted_mid = None
        if text and not text.startswith('/'):
            self.accepted_mid = self.core.store.continuity.admit(self.key, ident, text)
            if self.accepted_mid is None:
                return None
        elif not self.core.store.claim_message(self.key, ident):
            return None
        self.context, self.context_time = context, time.time()
        self.core.wechat_ready = self.core.wechat_state == 'connected'
        return text, context

    async def send(self, context, text):
        if self.stopping or self.core.store.get('wechat_transport', 'ilink') != 'ilink':
            return
        self.core.store.audit('wechat_send', 'attempted')
        try:
            await self.api.send(context, text)
            self.core.store.audit('wechat_send', 'succeeded')
            return True
        except SessionExpired:
            raise
        except Exception:
            # Ambiguous sends are never automatically retried.
            self.core.store.audit('wechat_send', 'failed_no_retry')
            self.core.emit('notice', text='微信消息未确认送达；为避免重复发送，不会自动重试。')
            return False

    async def send_meme(self, context, ident, automatic=False, generation=None):
        cancel_generation = self.cancel_generation
        def permitted():
            return (not self.stopping and self.core.store.get('wechat_transport', 'ilink') == 'ilink'
                    and cancel_generation == self.cancel_generation and
                    (not automatic or self.meme_gate.enabled() and
                     generation == self.meme_gate.generation() and not self.core.settings.dnd and not self.core.env.focused))
        if not permitted():
            self.core.store.audit('wechat_meme', 'skipped_policy')
            return False
        if self.image_job and not self.image_job.done():
            self.core.store.audit('wechat_meme', 'skipped_busy')
            if not automatic:
                await self.send(context, '上一张表情还在发送，请稍等。')
            return False
        try:
            data = self.memes.read(ident)
        except (ValueError, OSError):
            self.core.store.audit('wechat_meme', 'skipped_asset')
            if not automatic:
                await self.send(context, '这张表情暂不可用，请用 /memes 查看已有编号。')
            return False
        if not self.meme_gate.claim(ident, automatic):
            self.core.store.audit('wechat_meme', 'skipped_burst_guard')
            if not automatic:
                await self.send(context, '表情发送有点频繁，请稍后再试。')
            return False
        self.core.store.audit('wechat_meme', 'attempted')
        try:
            self.image_job = asyncio.create_task(self.api.send_image(context, data, permitted))
            await self.image_job
            self.core.store.audit('wechat_meme', 'succeeded')
            return True
        except asyncio.CancelledError:
            self.core.store.audit('wechat_meme', 'cancelled')
            if asyncio.current_task().cancelling():
                raise
            return False
        except SessionExpired:
            raise
        except Exception:
            self.core.store.audit('wechat_meme', 'failed_no_retry')
            # Plain text replies have already been sent; media failure never loses them.
            if not automatic:
                await self.send(context, '表情未确认送达，未自动重试。可以稍后重新点发。')
            return False

    async def memory_control(self, text, context):
        words = text.split()
        if not words or words[0] != '/memory':
            return False
        action = words[1] if len(words) == 2 else 'status'
        if len(words) > 2 or action not in ('status', 'on', 'off', 'rebuild'):
            await self.send(context, '用法：/memory 查看索引；/memory on、off、rebuild。重建不会删除原始记忆。')
            return True
        state = await self.core.memory.control(action)
        status = '已就绪' if state['ready'] else '准备中或暂不可用，关键词检索继续工作'
        await self.send(context, f'本地向量记忆：{"开" if state["enabled"] else "关"}；{status}。'
            f'已索引 {state["indexed"]}/{state["total"]} 条记忆。\n'
            'SQLite 原文保留；/memory off 关闭向量检索，/memory on 开启，/memory rebuild 重建索引。')
        return True

    async def meme_control(self, text, context):
        words = text.split()
        if not words or words[0] != '/memes':
            return False
        if len(words) == 2 and words[1] in ('on', 'off'):
            self.meme_gate.set_enabled(words[1] == 'on')
        elif len(words) != 1:
            await self.send(context, '用法：/memes、/memes on、/memes off；/meme 087 点发表情。')
            return True
        items = self.memes.available()
        reply = ('自动配图：' + ('开' if self.meme_gate.enabled() else '关') +
                 '\n自动配图自由选择，不设每小时张数配额；仅保留10秒发送间隔防刷屏。' +
                 '\n发送 /meme 编号 点发；/memes off 关闭自动配图，仍可手动点发。\n' +
                 '\n'.join(i['id'] + ' · ' + i['title'] for i in items))
        if not items:
            reply += '\n本机尚未安装表情素材。'
        await self.send(context, reply)
        return True

    async def poll(self):
        delay = 1
        while True:
            if self.core.handoff_requested:
                self.poll_paused = True
                await asyncio.sleep(.1)
                continue
            try:
                result = await self.api.request('getupdates', {
                    'get_updates_buf': self.core.store.get(self.key + ':cursor', '')})
                self.core.wechat_state = 'connected'
                messages = result.get('msgs', [])
                if not isinstance(messages, list) or len(messages) > 1000:
                    raise ValueError('微信消息列表无效')
                for message in messages:
                    accepted = self.accept(message)
                    if accepted is None:
                        continue
                    text, context = accepted
                    if text in ('/browser', '/browser pause', '/browser auto'):
                        try:
                            action = {'/browser': 'status', '/browser pause': 'pause', '/browser auto': 'auto'}[text]
                            state = await self.core.browser.control(action)
                            label = {'auto': '自主浏览', 'pause': '已暂停', 'manual': '由你接管'}[state['mode']]
                            await self.send(context, f"独立浏览器：{label}。\n{state.get('title', '')}\n"
                                '电脑上右键桌宠 → 她的浏览器，可查看和接管。')
                        except Exception:
                            await self.send(context, '独立浏览器暂时连不上；聊天不受影响。')
                        continue
                    if text in ('/context', '/followup on', '/followup off', '关闭跟进', '开启跟进', '不要再问进度了'):
                        continuity = self.core.store.continuity
                        mid = self.accepted_mid or continuity.append('user', text, 'wechat')
                        reply = continuity.observe(text, 'wechat', mid)
                        reply_id = continuity.append('assistant', reply, 'wechat', delivery='pending')
                        delivered = await self.send(context, reply)
                        continuity.delivered(reply_id, delivered)
                        continuity.inbox_state([mid], 'answered' if delivered else 'failed')
                        continue
                    if await self.identity_command(text, context):
                        continue
                    if await self.meme_control(text, context):
                        continue
                    if await self.memory_control(text, context):
                        continue
                    if text == '/cancel':
                        self.cancel_generation += 1
                        self.core.store.continuity.cancel_inbox()
                        self.held = None
                        if self.image_job and not self.image_job.done():
                            self.image_job.cancel()
                        while not self.queue.empty():
                            self.queue.get_nowait()
                        if self.core.current and self.core.current.get('origin') == 'wechat':
                            await self.core.cancel()
                        await self.send(context, '微信任务已取消；已排队的消息已清空。')
                    elif self.queue.full():
                        self.core.store.continuity.inbox_state([self.accepted_mid], 'rejected')
                        await self.send(context, '消息有点多，请稍后再发。')
                    else:
                        mid = self.accepted_mid
                        self.queue.put_nowait((text, context, mid))
                        self.core.wechat_receiving = True
                cursor = result.get('get_updates_buf')
                if isinstance(cursor, str) and cursor and len(cursor) <= 65536:
                    self.core.store.put(self.key + ':cursor', cursor)
                delay = 1
                await asyncio.sleep(.5)
            except SessionExpired:
                self.core.wechat_state = 'expired'
                self.core.wechat_ready = False
                self.core.emit('notice', text='微信绑定已过期，请重新扫码。')
                raise
            except Exception as error:
                self.core.wechat_state = 'reconnecting'
                self.core.wechat_ready = False
                LOG.warning('WeChat polling: %s', type(error).__name__)
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    async def identity_command(self, text, context):
        from .growth import describe, version_text
        words = text.split()
        if not words or words[0] not in ('/identity', '/growth'):
            return False
        growth = self.core.store.growth
        snapshot = growth.snapshot()
        try:
            if words[0] == '/identity' and len(words) == 1:
                reply = describe(snapshot)
                if snapshot['versions']:
                    reply += '\n最近版本：' + '、'.join(str(r['revision']) for r in snapshot['versions'][:5])
                reply += '\n/identity 版本号 查看依据\n/growth off 关闭自动更新\n/growth on 开启\n/growth undo 撤销最近更新'
            elif words[0] == '/identity' and len(words) == 2 and words[1].isdigit():
                row = next((r for r in snapshot['versions'] if str(r['revision']) == words[1]), None)
                if row is None:
                    raise ValueError('未找到该版本（显示最近 30 个版本）。')
                reply = f"版本 {row['revision']} · {row['created']}\n" + version_text(row)
                if row['undone_by'] is not None:
                    reply += f"\n已被版本 {row['undone_by']} 撤销。"
                if len(reply) > 3500:
                    reply = reply[:3500] + '\n内容较长，完整依据请在桌面“身份与成长”中查看。'
            elif words[0] == '/growth' and len(words) == 2 and words[1] in ('on', 'off', 'undo'):
                reply = describe(growth.control(words[1], snapshot['revision']))
            else:
                reply = '用法：/identity 或 /identity 版本号；/growth on、/growth off、/growth undo。'
        except ValueError as error:
            reply = str(error)
        await self.send(context, reply)
        return True

    def normalize(self, item):
        if len(item) == 3:
            return item
        text, context = item
        mid = self.core.store.continuity.append('user', text, 'wechat') if text and not text.startswith('/') else None
        return text, context, mid

    async def collect(self, first, existing=None):
        """Trailing-edge debounce, bounded by 4s / 6 messages / 2000 characters."""
        parts = list(existing or []) + [first]
        size = sum(len(p[0]) for p in parts) + len(parts) - 1
        deadline = time.monotonic() + 4
        generation = self.cancel_generation
        while len(parts) < 6:
            timeout = min(self.core.settings.wechat_batch_seconds, deadline - time.monotonic())
            if timeout <= 0:
                break
            try:
                item = self.normalize(await asyncio.wait_for(self.queue.get(), timeout))
            except TimeoutError:
                break
            if generation != self.cancel_generation:
                self.held = item  # Preserve messages received AFTER a cancel boundary.
                break
            if not item[0] or item[0].startswith('/') or size + 1 + len(item[0]) > 2000:
                self.held = item
                break
            parts.append(item)
            size += 1 + len(item[0])
        return parts

    async def consume(self):
        while True:
            item = self.held or await self.queue.get()
            self.held = None
            self.processing = True
            self.core.wechat_receiving = True
            try:
                await self.consume_one(self.normalize(item))
            finally:
                self.processing = False
                self.core.wechat_receiving = not self.queue.empty() or self.held is not None

    async def consume_one(self, item):
        text, context, mid = item
        generation = self.cancel_generation
        if text in ('/bili sharing on', '/bili sharing off'):
            config = self.core.bili.settings.model_dump()
            config['share_wechat'] = text.endswith(' on')
            await self.core.bili.control({'action':'configure','settings':config})
            await self.send(context, '好，逛 B 站遇到有意思的就把链接和心得发给你。' if config['share_wechat'] else '好，B 站心得继续记着，先不主动发给你。')
        elif text in ('/sharing on', '/sharing off'):
            self.core.store.put('browser_sharing_enabled', text.endswith(' on'))
            await self.send(context, '好，看到有趣的再分享给你。' if text.endswith(' on') else '好，逛网页的事先留给我自己。')
        elif text.split()[:1] == ['/meme']:
            words = text.split()
            if len(words) == 2 and words[1] in self.memes.items:
                await self.send_meme(context, words[1])
            else:
                await self.send(context, '发送 /memes 查看编号，再用 /meme 087 点发表情。')
        elif text == '/status':
            life = self.core.life
            budget = self.core.store.budget_usage()
            await self.send(context, f'DeepSeek在这里。精力 {life.needs.energy:.0f}，'
                f'心情 {life.needs.mood:.0f}，亲密度 {life.needs.trust:.0f}。'
                f'联网认知：{"开" if self.core.settings.online else "关"}。\n'
                f'主动交流：{"开" if self.core.settings.proactive else "关"}；'
                f'勿扰：{"开" if self.core.settings.dnd else "关"}；'
                f'微信会话：{"可用" if self.core.wechat_ready else "等待新消息"}。\n'
                f'连发合并：{self.core.settings.wechat_batch_seconds:g}秒；'
                f'允许不回复：{"开" if self.core.settings.wechat_silence else "关"}。\n'
                f'今日 API 请求：{budget["requests"]}/{self.core.settings.daily_requests}；'
                f'Token 已用与预留：{budget["reserved"]}/{self.core.settings.daily_tokens}。')
        elif not text:
            await self.send(context, '目前请发文字给我，图片、语音和文件还不能读取。')
        else:
            parts = await self.collect(item) if not text.startswith('/') else [item]
            text = '\n'.join(p[0] for p in parts)
            context = parts[-1][1]
            # Keep received messages during another task; never execute them twice.
            try:
                async with asyncio.timeout(245):
                    while self.core.current and generation == self.cancel_generation:
                        await asyncio.sleep(.05)
            except TimeoutError:
                self.core.store.continuity.inbox_state([p[2] for p in parts], 'failed')
                await self.send(context, '消息已保存，但前一个任务一直未结束。请稍后再找我，或取消当前任务。')
                return
            if generation != self.cancel_generation:
                return
            # Fold one late batch into an unsent draft, never replay a sent reply.
            # One revision caps paid work and prevents starvation during long bursts.
            for revision in range(2):
                text = '\n'.join(p[0] for p in parts)
                context = parts[-1][1]
                ids = [p[2] for p in parts if p[2] is not None]
                # A desktop task may have started during the late-batch wait.
                try:
                    async with asyncio.timeout(245):
                        while self.core.current and generation == self.cancel_generation:
                            await asyncio.sleep(.05)
                except TimeoutError:
                    self.core.store.continuity.inbox_state(ids, 'failed')
                    await self.send(context, '消息已保存，但前一个任务还没结束。可以稍后再聊。')
                    return
                if generation != self.cancel_generation:
                    return
                self.core.store.continuity.inbox_state(ids, 'processing')
                self.core.emit('wechat_incoming', text=text)
                task_id = self.core.submit(text, origin='wechat', received_ids=ids or None)
                answer = await asyncio.shield(self.core.job)
                result = next((e for e in reversed(self.core.events) if e.get('id') == task_id and e['kind'] == 'result'), {})
                if generation != self.cancel_generation:
                    self.core.store.continuity.supersede(result.get('message_id'))
                    return
                late = None
                if revision == 0 and result.get('ok') and len(parts) < 6 and not text.startswith('/'):
                    late = self.held
                    self.held = None
                    if late is None and not self.queue.empty():
                        late = self.normalize(self.queue.get_nowait())
                    if late and (not late[0] or late[0].startswith('/') or len(text) + 1 + len(late[0]) > 2000):
                        self.held, late = late, None
                if late:
                    self.core.store.continuity.supersede(result.get('message_id'))
                    parts = await self.collect(late, existing=parts)
                    if generation != self.cancel_generation:
                        return
                    continue
                break
            if result.get('silent') or not answer:
                self.core.store.continuity.inbox_state(ids, 'silent' if result.get('ok') else 'failed')
                return
            delivered = await self.send(context, answer)
            self.core.store.continuity.delivered(result.get('message_id'), delivered)
            self.core.store.continuity.inbox_state(ids, 'answered' if delivered and result.get('ok') else 'failed')
            if delivered and result.get('ok'):
                self.core.store.remember_dialogue(text, answer)
            if delivered and result.get('ok') and result.get('meme_id'):
                await self.send_meme(context, result['meme_id'], True, result.get('meme_generation'))

    async def forward_events(self):
        s, now = self.core.settings, time.time()
        self.core.wechat_ready = bool(self.context and now - self.context_time < 12 * 3600
                                     and self.core.wechat_state == 'connected')
        events = [e for e in self.core.events if e['seq'] > self.last_event]
        self.last_event = self.core.sequence
        for event in events:
            if (event['kind'] in ('reminder', 'browser_share', 'bili_share') or
                    event['kind'] == 'followup' and event.get('origin') == 'wechat' or
                    event['kind'] == 'result' and event.get('proactive') and event.get('ok') and not event.get('silent')):
                self.notifications.append((event.get('started', event.get('created', now)), event['text'], event.get('meme_id'), event.get('meme_generation'),
                                           event.get('source_id') if event['kind'] == 'followup' else None,
                                           event['kind'], event.get('message_id'),
                                           bool(event['kind']=='browser_share' and event.get('requested')),
                                           event.get('request_id')))
        self.sent = deque((t for t in self.sent if now - t < 3600), maxlen=20)
        # Deferred notifications expire locally; no surprise catch-up after restarting.
        # A delayed follow-up becomes stale when the activity changes or is disabled.
        activity = self.core.store.get('current_activity')
        retained = deque(maxlen=20)
        for n in self.notifications:
            stale = (now - n[0] > 3600 or n[5] in ('result', 'browser_share') and
                     n[0] < max(self.context_time, self.core.store.get('last_user_at', 0)) or
                     n[5] == 'browser_share' and not self.core.store.get('browser_sharing_enabled', True) or
                     n[5] == 'bili_share' and (not self.core.bili.settings.share_wechat or not self.core.bili.settings.enabled) or
                     n[4] is not None and not (self.core.store.get('activity_followups', True) and activity
                     and activity['status'] == 'active' and activity['source_id'] == n[4]))
            if stale:
                self.core.store.continuity.delivered(n[6], False)
                if n[5] == 'bili_share':
                    self.core.bili.share_result(n[6], 'cancelled')
                if n[8]:
                    self.core.store.task(n[8], 'cancelled', '分享已过时或被关闭，没有发送')
            else:
                retained.append(n)
        self.notifications = retained
        # Explicit user requests take precedence over unsolicited notifications.
        self.notifications = deque(sorted(self.notifications, key=lambda n: not n[7]), maxlen=20)
        requested = bool(self.notifications and self.notifications[0][7])
        if (self.notifications and self.notifications[0][5] == 'bili_share'
                and (self.core.handoff_requested or not s.online)):
            return
        if (not self.notifications or not self.core.wechat_ready or s.dnd or
                self.core.env.focused or not s.proactive or
                not requested and (len(self.sent) >= s.proactive_per_hour or
                self.sent and now - self.sent[-1] < s.proactive_cooldown)):
            return
        if self.notifications[0][5] != 'reminder' and (self.core.wechat_receiving or self.core.current or not requested and now - self.context_time < 90):
            return
        # Recheck social timing at the actual send boundary, including desktop
        # input that arrived after generation. Explicit reminders/requests remain usable.
        from .conversation_scene import scene
        if not requested and self.notifications[0][5] != 'reminder' and scene(self.core.store, now)['defer_unsolicited']:
            return
        self.sent.append(now)
        self.core.store.put(self.key + ':sent', list(self.sent))
        _, text, meme_id, generation, _, kind, mid, _, request_id = self.notifications.popleft()
        try:
            delivered = await self.send(self.context, text)
        except BaseException:
            if kind == 'bili_share':
                self.core.bili.share_result(mid, 'unconfirmed')
                self.core.store.continuity.delivered(mid, False)
            raise
        self.core.store.continuity.delivered(mid, delivered)
        if kind == 'bili_share':
            self.core.bili.share_result(mid, 'delivered' if delivered else 'unconfirmed')
            self.core.store.audit('bili_share', 'delivered' if delivered else 'unconfirmed_no_retry')
        if delivered and kind == 'browser_share':
            self.core.store.remember('已向用户分享实际浏览的网页：' + text)
        if request_id:
            self.core.store.task(request_id, 'succeeded' if delivered else 'failed',
                                 'iLink 已接受网页分享消息' if delivered else '发送未确认，不自动重试')
        if delivered and meme_id:
            await self.send_meme(self.context, meme_id, True, generation)

    async def proactive(self):
        while True:
            await asyncio.sleep(1)
            if self.core.handoff_requested:
                continue
            await self.forward_events()

    async def run(self):
        self.core.wechat_state = 'connecting'
        tasks = [asyncio.create_task(f()) for f in (self.poll, self.consume, self.proactive)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        except SessionExpired:
            self.core.wechat_state = 'expired'
            raise
        finally:
            self.stopping = True
            self.core.wechat_ready = False
            self.core.wechat_receiving = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.core.current and self.core.current.get('origin') == 'wechat':
                await self.core.cancel()
            self.core.store.continuity.interrupt_inbox()
            await self.api.close()


def read_account(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > 32768:
        raise ValueError('微信绑定文件过大')
    return Account.model_validate_json(path.read_text(encoding='utf-8'))


def save_account(path, account):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(account.model_dump_json())
    temporary.replace(path)


async def supervise(core, path):
    current, job = None, None
    try:
        while True:
            try:
                if core.store.get('wechat_transport', 'ilink') != 'ilink':
                    if job:
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                    current, job, core.ilink_job = None, None, None
                    await asyncio.sleep(1)
                    continue
                account = read_account(path)
                if account != current:
                    if job:
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                    current, job = account, None
                    if account and account.enabled:
                        core.wechat_channel = WeChatChannel(core, account)
                        job = asyncio.create_task(core.wechat_channel.run())
                        core.ilink_job = job
                    else:
                        core.wechat_channel = None
                        core.wechat_state = 'disabled' if account else 'unbound'
                if job and job.done():
                    # Wait for explicit rebind/change; never loop on an expired login.
                    await asyncio.gather(job, return_exceptions=True)
                    job = None
                    if core.wechat_state != 'expired':
                        core.wechat_state = 'failed'
            except Exception as error:
                if job:
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                    job = None
                current = None
                core.wechat_state = 'invalid_config'
                LOG.warning('WeChat config: %s', type(error).__name__)
            await asyncio.sleep(2)
    finally:
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)


async def login(path, qr_file=None, api=None):
    import qrcode
    api = api or ILink()
    old = read_account(path)
    try:
        result = await api.request('get_bot_qrcode', {'local_token_list': [old.token] if old else []},
                                   params={'bot_type': '3'}, authenticated=False)
        qr = qrcode.QRCode(border=4)
        qr.add_data(result['qrcode_img_content'])
        if qr_file:
            from qrcode.image.svg import SvgPathImage
            qr.make_image(image_factory=SvgPathImage).save(str(qr_file))
            print('二维码已保存。', flush=True)
        else:
            qr.print_ascii(invert=True)
        print('请用你的微信扫码并确认绑定DeepSeek。绑定后仅接受此微信账号的文字消息。', flush=True)
        print('微信对话在开启联网时会发往 DeepSeek；工具仍只能在桌面端发起。', flush=True)
        base, verify, deadline = BASE, None, time.monotonic() + 300
        while time.monotonic() < deadline:
            params = {'qrcode': result['qrcode']}
            if verify:
                params['verify_code'] = verify
            try:
                status = await api.request('get_qrcode_status', base=base, params=params, authenticated=False)
            except RuntimeError:
                await asyncio.sleep(2)
                continue
            kind = status.get('status')
            if kind == 'confirmed':
                account = Account(token=status['bot_token'], owner=status['ilink_user_id'],
                                  bot=status['ilink_bot_id'], base=status.get('baseurl') or BASE,
                                  revision=time.time_ns())
                save_account(path, account)
                print('绑定已保存。请在微信给DeepSeek发送 /status 或一句话。', flush=True)
                return
            if kind == 'scaned_but_redirect':
                host = status.get('redirect_host', '')
                base = api_base(host if '://' in host else 'https://' + host)
            elif kind == 'need_verifycode':
                verify = input('输入手机显示的配对码（不要发到聊天）：').strip()
            elif kind in ('expired', 'verify_code_blocked', 'binded_redirect'):
                raise RuntimeError('二维码已失效或已有绑定，请检查微信端绑定后重新运行')
            await asyncio.sleep(1)
        raise RuntimeError('等待扫码超时，请重新运行')
    finally:
        await api.close()


def main():
    parser = argparse.ArgumentParser(description='DeepSeek微信绑定（Docker 核心）')
    parser.add_argument('command', choices=['login', 'disable', 'enable', 'status', 'logout'])
    parser.add_argument('--qr-file', type=Path)
    parser.add_argument('--account-file', type=Path,
                        default=Path(os.getenv('LITTLE_BLUE_DATA_DIR', 'data/core')) / 'wechat-account.json')
    args = parser.parse_args()
    if args.command != 'status':
        from .handoff_guard import require_active
        require_active(args.account_file.parent)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    try:
        if args.command == 'login':
            asyncio.run(login(args.account_file, args.qr_file))
        elif args.command == 'logout':
            args.account_file.unlink(missing_ok=True)
            print('本机微信凭据已移除；微信端的绑定可在手机中解除。')
        else:
            account = read_account(args.account_file)
            if not account:
                print('尚未绑定微信。')
                return
            if args.command != 'status':
                account.enabled = args.command == 'enable'
                account.revision = time.time_ns()
                save_account(args.account_file, account)
            print('已绑定；微信通道' + ('开启。' if account.enabled else '关闭。'))
    except (KeyboardInterrupt, Exception) as error:
        # Pydantic/HTTP exceptions can contain credential values: never print them.
        print('微信操作未完成（' + type(error).__name__ + '），请检查网络或重新扫码。')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
