import json
import random

import pytest

from little_blue.cognition import Agent
from little_blue.dialogue_style import catalog, prompt, select, RECENT_KEY
from little_blue.models import Settings
from little_blue.storage import Store
from little_blue.tools import ToolRuntime


@pytest.mark.parametrize(('draft', 'expected'), [
    ('那我讲个没用的想法：我觉得云像在天上排队。', '我觉得云像在天上排队。'),
    ('那我随便说点？猫的眼神真的很嫌弃人。', '猫的眼神真的很嫌弃人。'),
    ('行，那我说点正经的：这个视频的开头太长了。', '这个视频的开头太长了。'),
    ('行行行。那我说个正经的——这个开头太长了。\n\n后面才有内容。', '这个开头太长了。\n\n后面才有内容。'),
    ('白等了那么久确实烦，我陪着你。', '白等了那么久确实烦。'),
    ('（挨着坐下）我听着。', '（挨着坐下）我听着。'),
    ('我在这里陪着你。', '我在这里陪着你。'),
    ('我听着呢，你继续。', '我听着呢，你继续。'),
    ('那我讲个现实的问题：你希望保留哪份备份？', '那我讲个现实的问题：你希望保留哪份备份？'),
    ('你说的“我陪着你”是什么意思？', '你说的“我陪着你”是什么意思？'),
    ('先按这两步：\n1. 保存\n2. 退出', '先按这两步：\n1. 保存\n2. 退出'),
    ('那我随便说点。', '那我随便说点。'),
])
def test_chat_cleanup_preserves_content_presence_quotes_and_instructions(draft, expected):
    from little_blue.dialogue_style import clean_chat_reply
    assert clean_chat_reply(draft) == expected


async def test_cleaned_reply_is_the_only_pending_chat_record(tmp_path):
    store = Store(tmp_path / 'clean.db')
    store.growth.control('off')
    class Client:
        async def complete(self, messages, tools=None):
            return {'content': '那我说点别的：猫半夜跑酷也太吵了。'}
    try:
        result = await Agent(store, Settings(online=True), None, Client()).run(
            '说点别的呀', {}, chat_only=True)
        assert result == '猫半夜跑酷也太吵了。'
        row = store.continuity.recent()[-1]
        assert row['text'] == result and row['delivery'] == 'pending'
    finally:
        store.close()


def test_library_has_curated_short_replies_and_stable_ids():
    groups = catalog()
    examples = [e for g in groups for e in g['examples']]
    assert len(groups) == 21 and len(examples) == 78
    assert len({e['id'] for e in examples}) == len(examples)
    assert len({e['reply'] for e in examples}) == len(examples)
    assert all('[[meme:' not in e['reply'] and '[[no_reply]]' not in e['reply'] for e in examples)


@pytest.mark.parametrize('text', ['想我了没', '想发表情就发', '帮我解释代码', '我去睡了', '你好', '别的鱼'])
def test_selection_is_bounded_and_does_not_repeat_recent_ids(text):
    first = select(text, {}, rng=random.Random(7))
    second = select(text, {}, [e['id'] for e in first], rng=random.Random(7))
    assert 0 < len(first) <= 2 and 0 < len(second) <= 2
    assert not {e['id'] for e in first} & {e['id'] for e in second}


def test_distress_never_uses_teasing_jealousy_or_affection_fixtures():
    for n in range(20):
        chosen = select('我抽卡歪飞了，想找别的鱼玩', {'avoid_teasing': True}, rng=random.Random(n))
        assert chosen and all(e['group'] in {'comfort', 'repair', 'bedtime', 'busy', 'practical', 'fragments'} for e in chosen)
    assert all(e['group'] != 'jealousy' for e in select('你好', {}, previous_user='今天去看别的鱼了'))
    assert all(e['group'] not in {'affection', 'jealousy', 'emotion'}
               for e in select('别撒娇，帮我解释这个', {}))


def test_fragments_use_adjacent_user_context_and_proactive_respects_quiet():
    assert all(e['group'] == 'study' for e in select('三道', {}, previous_user='今天还有好多作业'))
    assert select('主动聊一下', {'defer_unsolicited': True}, proactive=True) == []
    assert select('这条不用回复哦', {}) == []
    assert all(e['group'] in {'missing', 'daily', 'emotion', 'curiosity'}
               for e in select('想起今天的事', {}, proactive=True))


def test_unmatched_input_does_not_inject_an_unrelated_daily_story():
    assert select('嗯，收到', {}) == []
    assert select('这次延迟是 320ms', {}) == []
    assert select('主动聊一下', {}, proactive=True) == []


def test_request_for_closeness_selects_affection_but_respects_boundaries():
    for text in ('你可以主动一点嘛', '想跟你亲近一点'):
        chosen = select(text, {}, rng=random.Random(7))
        assert chosen and all(e['group'] == 'affection' for e in chosen)
    assert all(e['group'] != 'affection' for e in select('别撒娇，主动一点解释代码', {}))


def test_company_invitation_has_relevant_examples_and_respects_quiet():
    for text in ('没什么，就是想听你说话', '你说点什么都行', '我们聊会儿'):
        selected = select(text, {}, rng=random.Random(7))
        assert selected and all(e['group'] == 'company' for e in selected)
    assert select('不用回复，陪我待会儿', {}) == []
    assert select('陪我待会儿，不想说话', {}) == []
    assert all(e['group'] != 'company' for e in select('我好难受，陪我待会儿', {'avoid_teasing': True}))
    for seed in range(20):
        chosen = select('别一直说陪着我了，你倒是说点别的啊', {}, rng=random.Random(seed))
        assert chosen and all(e['group'] == 'company' for e in chosen)
        chosen = select('哈哈哈你怎么这么可爱', {}, rng=random.Random(seed))
        assert chosen and all(e['group'] == 'affection' for e in chosen)


def test_prompt_only_persists_ids_and_cannot_claim_examples_are_memories(tmp_path):
    store = Store(tmp_path / 'style.db')
    try:
        text = prompt(store, '想我了吗', {})
        assert '虚构' in text and '不是历史' in text and '沉默策略优先' in text
        assert len(text) < 2000
        assert len(json.loads(text.split('\n', 1)[1])) <= 3
        assert store.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
        assert store.db.execute('SELECT count(*) FROM conversation').fetchone()[0] == 0
        ids = store.get(RECENT_KEY)
        assert all(isinstance(i, str) and '.' in i for i in ids)
    finally:
        store.close()
    reopened = Store(tmp_path / 'style.db')
    try:
        assert reopened.get(RECENT_KEY) == ids
    finally:
        reopened.close()


@pytest.mark.parametrize('reflection', [False, True])
async def test_agent_injects_style_in_chat_but_not_reflection(tmp_path, reflection):
    store = Store(tmp_path / 'agent.db')
    settings = Settings(online=True, tools_enabled=False)
    store.growth.control('off')
    calls = []
    class Client:
        async def complete(self, messages, tools=None):
            calls.append(messages)
            return {'content': '嗯，今天慢慢聊。'}
    async def approve(*args):
        raise AssertionError('No tool use')
    try:
        agent = Agent(store, settings, ToolRuntime(store, settings, approve), Client())
        await agent.run('想我了吗', {}, reflection=reflection, chat_only=not reflection)
        references = [m for m in calls[0] if m['content'].startswith('以下是虚构的语气范例')]
        assert len(references) == (0 if reflection else 1)
        assert all('以下是虚构的语气范例' not in r['text'] for r in store.search('', 30))
    finally:
        store.close()
