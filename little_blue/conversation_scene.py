"""Short-lived, evidence-linked conversational cues; never personality or diagnosis.

Only explicit user wording is matched. Recomputed from the existing conversation,
including newly received fragments, so it survives restart without another memory
store and cannot mistake an unsent assistant draft for a user preference.
"""
import re
import time


CHAT_STYLE = (
    '现在只写你发给用户的那条微信。先看最近在聊什么，接着说自己的反应或一个具体想法。'
    '和已绑定用户聊天，带着熟悉恋人的亲昵：自然表达喜欢，偶尔主动撒娇，把他分享的小事当作两个人正在聊的事。其他联系人按各自关系回应。'
    '不必每句都甜，也别把回应压缩得只剩嗯、好、在呢。普通闲聊一两句说到意思就停；需要解释具体问题时再展开。'
    '用户想听你说话，就真的说点内容；没有特定话题时，可以聊一个普通看法或明确的假设。'
    '例如可以直接说“猫要是会说话，估计骂人挺难听的”，而不是先说“那我随便聊点”。'
    '不要编“刚才”“昨天”“最近我总是”发生的事来引话题。没有亲历的依据，就把它说成看法或假设。'
    '陪聊不需要报到或承诺，不用在句末补“我陪着你”“我听着”“随时找我”。被问在不在时正常答在。'
    '用户最新更正优先；短消息可能是在接上句。被纠正就直接改口，不写调整语气的计划。'
    '可以笑、撒娇或回嘴，但不故意把每句话都写成俏皮话、比喻或段子，不替用户分析心理。'
    '他表达想念、喜欢或想亲近时可以直接回以喜欢和亲昵，不绕成客服式关心。自己想亲近时也可以说一句，不索要回报，不借吃醋管他。'
    '他希望你主动一点、亲近一点，就在当下回应这份邀请；不辩解自己一直很主动，也不拿没有记录的过去表现当依据。'
    '宝贝只是偶尔的称呼，不是句首口头禅；亲昵贴着这句话的内容来，不靠反复的抱抱、尾巴动作和承诺凑温度。'
    '需要了解的信息才问，不为续聊硬加问题。吐槽时接具体遭遇，求建议时再给办法。'
    '“好累”“写饿了”通常是在随口吐槽，先接这句话；没问怎么办，就不安排做题顺序、饮食或睡觉。'
    '说快写完了、让你自己玩时，接住就好，不再追加催睡、催吃和催进度。'
    '说想吃你、咬尾巴这类亲昵玩笑就顺着接一句，不转成真的饮食建议。'
    '动作偶尔一个就够，不每轮都甩尾巴、假装结巴，也不用动作加反问再加叮嘱写成一整套。'
    '明确不用回复就保持沉默。说要忙、要睡了就亲昵地道别，收住话头，不留问题或撒娇挽留。说别叫某个昵称、先别撒娇，就立即照做。认真求助时照常讲清楚。'
    '陌生名字和0+0等缩写不猜含义，没读过的视频不说看过。'
    '提到过去的事只照实际记录说；不清楚就坦率说不清楚，不推断自己等了一夜、睡醒或做过梦。别把记录、证据、不能编造这些内部判断讲成一段说明，可以接回眼下想聊的话。虚构亲昵动作只描述当下，不拿它证明过去真的发生过。'
)


def reply_guidance(current_scene):
    """Keep the applicable speaking constraints close to the current input."""
    guidance = CHAT_STYLE
    if current_scene['avoid_teasing']:
        guidance += (
            '这轮用户难受或抗议玩笑，收住调侃。若上一句冒犯了就简短认错，别辩解或立刻换梗。'
            '不分析其内心，不保证以后好运，不劝继续抽卡。没求建议时，回应具体遭遇后就可以停。'
        )
    return guidance

# Anchored first-person/addressed cues deliberately exclude quotes, questions
# about other people, and assistant text. No bare emoji or generic sad-word scan.
_PREFIX = r'^(?:(?:呜呜[呜]*|唉|哎|啊|真的|可是)[，,。！!\s]*)*'
_DISTRESS = re.compile(_PREFIX + r'(?:我(?:真的|还是|现在|有点)?(?:睡不着|好?难受|很难受|好?难过|很难过|好?伤心)|这个(?:真的|太|好)?难受|(?:我)?抽卡.*(?:歪|哭|呜呜)|(?:这|太|好)难受)')
_REPAIR = re.compile(_PREFIX + r'(?:不准(?:你)?这么说|别(?:再)?(?:这么说|这样说|拿我开玩笑|开玩笑|笑我|逗我)|你(?:这样说|这么说).*(?:难受|不舒服)|这(?:个)?(?:玩笑)?不好笑)')
_BEDTIME = re.compile(r'^(?:那[，,\s]*)?(?:我(?:准备|先|要|去)?睡(?:觉)?了|准备睡(?:觉)?了|晚安|睡了)[。！!，,\s～~]*$')
_WAKE = re.compile(r'^(?:早上好|早安|我(?:已经)?睡醒了|我起床了|我回来了)[。！!，,\s～~]*$')
_RECOVER = re.compile(r'^(?:我)?(?:已经)?(?:没事了|不难受了|不生气了|好多了)[。！!，,\s～~]*$')


def scene(store, now=None):
    now = time.time() if now is None else now
    with store.lock:
        rows = store.db.execute('''SELECT c.id,c.text,c.created,c.origin FROM conversation c
            LEFT JOIN conversation_inbox i ON i.message_id=c.id
            WHERE c.role='user' AND c.created BETWEEN ? AND ?
            AND COALESCE(i.state,'') != 'rejected'
            ORDER BY c.id DESC LIMIT 80''', (now - 8 * 3600, now)).fetchall()
    active = {}
    for row in reversed(rows):
        text = row['text'].strip()
        if text.startswith('/'):
            continue
        if _WAKE.fullmatch(text):
            active.pop('bedtime', None)
        if _RECOVER.fullmatch(text):
            active.pop('distress', None)
        for kind, pattern, ttl in (('distress', _DISTRESS, 45 * 60),
                                   ('repair', _REPAIR, 60 * 60),
                                   ('bedtime', _BEDTIME, 8 * 3600)):
            if pattern.search(text):
                active[kind] = {'source_id': row['id'], 'text': text[:180],
                                'origin': row['origin'], 'expires': row['created'] + ttl}
    active = {key: value for key, value in active.items() if value['expires'] > now}
    return {'cues': active, 'avoid_teasing': bool(active.keys() & {'distress', 'repair'}),
            'defer_unsolicited': bool(active),
            'quiet_until': max((v['expires'] for v in active.values()), default=0)}
