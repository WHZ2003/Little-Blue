"""Separate short chat replies; keep diaries and long-form replies intact."""
import re

LIMIT = 30
# Only a standalone verbal tic, not words such as 数据库 / 仓库.
TIC = re.compile(r'(^|[，,。！？!?；;\s])库[.。…~～!！]*(?=$|\s)', re.MULTILINE)


def messages(text, preferences=None):
    preferences = preferences or {}
    limit = preferences.get('short_limit', LIMIT)
    mode = preferences.get('message_mode', 'smart')
    long_mode = preferences.get('long_form_mode', 'whole')
    text = TIC.sub(lambda m: '' if not m.group(1) or m.group(1).isspace() else m.group(1), text)
    text = text.strip()
    paragraphs = [re.sub(r'\s+', ' ', line).strip(' ,，') for line in text.splitlines()]
    # Thirty characters describes a short chat bubble, not a transport limit.
    # Keep long prose (including already line-broken prose) in one message.
    long_form = (text.startswith('日记 · ') or any(len(p) > limit for p in paragraphs)
                 or sum(map(len, paragraphs)) > 2 * limit)
    if mode == 'whole' or long_form and long_mode == 'whole':
        return [text] if text else []
    if long_form or mode == 'split_short':
        parts=[]
        for paragraph in paragraphs:
            sentences = re.split(r'(?<=[。！？!?])', paragraph) if mode == 'split_short' else [paragraph]
            for sentence in sentences:
                while sentence:
                    parts.append(sentence[:limit]); sentence=sentence[limit:]
        return [part for part in parts if part.strip()]
    return [paragraph for paragraph in paragraphs if paragraph]
