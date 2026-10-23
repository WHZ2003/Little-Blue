"""Short, single-line messages for the paired Coopanion deployment."""
import re

LIMIT = 30
# Only a standalone verbal tic, not words such as 数据库 / 仓库.
TIC = re.compile(r'(^|[，,。！？!?；;\s])库[.。…~～!！]*(?=$|\s)', re.MULTILINE)


def messages(text):
    text = TIC.sub(lambda m: '' if not m.group(1) or m.group(1).isspace() else m.group(1), text)
    out = []
    for paragraph in text.splitlines():
        paragraph = re.sub(r'\s+', ' ', paragraph).strip(' ,，')
        while paragraph:
            end = min(LIMIT, len(paragraph))
            if len(paragraph) > LIMIT:
                # Prefer sentence/word boundaries without discarding the remainder.
                for pos in range(end - 1, max(0, end // 2 - 1), -1):
                    if paragraph[pos] in '。！？!?；;，,、 ':
                        end = pos + 1
                        break
            part, paragraph = paragraph[:end].strip(), paragraph[end:].strip()
            if part:
                out.append(part)
    return out
