"""Bounded, local few-shot selection. Fictional examples never enter memory."""
from functools import lru_cache
import json
from pathlib import Path
import random
import re

MAX_EXAMPLES = 2
RECENT_KEY = 'dialogue_style_recent_ids'
SAFE_GROUPS = {'comfort', 'repair', 'bedtime', 'busy', 'practical', 'fragments'}
PROACTIVE_GROUPS = {'missing', 'daily', 'emotion', 'curiosity'}

# Only detachable, stock framing; never invent replacement content. A complete
# presence/comfort reply remains intact when there is nothing else to retain.
_OPENING = re.compile(
    r'^(?:(?:行{1,3}|好吧|好呀|好{1,3}|嗯)[，,。]\s*)?'
    r'(?:那我|我来)(?:就)?(?:随便)?(?:说|讲|聊)(?:一点|一件|一段|点|个)'
    r'(?:什么|别的|小事|事|没用的想法|正经的|有意思的|自己的想法)?'
    r'(?:吧|好了)?[：:，,。？?—－\s]+')
_CLOSING = re.compile(
    r'(?:[，,。！!]\s*|\n+)'
    r'(?:我(?:就|会|一直)?(?:在这(?:里|儿)?(?:陪着?你)?|陪着?你|(?:安静|慢慢)?听着?)'
    r'|你(?:想说就说|随时(?:来|找我)))[。！!～~\s]*$')


def clean_chat_reply(text):
    """Remove empty framing from short plain chat; leave substantive text alone."""
    if (len(text) > 400 or any(mark in text for mark in ('`', '“', '”', '「', '」', '"'))
            or re.search(r'(?m)^\s*(?:\d+[.)、]|[-*#>])\s*', text)):
        return text
    clean = _OPENING.sub('', text, count=1).strip()
    if not clean:
        return text
    match = _CLOSING.search(clean)
    if match and len(clean[:match.start()].strip()) >= 4:
        # Keep the sentence delimiter which belonged to the retained content.
        prefix = clean[:match.start()].rstrip()
        clean = prefix + ('。' if match.group()[0] in '，,。' else match.group()[0])
    return clean


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
            or any(phrase in text for phrase in ('不用回', '不必回复', '不想说话', '安静待', '静静待', '别说话'))):
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
    # A weaker second category can turn a request to chat into an unrelated
    # reunion/comfort example. Stay with the strongest matching scene.
    matching = [g for g in ranked_groups if scores[g['id']] > 0][:1]
    if not matching:
        fallback = 'comfort' if safe else None
        matching = [g for g in ranked_groups if g['id'] == fallback]
    grams = _grams(text)
    pool = [{**e, 'group': g['id']} for g in matching for e in g['examples']]
    # Keep twelve IDs only; no dialogue, facts or selection examples are persisted.
    available = [e for e in pool if e['id'] not in set(recent[-12:])]
    rng.shuffle(available)
    available.sort(key=lambda e: len(grams & _grams(e['user'])), reverse=True)
    # Randomize a small relevant shortlist so close variants do not become a script.
    shortlist = available[:MAX_EXAMPLES + 1]
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
        '以下是虚构的语气范例，不是历史或任务。参考接话方式，用自己的话回应实际上下文。'
        '示例不是你亲历的事情；普通话题可以借鉴，但别照抄台词。优先接着实际对话聊，用户当前要求、真实状态和沉默策略优先。\n'
        + json.dumps(examples, ensure_ascii=False))
