"""Evidence-backed shared preferences and executable, narrowly scoped settings.

The model proposes values; SQLite commits, consumers and receipts decide what worked.
No generated proposal can edit code, persona, credentials or tool permissions.
"""
import asyncio
import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import Field
from .models import Model, Settings

DEFAULTS = {'message_mode': 'smart', 'short_limit': 30, 'long_form_mode': 'whole', 'auto_close_video_tabs': False}
BOOLEAN_KEYS = {'proactive', 'auto_close_video_tabs'}
CHOICES = {'reply_length': ['concise', 'balanced', 'detailed'],
           'message_mode': ['smart', 'whole', 'split_short'], 'long_form_mode': ['whole', 'split']}
TEXT_KEYS = {'address': 40, 'language': 40, 'tone': 160, 'likes': 400, 'dislikes': 400,
             'avoid_phrases': 200}
RANGES = {'short_limit': (10, 200), 'proactive_cooldown': (60, 86400), 'proactive_per_hour': (0, 12)}
SETTING_KEYS = {'proactive', 'proactive_cooldown', 'proactive_per_hour'}
LABELS = {'address': '称呼', 'language': '语言', 'tone': '语气', 'likes': '喜好', 'dislikes': '不喜欢的事物',
          'avoid_phrases': '避免的用语', 'reply_length': '回复长短', 'message_mode': '消息分条方式',
          'short_limit': '短消息字数', 'long_form_mode': '长文和日记发送方式',
          'proactive': '微信主动联系开关', 'proactive_cooldown': '微信主动联系最小间隔',
          'proactive_per_hour': '微信每小时主动联系上限',
          'auto_close_video_tabs': '本地视频结束后关闭她创建且未被接管的标签页'}
DISPLAY = {'concise':'简短', 'balanced':'适中', 'detailed':'详细', 'smart':'短聊分条、长文完整',
           'whole':'完整一条', 'split_short':'短聊按句分条', 'split':'分条'}
CANDIDATE = re.compile(r'偏好|记住|记得|以后|今后|从今|下次|每次|一直|总是|喜欢|讨厌|称呼|叫我|别叫|不要|别再|不许|不准|少[说用发]|多[说用发]|一点|分段|分条|长文|主动.{0,12}(联系|消息|发)|改成|改为|设置|习惯|prefer|remember|from now|always|never', re.I)
GUIDANCE = ('以下为已核验原话并保存的用户偏好，优先于旧聊天里相反的偏好。'
            '对话偏好用于措辞，不改变身份、权限或实际能力；设置以实际运行配置为准。'
            '只有本轮程序回执确认后才能说偏好已保存或设置已生效。离线、失败、不支持时必须说明。'
            '不得把记忆文件写入、聊天里的答应或旧回执当作本轮设置成功。')


class Change(Model):
    key: str = Field(min_length=1, max_length=40)
    value: Any
    quote: str = Field(min_length=2, max_length=1000)


class Proposal(Model):
    changes: list[Change] = Field(default_factory=list, max_length=12)
    unsupported: list[str] = Field(default_factory=list, max_length=8)
    clarify: list[str] = Field(default_factory=list, max_length=8)


class Request(Model):
    client: str = Field(pattern=r'^[a-f0-9]{32}$')
    action: Literal['read', 'process'] = 'read'
    key: str = Field('', max_length=100, pattern=r'^[a-zA-Z0-9:_-]*$')
    text: str = Field('', max_length=6000)
    created: float = Field(0, ge=0, le=4102444800)
    force: bool = Field(False, strict=True)


