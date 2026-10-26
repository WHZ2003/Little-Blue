"""Paired-desktop video jobs. History replication is never executable authority."""
import hashlib
import json
import re
import time
import uuid
from typing import Literal
from pydantic import Field
from .models import Model
from .bili_chat import references

ACTIVE = ('queued', 'claimed', 'playing')
REASONS = {
    'offline': '电脑上的大肥鱼未连接，无法打开播放。',
    'unavailable': '电脑上的视频插件尚未就绪，无法打开播放。',
    'busy': '电脑正在处理另一项视频观看，未打开新视频。',
    'expired': '电脑没有及时接收这次观看请求，已取消，之后不会补开。',
    'disconnected': '电脑连接中断，无法确认播放完成。',
    'restarted': '桌面程序已重启，这次观看已中断，不会自动重播。',
    'open_failed': '视频页面未能打开，或页面没有可用播放器。',
    'play_failed': '页面打开了，但未能确认视频开始播放（可能需登录或有播放限制）。',
    'stalled': '视频长时间没有继续播放，未确认看完。',
    'taken_over': '页面已切换或不再由我管理，已停止这次观看。',
    'interrupted': '观看被中止，没有看完。',
    'closed': '视频页面被关闭，没有看完。',
    'unsupported': '当前只支持两小时以内、第一 P 的 B 站视频。',
    'incomplete': '播放进度出现跳转或缺少完整播放的证据，不能说看完。',
}


class Report(Model):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    token: str = Field(pattern=r'^[a-f0-9]{32}$')
    session: str = Field(pattern=r'^[a-f0-9]{32}$')
    state: Literal['playing', 'completed', 'failed']
    reason: str = Field('', max_length=40)
    title: str = Field('', max_length=200)
    position: float = Field(0, ge=0, le=7205, allow_inf_nan=False)
    duration: float = Field(0, ge=0, le=7200, allow_inf_nan=False)
    played: float = Field(0, ge=0, le=8000, allow_inf_nan=False)
    frames: int = Field(0, ge=0, le=50000)
    cleanup: Literal['closed', 'retained', 'failed', 'none'] = 'none'


class Claim(Model):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    token: str = Field(pattern=r'^[a-f0-9]{32}$')


class Desktop(Model):
    session: str = Field(pattern=r'^[a-f0-9]{32}$')
    ready: bool = Field(strict=True)
    claim: Claim | None = None
    reports: list[Report] = Field(default_factory=list, max_length=8)


