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
    'offline': '电脑这边还没连上，这次打不开。',
    'unavailable': '电脑上的播放器还没准备好，这次没打开。',
    'busy': '我还在处理上一个视频，这个先没打开。',
    'expired': '电脑没及时接到这次请求，没打开，之后也不会突然补播。',
    'disconnected': '电脑断开了，这段有没有播完，我还不能确定。',
    'restarted': '电脑上的程序重启了，视频中断了，没有自动重播。',
    'open_failed': '没播起来，网页没打开，或者里面的播放器没加载出来。',
    'play_failed': '网页打开了，但视频没播起来，可能需要登录或有播放限制。',
    'stalled': '视频卡住好一会儿了，这段没播完。',
    'taken_over': '页面被接管或切走了，我停下了，还没看完。',
    'interrupted': '这次中断了，没看完。',
    'closed': '视频页关掉了，这段没播完。',
    'unsupported': '这个暂时播不了，我现在只能打开 B 站第一 P、两小时以内的视频。',
    'incomplete': '这段没确认播完整，进度可能跳过了，不能算看完。',
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
        return {'error': '还没找到这个视频。把 B 站链接或 BV 号发我吧，现在还没播放。'} if re.search(r'视频|B站|b站|播放',words) or re.search(r'B站|B 站|bilibili|视频页面|视频封面',context_text,re.I) else None
    if len(refs) != 1: return {'error': '这里有好几个视频，你想让我看哪个？我先没打开。'}
    return {'url': refs[0], 'source': 'wechat-video:' + ':'.join(str(r['id']) for r in rows)}


def notice(store, job, kind, text, now):
    store.db.execute('INSERT OR IGNORE INTO video_watch_notices(job,kind,text,created) VALUES(?,?,?,?)', (job,kind,text,now))


def video_title(title):
    name = re.sub(r'[\s\x00-\x1f]+', ' ', title).strip()[:200]
    while name.startswith('《') and name.endswith('》'):
        name = name[1:-1].strip()
    return f'《{name}》' if name else '这个视频'


def finish(store, row, report, now):
    result = dict(report)
    if result['state'] == 'completed':
        duration = result.get('duration', 0)
        if (row['state'] != 'playing' or duration <= 0 or result.get('position',0) < duration-1.5
                or result.get('played',0) < max(0.5,duration*.9) or now-row['created'] < duration*.8):
            result.update(state='failed',reason='incomplete')
    store.db.execute('UPDATE video_watch_jobs SET state=?,result=?,updated=? WHERE id=?',
                     (result['state'],json.dumps(result,ensure_ascii=False),now,row['id']))
    if result['state']=='completed':
        text = f'{video_title(result.get("title", ""))}播完啦。'
    else:
        text = REASONS.get(result.get('reason'),REASONS['interrupted'])
    if result.get('cleanup')=='closed': text += '我开的那页也关好了。'
    elif result.get('cleanup')=='failed': text += '不过我开的那页没关成功，还留着。'
    elif result.get('cleanup')=='retained': text += '我没有再动那个网页。'
    # A delayed start notice is obsolete once the video has stopped. In particular,
    # a single sync batch gives both notices the same timestamp; alphabetical order
    # used to send "result" before "started".
    store.db.execute("UPDATE video_watch_notices SET state='superseded' WHERE job=? AND kind='started' AND state='ready'",(row['id'],))
    notice(store,row['id'],'result',text,now)


def expire(store, now):
    for row in store.db.execute("SELECT * FROM video_watch_jobs WHERE state IN ('queued','claimed','playing') AND (lease<? OR created<?)", (now,now-10800)).fetchall():
        finish(store,row,{'state':'failed','reason':'expired' if row['state']=='queued' else 'disconnected'},now)


def submit(store, selection, client, who, now=None):
    now = now or time.time()
    with store.lock, store.db:
        expire(store,now)
        old = store.db.execute('SELECT * FROM video_watch_jobs WHERE source=?',(selection['source'],)).fetchone()
        if old: return '这个请求已经收到过了，我没有重复打开。'
        heartbeat = store.get('video_watch_desktop',{})
        if heartbeat.get('client') != client or now-heartbeat.get('at',0)>25: return REASONS['offline']
        if not heartbeat.get('ready'): return REASONS['unavailable']
        if store.db.execute("SELECT 1 FROM video_watch_jobs WHERE state IN ('queued','claimed','playing')").fetchone(): return REASONS['busy']
        store.db.execute('INSERT INTO video_watch_jobs(id,source,client,session,token,requester,url,state,created,updated,lease) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex,selection['source'],client,heartbeat['session'],uuid.uuid4().hex,who,selection['url'],'queued',now,now,now+45))
    return '好呀，我去电脑上打开看看。'


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
                    notice(store,row['id'],'started',f'{video_title(report.title)}播起来了。',now)
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
        return '好，不继续看了。停止请求已经发过去，电脑连着的话就会停下。'
    generation=channel.cancel_generation
    selection=select_request(channel.core.store,ids,time.time())
    if not selection: return None
    if selection.get('error'): return selection['error']
    if not channel.core.settings.online or channel.core.handoff_requested:
        return '现在关着联网，或者程序正在维护，这次没打开视频。'
    if 'b23.tv/' in selection['url']:
        from .bili_chat import SharedVideoReader
        try: selection['url']=await SharedVideoReader('',channel.core.store).resolve(selection['url'])
        except Exception: return '这个短链接没打开。换个完整的 B 站链接或 BV 号给我吧。'
    if not selection['url'].endswith('/?p=1'): return REASONS['unsupported']
    if generation!=channel.cancel_generation:return '好，这次取消了，没打开网页。'
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
        # Also retire stale starts persisted by older versions before this fix.
        store.db.execute("UPDATE video_watch_notices SET state='superseded' WHERE kind='started' AND state='ready' AND job IN (SELECT id FROM video_watch_jobs WHERE state NOT IN ('queued','claimed','playing'))")
        row=store.db.execute("SELECT n.* FROM video_watch_notices n JOIN video_watch_jobs j ON j.id=n.job WHERE j.requester=? AND n.state='ready' ORDER BY n.created,CASE n.kind WHEN 'started' THEN 0 ELSE 1 END LIMIT 1",(requester(channel),)).fetchone()
        if not row:return
        store.db.execute("UPDATE video_watch_notices SET state='sending' WHERE job=? AND kind=?",(row['job'],row['kind']))
    # Claim before network I/O. An ambiguous send is never replayed on restart.
    mid=store.continuity.append('assistant',row['text'],'wechat',delivery='pending')
    try: sent=bool(await channel.send(channel.context,row['text']))
    finally:
        with store.lock,store.db:
            store.db.execute("UPDATE video_watch_notices SET state=?,message_id=? WHERE job=? AND kind=?",('delivered' if locals().get('sent') else 'unconfirmed',mid,row['job'],row['kind']))
        store.continuity.delivered(mid,locals().get('sent',False))