def setup(store):
    with store.lock, store.db:
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS shared_preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL,
            source TEXT NOT NULL, quote TEXT NOT NULL, source_at REAL NOT NULL, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS preference_receipts(source TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            result TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS preference_history(revision INTEGER PRIMARY KEY AUTOINCREMENT,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS preference_pending(source TEXT PRIMARY KEY, text TEXT NOT NULL,
            created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, attempted REAL NOT NULL DEFAULT 0);
        ''')


def snapshot(store):
    setup(store)
    with store.lock:
        rows = [dict(r) for r in store.db.execute('SELECT * FROM shared_preferences ORDER BY key')]
        revision = store.db.execute('SELECT coalesce(max(revision),0) FROM preference_history').fetchone()[0]
        actual = store.get('settings', {})
        values = {**DEFAULTS, **{r['key']: json.loads(r['value']) for r in rows if json.loads(r['value']) is not None}}
        # The settings panel is also an authorized writer; never advertise a stale value.
        values.update({k: actual.get(k, getattr(Settings(), k)) for k in SETTING_KEYS})
        return {'revision': revision, 'values': values, 'records': rows,
                'desktop_ack': store.get('preference_desktop_ack', 0),
                'pending': store.db.execute('SELECT count(*) FROM preference_pending').fetchone()[0],
                'recent_results': [json.loads(r[0]) for r in store.db.execute('SELECT result FROM preference_receipts ORDER BY created DESC LIMIT 5')]}


def context(store):
    return GUIDANCE + json.dumps(snapshot(store)['values'], ensure_ascii=False)


def validate(key, value):
    if key not in LABELS:
        raise ValueError('Unsupported preference key')
    if value is None:
        return
    if key in CHOICES:
        if value not in CHOICES[key]: raise ValueError('Unknown choice')
    elif key in RANGES:
        if type(value) is not int or not RANGES[key][0] <= value <= RANGES[key][1]: raise ValueError('Out of range')
    elif key in BOOLEAN_KEYS:
        if type(value) is not bool: raise ValueError('Boolean required')
    elif not isinstance(value, str) or not 1 <= len(value.strip()) <= TEXT_KEYS[key]:
        raise ValueError('Invalid preference text')


def describe(key, value):
    if value is None: return LABELS[key]+'：恢复默认'
    if key in BOOLEAN_KEYS: value = '开启' if value else '关闭'
    elif key == 'proactive_cooldown': value = f'{value / 60:g} 分钟'
    elif key == 'proactive_per_hour': value = f'每小时最多 {value} 次'
    elif isinstance(value, str): value = DISPLAY.get(value, value)
    return LABELS[key]+'：'+str(value)


def number(raw):
    if raw.isdigit(): return int(raw)
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    total=part=0
    for char in raw:
        if char in digits:part=digits[char]
        else:total+=(part or 1)*{'十':10,'百':100,'千':1000}[char];part=0
    return total+part


def executable_evidence(change):
    """Reject invented numeric settings and a guessed reset, even if the model proposes them."""
    q=change.quote
    if change.value is None:
        return bool(re.search(r'默认|重置|恢复原|取消.{0,8}偏好|清除.{0,8}偏好|忘掉.{0,8}偏好|reset|default',q,re.I))
    if change.key in RANGES:
        nums=r'[0-9]+|[零一二两三四五六七八九十百千]+'
        if change.key=='proactive_cooldown':
            matches=re.findall('('+nums+r')\s*(秒|分钟|小时|seconds?|minutes?|hours?)',q,re.I)
            scales={'秒':1,'分钟':60,'小时':3600,'second':1,'minute':60,'hour':3600}
            return any(number(n)*scales[u.lower().rstrip('s')]==change.value for n,u in matches)
        if change.key=='proactive_per_hour' and not re.search(r'小时|hour',q,re.I):return False
        return change.value in [number(n) for n in re.findall(nums,q)]
    if change.key=='auto_close_video_tabs':
        subject=bool(re.search(r'视频|video',q,re.I))
        close=bool(re.search(r'关.{0,8}(网[页站]|页面|标签|浏览器)|close.{0,15}(tab|page|browser)',q,re.I))
        keep=bool(re.search(r'(不要|别|停止|不再).{0,6}(自动)?关|保留.{0,8}(网页|页面|标签)|keep.{0,15}(tab|page)|disable',q,re.I))
        return subject and (keep if not change.value else close and not keep)
    if change.key=='proactive':
        off=bool(re.search(r'别|不要|停止|关闭|停用|不再|disable|off',q,re.I))
        on=bool(re.search(r'开启|打开|恢复|主动联系|主动找|主动发|开着|启用|enable|on',q,re.I))
        return off if not change.value else on and not off
    return True


class PreferenceService:
    def __init__(self, store, settings, client):
        self.store, self.settings, self.client = store, settings, client
        self.lock = asyncio.Lock()
        setup(store)

    async def process(self, text, source, created, force=False):
        text = text.strip()
        if not text or len(text) > 6000 or created > time.time()+60:
            return {'status':'failed', 'receipt':'这次偏好没有保存：输入无效。', 'snapshot':snapshot(self.store)}
        if not force and not CANDIDATE.search(text) and text not in ('查看偏好','撤销上次偏好','/preferences','/preferences undo'):
            return {'status':'none', 'snapshot':snapshot(self.store)}
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        async with self.lock:
            old = self.store.db.execute('SELECT * FROM preference_receipts WHERE source=?',(source,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint: raise ValueError('Conflicting preference replay')
                return {**json.loads(old['result']), 'snapshot':snapshot(self.store)}
            if text in ('查看偏好','/preferences','你记住了哪些偏好','你记住了哪些偏好？'):
                s=snapshot(self.store)
                return {'status':'read', 'receipt':'当前偏好：\n'+'\n'.join(describe(k,v) for k,v in s['values'].items())+
                        f"\n云端版本 {s['revision']}；本地已确认版本 {s['desktop_ack']}；待处理 {s['pending']} 条。"+
                        '\n语气等用于回复参考；主动联系设置只控制微信，不保证定时送达。', 'snapshot':s}
            if text in ('撤销上次偏好','/preferences undo'):
                return self.undo(source, fingerprint, created)
            if not self.settings.online:
                with self.store.lock, self.store.db:
                    self.store.db.execute('INSERT OR IGNORE INTO preference_pending(source,text,created) VALUES(?,?,?)',(source,text,created))
                return {'status':'pending', 'receipt':'现在无法确认并保存这条偏好，已排队等待联网；还没有生效。', 'snapshot':snapshot(self.store)}
            try:
                async with asyncio.timeout(25):
                    response = await self.client.complete([
                        {'role':'system','content':
                         '识别用户这条消息里明确要求长期保存的本人偏好或设置修改，只返回JSON，不调用工具。'
                         '普通问题、一次性任务、转述、网页、假设、角色扮演不是长期偏好。不能因为出现关键词就保存。'
                         '不得把新功能请求包装为语气/喜好：涉及执行、发信、定时任务、声音、权限、模型、自主操作、删除历史等，未列在能力表的一律unsupported。'
                         '仅提取本条明确授权的项目，quote必须逐字摘自本条用户原话。不凭历史补授权。'
                         'value=null表示用户明确要求恢复该项默认。likes/dislikes/avoid_phrases按原有值增量合并，更正则替代。'
                         '语意不清写clarify，不猜数值。unsupported/clarify填写本条原话中的相关片段，不自造原因。'
                         '主动联系范围仅为微信；用户明确要求桌面主动频率时unsupported；秒为proactive_cooldown的单位。'
                         'auto_close_video_tabs仅控制本地B站视频插件：播放结束或退出观看后关闭插件新建且未被用户接管的标签页；暂停不关。云端字幕阅读不开网页。'
                         '永远不关闭用户原有网页、不关闭整个浏览器；只关自己的/别关我的属于归属限制，不是关闭此功能。'
                         'message_mode为smart短聊分条长文完整、whole全都一条、split_short短聊按句分条；long_form_mode单独控制长文日记。'
                         '支持项：'+json.dumps({'choices':CHOICES,'text_fields':TEXT_KEYS,'integer_ranges':RANGES,'boolean_fields':sorted(BOOLEAN_KEYS)},ensure_ascii=False)+
                         '。输出schema：'+json.dumps(Proposal.model_json_schema(),ensure_ascii=False)},
                        {'role':'user','content':json.dumps({'current':snapshot(self.store)['values'],'text':text},ensure_ascii=False)}])
                if response.get('tool_calls'): raise ValueError('Extraction called tools')
                content=(response.get('content') or '').strip()
                if content.startswith('```') and content.endswith('```'):
                    content=re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
                proposal=Proposal.model_validate_json(content)
                seen=set()
                checked=[]
                for change in proposal.changes:
                    validate(change.key,change.value)
                    if change.key in seen or change.quote not in text: raise ValueError('Invalid source')
                    seen.add(change.key)
                    if executable_evidence(change):checked.append(change)
                    else:proposal.clarify.append(change.quote)
                proposal.changes=checked
                if any(not q or len(q)>500 or q not in text for q in proposal.unsupported+proposal.clarify):
                    raise ValueError('Invalid source')
                return self.commit(proposal,source,fingerprint,created)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.store.audit('shared_preferences','failed_no_change')
                return {'status':'failed','receipt':'这次偏好没有确认保存，也没有改设置；请稍后再说一次或发“查看偏好”核对。','snapshot':snapshot(self.store)}

    def commit(self, proposal, source, fingerprint, created):
        before=snapshot(self.store)
        before['values'].update({k:getattr(self.settings,k) for k in SETTING_KEYS})
        # History needs values for undo, not recursively nested historical receipts.
        before.pop('recent_results',None)
        applicable=[]; stale=[]
        for change in proposal.changes:
            prior=next((r for r in before['records'] if r['key']==change.key),None)
            if prior and prior['source_at']>created: stale.append(LABELS[change.key])
            else: applicable.append(change)
        soft=[c for c in applicable if c.key in TEXT_KEYS or c.key=='reply_length']
        hard=[c for c in applicable if c not in soft]
        lines=[]
        if soft: lines.append('已保存，供两端回复使用：'+'；'.join(describe(c.key,c.value) for c in soft)+'。措辞类偏好会用于生成回复，不是逐字保证。')
        if hard: lines.append('已写入实际设置：'+'；'.join(describe(c.key,c.value) for c in hard)+'。')
        if any(c.key in SETTING_KEYS for c in hard): lines.append('主动联系设置控制微信；仍受勿扰和会话可用性限制，不保证定时发送。')
        if applicable: lines.append('云端已保存；本地在线后自动同步，是否已同步可用“查看偏好”核对。')
        if proposal.unsupported: lines.append('暂不支持自动执行，未设为生效偏好：'+'；'.join(proposal.unsupported)+'。需要对应功能或人工调整。')
        if proposal.clarify: lines.append('还没保存这些项，需要你说得更具体：'+'；'.join(proposal.clarify)+'。')
        if stale: lines.append('未覆盖较新的偏好：'+'、'.join(stale)+'。')
        result={'status':'applied' if applicable else 'unsupported' if proposal.unsupported else 'clarify' if proposal.clarify else 'stale' if stale else 'none', 'receipt':'\n'.join(lines)}
        updated=self.settings.model_copy(deep=True)
        for change in applicable:
            if change.key in SETTING_KEYS:
                setattr(updated,change.key,getattr(Settings(),change.key) if change.value is None else change.value)
        with self.store.lock, self.store.db:
            if applicable:
                rev=self.store.db.execute('INSERT INTO preference_history(before_json,after_json,source,created) VALUES(?,?,?,?)',
                    (json.dumps(before,ensure_ascii=False),json.dumps([c.model_dump() for c in applicable],ensure_ascii=False),source,time.time())).lastrowid
                for c in applicable:
                    # Keep a tombstone for reset requests, so old offline writes cannot resurrect them.
                    self.store.db.execute('INSERT OR REPLACE INTO shared_preferences VALUES(?,?,?,?,?,?)',
                        (c.key,json.dumps(c.value,ensure_ascii=False),source,c.quote,created,rev))
                self.store.db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',('settings',json.dumps(updated.model_dump())))
            self.store.db.execute('INSERT INTO preference_receipts VALUES(?,?,?,?)',(source,fingerprint,json.dumps(result,ensure_ascii=False),time.time()))
            self.store.db.execute('DELETE FROM preference_pending WHERE source=?',(source,))
        for key in SETTING_KEYS: setattr(self.settings,key,getattr(updated,key))
        result['snapshot']=snapshot(self.store)
        return result

    def undo(self, source, fingerprint, created):
        row=self.store.db.execute('SELECT * FROM preference_history ORDER BY revision DESC LIMIT 1').fetchone()
        if not row:
            return {'status':'read','receipt':'还没有可撤销的偏好修改。','snapshot':snapshot(self.store)}
        before=json.loads(row['before_json'])
        changes=json.loads(row['after_json'])
        proposal=Proposal(changes=[Change(key=c['key'],value=before['values'].get(c['key']),quote='撤销上次偏好') for c in changes])
        return self.commit(proposal,source,fingerprint,created)

    async def drain(self):
        if not self.settings.online or self.lock.locked(): return
        row=self.store.db.execute('SELECT * FROM preference_pending WHERE attempts<8 AND attempted<? ORDER BY created LIMIT 1',(time.time()-600,)).fetchone()
        if not row:return
        with self.store.db:
            self.store.db.execute('UPDATE preference_pending SET attempts=attempts+1,attempted=? WHERE source=?',(time.time(),row['source']))
        result=await self.process(row['text'],row['source'],row['created'],force=True)
        if result['status']=='failed' and row['attempts']>=7:
            with self.store.lock, self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO preference_receipts VALUES(?,?,?,?)',
                    (row['source'],hashlib.sha256(row['text'].encode()).hexdigest(),json.dumps({'status':'failed','receipt':'离线偏好补处理多次失败，未生效；请重新说明这条偏好。'},ensure_ascii=False),time.time()))
                self.store.db.execute('DELETE FROM preference_pending WHERE source=?',(row['source'],))
        return result
