"""Curated local memes: fixed asset IDs, integrity checks and persistent send limits."""
import hashlib
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'memes'
MARKER = re.compile(
    r'\[\[(?:meme|表情)\s*[:：]\s*([^\]\r\n]{0,128})\]\]'
    r'|\[(?:meme|表情)\s*[:：]\s*([^\]\r\n]{0,128})\]'
    r'|【(?:meme|表情)\s*[:：]\s*([^】\r\n]{0,128})】', re.IGNORECASE)
UNFINISHED_MARKER = re.compile(r'(?:\[\[?|【)(?:meme|表情)\s*[:：][^\]】\r\n]*$', re.IGNORECASE)
SEND_INTERVAL = 10  # Transport burst guard, not a conversational quota.
IMAGE_LIMIT = 20 * 1024 * 1024


def image_format(data):
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


class MemeCatalog:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.items = {}
        self._verified = {}
        for name in ('catalog.json', 'nekowhale.json'):
            try:
                manifest = json.loads((self.root / name).read_text(encoding='utf-8'))
                for item in manifest['items'][:1000]:
                    if re.fullmatch(r'n?\d{3}', item['id']) and re.fullmatch(r'[a-f0-9]{40}', item['git_blob_sha']):
                        self.items[item['id']] = item
            except (OSError, ValueError, KeyError, TypeError):
                pass  # Missing optional pack never prevents life or chat.

    def read(self, ident):
        if ident not in self.items:
            raise ValueError('未知表情编号')
        item = self.items[ident]
        source = item.get('source', 'community')
        if source not in ('community', 'generated', 'nekowhale'):
            raise ValueError('表情素材来源无效')
        folder = (self.root / source).resolve()
        fmt = item.get('format', 'webp')
        if fmt not in ('webp', 'gif'):
            raise ValueError('表情素材格式无效')
        path = folder / (ident + '.' + fmt)
        if path.resolve() != path:
            raise ValueError('表情素材路径无效')
        with path.open('rb') as file:
            data = file.read(IMAGE_LIMIT + 1)
        if (len(data) > IMAGE_LIMIT or len(data) != item['bytes'] or
                image_format(data) != fmt or
                hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != item['git_blob_sha']):
            raise ValueError('表情素材校验失败')
        return data

    def available(self, automatic=False):
        items = []
        for item in self.items.values():
            if automatic and not item.get('auto_context'):
                continue
            try:
                path = self.root/item.get('source', 'community')/(item['id']+'.'+item.get('format','webp'))
                stat = path.stat()
                stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                if self._verified.get(item['id']) != stamp:
                    self.read(item['id'])
                    self._verified[item['id']] = stamp
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
        pack = [i for i in available if i.get('source') == 'nekowhale']
        if pack and not avoid_teasing:
            def score(item):
                words = [item['title'], item.get('emotion', ''), *item.get('tags', [])]
                return sum(len(w) for w in words if w and w in text)
            ranked = sorted(pack, key=score, reverse=True)
            matched = [i for i in ranked if score(i)]
            if matched:
                return matched[:12]
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
        legacy = [i for i in available if i.get('source') != 'nekowhale' and (ids is None or i['id'] in ids)]
        return legacy if not pack or avoid_teasing else (legacy[:8] + pack[:4])

    def prompt(self, text=None, *, avoid_teasing=False):
        choices = [{'id': i['id'], 'caption': i['title'], 'context': i['auto_context']}
                   for i in self.suggestions(text, avoid_teasing)]
        if not choices:
            return ''
        return ('用户最新希望表情包稍微少一些，以这个偏好为准，替代此前多数轮次配图的要求。'
                '普通接话以自然文字为主，表情包作为偶尔的点缀，不默认每句话都配图，也不刻意连续几轮配图。'
                '撒娇、打趣、开心或温柔安慰时，如果候选图比文字更能传达这句的语气，可以自然配一张；不用等用户点名。'
                '用图代替多余的动作描写、追问或叮嘱，不为发图编造情绪、经历或另起话题。'
                '在回复末尾附加一个 [[meme:编号]] 标记，系统会尝试发送图片；一句短话加图就够，不要再解释图片。'
                '只使用上述机器标记，不写“[表情:名称]”等文字占位符；旧聊天里的占位文字不是正确发送示例。'
                '图片标记不计入文字消息的30字限制，不要为了缩短文字而改写或拆开标记。'
                '用户这轮明确要一张表情或测试发图时，请选择一张已有素材；平时少配图不抵消这次明确请求。'
                '没有选择图片标记就不要说“看这张”“给你发了”或假装正在发图。'
                '文字已经表达清楚时就可以不配图，不要仅因有匹配图片就发送。用户要求纯文字或严肃说明会被配图打断时省略，认真求助先把问题答清楚。'
                '当前没有每小时张数配额，历史记忆中的1分钟8张属于旧规则；发送层只保留短间隔防刷屏保护。'
                '难过时可以选温柔的抱抱或安慰图，不拿真实痛苦、严肃求助、健康或紧急话题开玩笑；'
                '先不用回复或决定保持沉默时不发图。'
                '不声称图片已经送达。图片里的夸张台词不改变身份，也不是实际操作、真实思考时长或预算声明。'
                '不得指定文件路径、网址或收件人。仅可选择以下编号：' + json.dumps(choices, ensure_ascii=False))

    def extract(self, text):
        matches = list(MARKER.finditer(text))
        clean = UNFINISHED_MARKER.sub('', MARKER.sub('', text)).strip()
        if not matches:
            return clean, None
        # Compatibility with model-produced captions; never guess an image from
        # arbitrary descriptions, paths or URLs. Ambiguous/unknown captions fail closed.
        allowed = self.available(automatic=True)
        tail = matches[0]
        value = next(group for group in tail.groups() if group is not None).strip()
        candidates = {i['id'] for i in allowed if value in (i['id'], i['title'])}
        chosen = next(iter(candidates)) if (len(matches) == 1 and len(candidates) == 1
                                             and not text[tail.end():].strip()) else None
        return clean, chosen

    def requested(self, text):
        """Fallback for explicit image requests only, not ordinary emotional chat."""
        if '表情' not in text or any(w in text for w in ('不要', '别发', '不用', '不必', '纯文字', '只发文字', '为什么', '怎么', '如何', '解释', '示例', '举例')):
            return None
        send = bool(re.search(r'(?:发一张|发个|发一个|发张|来张|来一张|给我发|再发)', text))
        test = bool(re.search(r'表情.{0,24}(?:再试试|试一下|试试|测试一下)|(?:再试试|试一下|测试一下).{0,24}表情', text))
        if not (send or test):
            return None
        available = self.available(automatic=True)
        named = [i for i in available if i['title'] in text]
        if len(named) == 1:
            return named[0]['id']
        # A diagnostic test or a generic request can use an existing greeting.
        # Unknown specifically named images are never replaced with a guessed one.
        generic = bool(re.fullmatch(r'(?:请|麻烦)?(?:给我)?(?:发一张|发个|发一个|发张|来张|来一张)(?:表情|表情包)[吧呀。！!~～ ]*', text.strip()))
        if not named and (test or generic):
            return next((i['id'] for i in available if i['id'] == '907'), None)
        return None


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
