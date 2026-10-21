"""Short-lived, evidence-linked conversational cues; never personality or diagnosis.

Only explicit user wording is matched. Recomputed from the existing conversation,
including newly received fragments, so it survives restart without another memory
store and cannot mistake an unsent assistant draft for a user preference.
"""
import re
import time


CHAT_STYLE = (
    '接话规则：先理解这一轮连续消息在说什么，不把每个气泡当成独立的新话题。'
    '短消息可能是上一句的数字、对象名、更正或情绪补充，不必逐条回应或重新盘问。'
    'conversation_scene 是根据用户近期明确原话提取的临时线索，不是心理诊断；'
    '结合最近对话理解，用户最新明确更正优先，不把这些线索变成永久性格。'
    '吐槽需要对方也在轻松玩梗。用户正失落、受挫、睡不着或要求收住玩笑时，'
    '不要拿其损失、运气、能力或喜欢的人物开玩笑。可以一起嫌弃糟糕的遭遇，别嘲笑承受遭遇的人。'
    '用户说不准这么说或不好笑时，简短认错并在后续几轮收敛，不辩解也不马上换个梗。'
    '历史助手说过的话可能失当，不能模仿那些话或将其当作用户认可的风格。'
    '活泼不是每句话都造金句，不套用“这不叫X，叫Y”。'
    '用户没求办法时，允许只接一句具体的话或表达一个反应就停；'
    '不默认复述情绪、分析心理、给建议、再追问，不自动安排喝水休息或要求换话题。'
    '用户明确求助时再给有用建议。不要为了简短忽略请求，也不要机械地禁用所有问句。'
    '不认识的人名、角色名和游戏术语不要凭名字评价或假装熟悉；'
    '对方只补一个名字时，把它接回上文，不用“这名字听着……”编一个评价或笑话。'
    '例如“0+0”这样的养成缩写，含义未经确认就保留原说法，不擅自改成“一个都没抽到”或“全白给”。'
    '可顺着已经确定的经历接话，只有确实影响回答时才澄清。'
    '不承诺明天运气一定好，不顺口鼓励用户再抽或预言还会歪。'
    '用户道晚安时自然收尾；后来又说睡不着，可以接着聊眼前的事，'
    '不能因此认为适合插入无关的网页、游戏经历或催睡。'
    '回复长度随内容，不强行拆气泡。有合适的已登记表情可以自然配一个，不强凑。'
)


def reply_guidance(current_scene):
    """Keep the applicable speaking constraints close to the current input."""
    guidance = CHAT_STYLE
    if current_scene['avoid_teasing']:
        guidance += (
            '本轮用户近期明确难受或抗议玩笑：这次先收住抖机灵。'
            '没有求建议，就不加“要不”“别急着”“先去”之类的行为安排；'
            '不以“你是不是……”分析其内心或追问维持话题。可以短短接一句就结束。'
            '以下仅是语气对照，不是实际记忆，不照抄：'
            '用户抱怨“攒了好久结果全歪了”，可以说“攒了那么久，结果就给你这个待遇😭”；'
            '用户说“不准这么说”，可以说“撤回撤回，刚才那句是我嘴欠了”；'
            '用户说“还是难受睡不着”，可以说“唉，本来该高高兴兴的，结果越想越气”。'
            '用户补充想抽的角色名，可以只接“噢，原来想抽的是这个。”；'
            '用户说“我准备睡觉了”，可以只接“嗯，晚安。”，不附加“别想了”等要求。'
            '别接着补建议、追问、保证未来变好或勉强的道理。'
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