def setup(store):
    with store.lock, store.db:
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS video_watch_jobs(
          id TEXT PRIMARY KEY, source TEXT UNIQUE NOT NULL, client TEXT NOT NULL,
          session TEXT NOT NULL, token TEXT NOT NULL, requester TEXT NOT NULL,
          url TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
          updated REAL NOT NULL, lease REAL NOT NULL, result TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS video_watch_notices(
          job TEXT, kind TEXT, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready',
          created REAL NOT NULL, message_id INTEGER, PRIMARY KEY(job,kind));
        ''')


def requester(channel):
    return channel.key + ':' + hashlib.sha256(channel.account.owner.encode()).hexdigest()


def original(text):
    return text.split('\n[图片自动识别结果', 1)[0].replace('[用户发送了微信图片]', '').strip()


def intent(text):
    # Only the user's words authorize opening a page. OCR supplies a reference, never an action.
    text = original(text)
    if re.search(r'(不要|别|不用|先不|不许|停止).{0,8}(看|播|打开)|只.{0,5}(字幕|简介)|总结|摘要|翻译|[“「].*(看|播放).*[”」]|他说|她说|举例|假设|为什么|为啥|怎么|以后|每次|之前|昨天|上次|截图|这张图|封面', text):
        return False
    return bool(re.search(r'看看|看一下|看下|看这个|看.{0,10}视频|播放|打开.{0,15}(B站|b站|视频|这个)', text))


def select_request(store, ids, now):
    if not ids: return None
    rows = store.db.execute('SELECT * FROM conversation WHERE id IN ('+','.join('?' for _ in ids)+') ORDER BY id', ids).fetchall()
    if len(rows) != len(set(ids)) or any(r['role'] != 'user' or r['origin'] != 'wechat' or now-r['created'] > 300 for r in rows):
        return None
    words = '\n'.join(original(r['text']) for r in rows)
    if not intent(words): return None
    refs = list(dict.fromkeys(ref for r in rows for ref in references(r['text'])))
    context_text='\n'.join(r['text'] for r in rows)
    if not refs:
        if not re.search(r'这个|视频|播放', words):return None
        prior = store.db.execute("SELECT * FROM conversation WHERE role='user' AND origin='wechat' AND id<? ORDER BY id DESC LIMIT 1", (rows[0]['id'],)).fetchone()
        if prior and now-prior['created'] <= 120:
            refs = references(prior['text'])
            context_text+='\n'+prior['text']
    if not refs:
        # Do not turn arbitrary "看看这个" into a video command without video context.
        return {'error': '没有识别到可打开的视频，请发这个视频的 B 站链接或 BV 号；还没有播放。'} if re.search(r'视频|B站|b站|播放',words) or re.search(r'B站|B 站|bilibili|视频页面|视频封面',context_text,re.I) else None
    if len(refs) != 1: return {'error': '识别到不止一个视频，请明确这次要播放哪一个；还没有打开网页。'}
    return {'url': refs[0], 'source': 'wechat-video:' + ':'.join(str(r['id']) for r in rows)}


def notice(store, job, kind, text, now):
    store.db.execute('INSERT OR IGNORE INTO video_watch_notices(job,kind,text,created) VALUES(?,?,?,?)', (job,kind,text,now))


def finish(store, row, report, now):
    result = dict(report)
    if result['state'] == 'completed':
        duration = result.get('duration', 0)
        if (row['state'] != 'playing' or duration <= 0 or result.get('position',0) < duration-1.5
                or result.get('played',0) < max(0.5,duration*.9) or now-row['created'] < duration*.8):
            result.update(state='failed',reason='incomplete')
    store.db.execute('UPDATE video_watch_jobs SET state=?,result=?,updated=? WHERE id=?',
                     (result['state'],json.dumps(result,ensure_ascii=False),now,row['id']))
    title = re.sub(r'[\r\n\x00-\x1f]', ' ', result.get('title',''))[:200]
    if result['state']=='completed':
        text = f'电脑上的《{title or "你发的视频"}》已实际播放到结尾，播放进度已核验。'
    else:
        text = '这次视频没有确认看完：'+REASONS.get(result.get('reason'),REASONS['interrupted'])
    if result.get('cleanup')=='closed': text += ' 已关闭我自己打开的标签页。'
    elif result.get('cleanup')=='failed': text += ' 自己的标签页关闭失败，仍保留着。'
    elif result.get('cleanup')=='retained': text += ' 页面已保留，没有关闭你的网页。'
    notice(store,row['id'],'result',text,now)


def expire(store, now):
    for row in store.db.execute("SELECT * FROM video_watch_jobs WHERE state IN ('queued','claimed','playing') AND (lease<? OR created<?)", (now,now-10800)).fetchall():
        finish(store,row,{'state':'failed','reason':'expired' if row['state']=='queued' else 'disconnected'},now)


def submit(store, selection, client, who, now=None):
    now = now or time.time()
    with store.lock, store.db:
        expire(store,now)
        old = store.db.execute('SELECT * FROM video_watch_jobs WHERE source=?',(selection['source'],)).fetchone()
        if old: return '这个观看请求已经登记，不会重复打开；结果以实际播放回执为准。'
        heartbeat = store.get('video_watch_desktop',{})
        if heartbeat.get('client') != client or now-heartbeat.get('at',0)>25: return REASONS['offline']
        if not heartbeat.get('ready'): return REASONS['unavailable']
        if store.db.execute("SELECT 1 FROM video_watch_jobs WHERE state IN ('queued','claimed','playing')").fetchone(): return REASONS['busy']
        store.db.execute('INSERT INTO video_watch_jobs(id,source,client,session,token,requester,url,state,created,updated,lease) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex,selection['source'],client,heartbeat['session'],uuid.uuid4().hex,who,selection['url'],'queued',now,now,now+45))
    return '已向在线电脑提交观看请求。确认打开播放后会告诉你，播放完成或失败也会回报；现在还没有看完。'


def exchange(store, client, command, now=None):
    now=now or time.time()
    data=Desktop.model_validate(command)
    with store.lock, store.db:
        expire(store,now)
        # A new runtime never inherits permission to replay work from an old runtime.
        for row in store.db.execute("SELECT * FROM video_watch_jobs WHERE client=? AND session<>? AND state IN ('queued','claimed','playing')",(client,data.session)).fetchall():
            finish(store,row,{'state':'failed','reason':'restarted'},now)
        heartbeat={'client':client,'session':data.session,'ready':data.ready,'at':now}
        store.db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',('video_watch_desktop',json.dumps(heartbeat)))
        accepted=[]
        for report in data.reports:
            row=store.db.execute('SELECT * FROM video_watch_jobs WHERE id=?',(report.id,)).fetchone()
            if not row or (row['client'],row['session'],row['token']) != (client,report.session,report.token):
                raise PermissionError('Invalid video job receipt')
            if row['state'] not in ACTIVE:
                accepted.append(report.id+':'+report.state); continue
            if row['state']=='queued': raise ValueError('Job must be claimed before playback')
            if report.state=='playing':
                if report.position <= 0 or report.duration <= 0: raise ValueError('Playback evidence missing')
                if row['state']=='claimed':
                    store.db.execute("UPDATE video_watch_jobs SET state='playing',result=?,updated=? WHERE id=?",(report.model_dump_json(),now,row['id']))
                    title=re.sub(r'[\r\n\x00-\x1f]',' ',report.title)[:200]
                    notice(store,row['id'],'started',f'已在电脑上打开自己的标签页，确认《{title or "你发的视频"}》开始播放。',now)
            else: finish(store,row,report.model_dump(),now)
            accepted.append(report.id+':'+report.state)
        claimed=None
        if data.claim:
            row=store.db.execute('SELECT * FROM video_watch_jobs WHERE id=?',(data.claim.id,)).fetchone()
            if not row or (row['client'],row['session'],row['token']) != (client,data.session,data.claim.token):
                raise PermissionError('Invalid video claim')
            if row['state']=='queued' and data.ready:
                store.db.execute("UPDATE video_watch_jobs SET state='claimed',updated=?,lease=? WHERE id=?",(now,now+90,row['id']))
                claimed=row['id']
            elif row['state'] in ('claimed','playing'): claimed=row['id']
        store.db.execute("UPDATE video_watch_jobs SET lease=? WHERE client=? AND session=? AND state IN ('claimed','playing')",(now+90,client,data.session))
        row=store.db.execute("SELECT id,token,url,session,lease FROM video_watch_jobs WHERE client=? AND session=? AND state='queued' ORDER BY created LIMIT 1",(client,data.session)).fetchone()
        active=[r[0] for r in store.db.execute("SELECT id FROM video_watch_jobs WHERE client=? AND session=? AND state IN ('queued','claimed','playing')",(client,data.session))]
    return {'job':dict(row) if row else None,'claimed':claimed,'accepted':accepted,'active':active,'server_time':now}


async def handle(channel, ids):
    from .companion import profile
    paired=profile(channel.core.store)
    if not paired: return None
    words='\n'.join(original(r['text']) for r in channel.core.store.continuity.received(ids)) if ids else ''
    if re.search(r'^(?:先)?(?:别看了|不要看了|停止播放|停止看视频|取消观看)[。！!\s]*$',words):
        cancel(channel)
        return '已取消这次观看请求，电脑连接正常时会停止自己的视频页，不会操作你的网页。'
    generation=channel.cancel_generation
    selection=select_request(channel.core.store,ids,time.time())
    if not selection: return None
    if selection.get('error'): return selection['error']
    if not channel.core.settings.online or channel.core.handoff_requested:
        return '当前联网已关闭或程序正在维护，未打开视频。'
    if 'b23.tv/' in selection['url']:
        from .bili_chat import SharedVideoReader
        try: selection['url']=await SharedVideoReader('',channel.core.store).resolve(selection['url'])
        except Exception: return '视频短链接没有解析成功，未打开网页。请发完整 B 站链接或 BV 号。'
    if not selection['url'].endswith('/?p=1'): return REASONS['unsupported']
    if generation!=channel.cancel_generation:return '这次观看请求已取消，未打开网页。'
    return submit(channel.core.store,selection,paired['client'],requester(channel))


def cancel(channel):
    with channel.core.store.lock,channel.core.store.db:
        for row in channel.core.store.db.execute("SELECT * FROM video_watch_jobs WHERE requester=? AND state IN ('queued','claimed','playing')",(requester(channel),)).fetchall():
            finish(channel.core.store,row,{'state':'failed','reason':'interrupted'},time.time())


async def deliver_results(channel):
    store=channel.core.store;now=time.time()
    with store.lock,store.db: expire(store,now)
    if not channel.core.wechat_ready or channel.processing or channel.core.current or channel.stopping: return
    with store.lock,store.db:
        store.db.execute("UPDATE video_watch_notices SET state='expired' WHERE state='ready' AND created<?",(now-12*3600,))
        row=store.db.execute("SELECT n.* FROM video_watch_notices n JOIN video_watch_jobs j ON j.id=n.job WHERE j.requester=? AND n.state='ready' ORDER BY n.created,n.kind LIMIT 1",(requester(channel),)).fetchone()
        if not row:return
        store.db.execute("UPDATE video_watch_notices SET state='sending' WHERE job=? AND kind=?",(row['job'],row['kind']))
    # Claim before network I/O. An ambiguous send is never replayed on restart.
    mid=store.continuity.append('assistant',row['text'],'wechat',delivery='pending')
    try: sent=bool(await channel.send(channel.context,row['text']))
    finally:
        with store.lock,store.db:
            store.db.execute("UPDATE video_watch_notices SET state=?,message_id=? WHERE job=? AND kind=?",('delivered' if locals().get('sent') else 'unconfirmed',mid,row['job'],row['kind']))
        store.continuity.delivered(mid,locals().get('sent',False))
