"""Bounded, local few-shot selection. Fictional examples never enter memory."""
from functools import lru_cache
import json
from pathlib import Path
import random
import re

MAX_EXAMPLES = 3
RECENT_KEY = 'dialogue_style_recent_ids'
SAFE_GROUPS = {'comfort', 'repair', 'bedtime', 'busy', 'practical', 'fragments'}
PROACTIVE_GROUPS = {'missing', 'daily', 'emotion', 'curiosity'}


@lru_cache(maxsize=1)
def catalog():
    try:
        data = json.loads(Path(__file__).with_name('dialogue_examples.json').read_text(encoding='utf-8'))
        groups, ids = [], set()
        for group in data['groups']:
            key = group['id']
            if not re.fullmatch('[a-z_]+', key) or key in ids:
                raise ValueError('Invalid example group')
            ids.add(key)
            examples = []
            for n, pair in enumerate(group['examples'], 1):
                if len(pair) != 2 or not all(isinstance(t, str) and 0 < len(t) <= 180 for t in pair):
                    raise ValueError('Invalid example')
                examples.append({'id': f'{key}.{n:02}', 'user': pair[0], 'reply': pair[1]})
            keywords = group['keywords']
            if not keywords or not all(isinstance(k, str) and k for k in keywords):
                raise ValueError('Invalid example keywords')
            groups.append({'id': key, 'keywords': keywords, 'examples': examples})
        return tuple(groups)
    except (OSError, ValueError, KeyError, TypeError):
        return ()  # Optional style reference must never stop a conversation.


def _grams(text):
    text = re.sub(r'\s+', '', text.casefold())
    return {text[i:i+2] for i in range(max(0, len(text)-1))}


def select(text, current_scene, recent=(), previous_user='', proactive=False, rng=None):
    rng = rng or random.SystemRandom()
    text = str(text)[-2000:]
    groups = catalog()
    if (not groups or proactive and current_scene.get('defer_unsolicited')
            or any(phrase in text for phrase in ('不用回', '不必回复'))):
        return []
    safe = current_scene.get('avoid_teasing') or any(
        word in text for word in ('难受', '难过', '想哭', '不好笑', '不准', '别打扰', '别逗', '不要开玩笑'))
    scores = {g['id']: sum(len(k) for k in g['keywords'] if k.casefold() in text.casefold()) for g in groups}
    # Only use adjacent user context to resolve short additions, never old assistant prose.
    if len(text) < 10 and not any(scores.values()) and not proactive:
        scores = {g['id']: (0 if g['id'] in {'daily', 'fragments'} else
                            sum(len(k) for k in g['keywords'] if k in previous_user[-400:])) for g in groups}
    allowed = SAFE_GROUPS if safe else {g['id'] for g in groups}
    if proactive:
        allowed &= PROACTIVE_GROUPS
    if re.search(r'(?:别|不要|不想|不喜欢).{0,8}(?:撒娇|吃醋|黏|暧昧)', text):
        allowed -= {'affection', 'jealousy', 'emotion'}
    # Do not infer jealousy from earlier topics or use it as generic intimacy.
    if not any(k in text for k in ('吃醋', '别的鱼', '别的桌宠', '争宠', '偏心')):
        allowed = allowed - {'jealousy'}
    ranked_groups = sorted((g for g in groups if g['id'] in allowed),
                           key=lambda g: scores[g['id']], reverse=True)
    matching = [g for g in ranked_groups if scores[g['id']] > 0][:2]
    if not matching:
        fallback = 'comfort' if safe else 'emotion' if proactive else 'daily'
        matching = [g for g in ranked_groups if g['id'] == fallback]
    grams = _grams(text)
    pool = [{**e, 'group': g['id']} for g in matching for e in g['examples']]
    # Keep twelve IDs only; no dialogue, facts or selection examples are persisted.
    available = [e for e in pool if e['id'] not in set(recent[-12:])]
    rng.shuffle(available)
    available.sort(key=lambda e: len(grams & _grams(e['user'])), reverse=True)
    # Randomize a small relevant shortlist so close variants do not become a script.
    shortlist = available[:max(MAX_EXAMPLES, min(9, len(available)))]
    return rng.sample(shortlist, min(MAX_EXAMPLES, len(shortlist)))


def prompt(store, text, current_scene, previous_user='', proactive=False):
    recent = store.get(RECENT_KEY, [])
    if not isinstance(recent, list):
        recent = []
    selected = select(text, current_scene, recent, previous_user, proactive)
    if not selected:
        return ''
    store.put(RECENT_KEY, (recent + [e['id'] for e in selected])[-12:])
    examples = [{'用户': e['user'], '她': e['reply']} for e in selected]
    return (
        '以下是虚构的语气范例，只参考接话的松弛感、亲近程度和句子节奏，不是历史、事实或任务。'
        '先回应用户实际内容，使用自己的措辞；不要照抄例句、复述例子的事情或硬塞示例话题。'
        '示例中的情绪不是你此刻必须有的情绪，吃醋和撒娇也不是每轮任务。'
        '用户当前要求、真实状态、工具结果和沉默策略优先；可以普通接话或保持沉默。'
        '这些例子不代表看过网页、玩过游戏、设置过提醒或发过图片。表情只从实际可用目录选择。'
        '主动交流时参考语气即可，不假装用户刚说过范例中的话。\n'
        + json.dumps(examples, ensure_ascii=False))
