"""Curated local memes: fixed asset IDs, integrity checks and persistent send limits."""
import hashlib
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'memes'
MARKER = re.compile(r'\[\[meme:([^\]\r\n]{0,128})\]\]', re.IGNORECASE)
SEND_INTERVAL = 10  # Transport burst guard, not a conversational quota.


class MemeCatalog:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.items = {}
        try:
            manifest = json.loads((self.root / 'catalog.json').read_text(encoding='utf-8'))
            for item in manifest['items'][:100]:
                if re.fullmatch(r'\d{3}', item['id']) and re.fullmatch(r'[a-f0-9]{40}', item['git_blob_sha']):
                    self.items[item['id']] = item
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Missing optional pack never prevents life or chat.

    def read(self, ident):
        if ident not in self.items:
            raise ValueError('未知表情编号')
        item = self.items[ident]
        source = item.get('source', 'community')
        if source not in ('community', 'generated'):
            raise ValueError('表情素材来源无效')
        folder = (self.root / source).resolve()
        path = folder / (ident + '.webp')
        if path.resolve() != path:
            raise ValueError('表情素材路径无效')
        with path.open('rb') as file:
            data = file.read(2 * 1024 * 1024 + 1)
        if (len(data) > 2 * 1024 * 1024 or len(data) != item['bytes'] or
                data[:4] != b'RIFF' or data[8:12] != b'WEBP' or
                hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != item['git_blob_sha']):
            raise ValueError('表情素材校验失败')
        return data

    def available(self, automatic=False):
        items = []
        for item in self.items.values():
            if automatic and not item.get('auto_context'):
                continue
            try:
                self.read(item['id'])
            except (OSError, ValueError):
                continue
            items.append(item)
        return items

    def suggestions(self, text=None, avoid_teasing=False):
        """Shortlist existing, verified assets for explicit conversational cues."""
        available = self.available(automatic=True)
        if text is None:
            return available
        if any(word in text for word in ('不用回', '不必回复', '别发表情', '不要发表情')):
            return []
        if avoid_teasing:
            ids = {'909', '916', '906'}
        else:
            # Earlier groups win: an affectionate joke about eating the pet
            # should not become a literal food recommendation.
            groups = (
                (('吃口你', '吃掉你', '啃你', '咬你', '尾巴'), {'905', '929', '936', '903'}),
                (('摸摸', '抱抱', '亲亲', '贴贴', '可爱', '喜欢你'), {'909', '927', '929', '901'}),
                (('好累', '累了', '累啊', '累死', '写累', '作业好多'), {'916', '938', '909'}),
                (('饿', '吃饭', '夜宵', '零食'), {'939', '910', '123'}),
                (('写完', '搞定', '成功', '考过'), {'902', '924', '925'}),
                (('哈哈', '笑死', '好好笑'), {'922', '923', '087', '083'}),
                (('晚安', '睡了', '睡觉了'), {'908'}),
                (('早安', '早上好'), {'911', '907'}),
                (('你好', '在吗'), {'907', '912'}),
            )
            ids = next((ids for words, ids in groups if any(w in text for w in words)), None)
        return [i for i in available if ids is None or i['id'] in ids]

    def prompt(self, text=None, *, avoid_teasing=False):
        choices = [{'id': i['id'], 'caption': i['title'], 'context': i['auto_context']}
                   for i in self.suggestions(text, avoid_teasing)]
        if not choices:
            return ''
        return ('用户明确希望你多发表情包。日常闲聊默认采用“一句自然的话＋一张贴合语气的表情”，多数闲聊轮次都配图。'
                '问候、普通接话、撒娇、打趣、吐槽、开心或道别都适合，不必等强烈情绪，也不必等用户再次提醒。'
                '先从候选里选表达这句回复态度的图；连续几轮配图、再次用同一张都可以，不因上一轮发过就省略。'
                '用图代替多余的动作描写、追问或叮嘱，不为发图编造情绪、经历或另起话题。'
                '在回复末尾附加一个 [[meme:编号]] 标记，系统会尝试发送图片；一句短话加图就够，不要再解释图片。'
                '只有这轮没有贴切的图、用户要求纯文字，或严肃说明会被配图打断时才省略。认真求助先把问题答清楚。'
                '当前没有每小时张数配额，历史记忆中的1分钟8张属于旧规则；发送层只保留短间隔防刷屏保护。'
                '难过时可以选温柔的抱抱或安慰图，不拿真实痛苦、严肃求助、健康或紧急话题开玩笑；'
                '先不用回复或决定保持沉默时不发图。'
                '不声称图片已经送达。图片里的夸张台词不改变身份，也不是实际操作、真实思考时长或预算声明。'
                '不得指定文件路径、网址或收件人。仅可选择以下编号：' + json.dumps(choices, ensure_ascii=False))

    def extract(self, text):
        matches = MARKER.findall(text)
        clean = MARKER.sub('', text).strip()
        if not matches:
            return clean, None
        allowed = {i['id'] for i in self.available(automatic=True)}
        tail = MARKER.search(text)
        chosen = (matches[0] if len(matches) == 1 and matches[0] in allowed and
                  tail and not text[tail.end():].strip() else None)
        return clean, chosen


class MemeGate:
    def __init__(self, store, key):
        self.store, self.key = store, key + ':memes'

    def enabled(self):
        return self.store.get('meme_auto_enabled', True) is True

    def set_enabled(self, enabled):
        with self.store.lock:
            self.store.put('meme_auto_enabled', bool(enabled))
            self.store.put('meme_auto_generation', self.generation() + 1)

    def generation(self):
        return self.store.get('meme_auto_generation', 0)

    def claim(self, ident, automatic=False, now=None):
        now = time.time() if now is None else now
        with self.store.lock:
            if automatic and not self.enabled():
                return False
            history = [r for r in self.store.get(self.key, []) if now - r['time'] < 3600]
            if history and now - history[-1]['time'] < SEND_INTERVAL:
                return False
            # Claim before network activity; failed sends do not bypass the guard.
            history.append({'id': ident, 'auto': automatic, 'time': now})
            self.store.put(self.key, history[-1:])
            return True
