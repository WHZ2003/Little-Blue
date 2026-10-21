import asyncio
import json

import pytest

from little_blue.storage import Store
from little_blue.topic import TopicSummary


def seed(store):
    ids = []
    for i in range(22):
        ids.append(store.continuity.append('user' if i % 2 == 0 else 'assistant',
            '论文主题是海洋生态' if i == 0 else f'讨论进展{i}', 'desktop'))
    return ids


async def test_summary_persisted_with_evidence_and_recent_tail_untouched(tmp_path):
    path = tmp_path / 'memory.db'
    store = Store(path)
    ids = seed(store)
    store.remember('用户爱喝茶', 'semantic')
    original = store.continuity.recent()
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            calls.append(messages)
            data = json.loads(messages[-1]['content'])
            assert len(data['messages']) == 10
            assert data['messages'][-1]['id'] == ids[9]
            return {'content': json.dumps({'summary': '用户在聊海洋生态论文。', 'source_ids': [ids[0]]})}
    summary = await TopicSummary(store).refresh(Client())
    assert summary['mode'] == 'model' and summary['through_id'] == ids[9]
    assert store.continuity.recent() == original
    assert store.search('用户爱喝茶')[0]['kind'] == 'semantic'
    assert await TopicSummary(store).refresh(Client()) == summary
    assert len(calls) == 1
    store.close()
    store = Store(path)
    assert store.get('conversation_summary') == summary
    assert '海洋生态' in store.continuity.describe()
    store.close()


@pytest.mark.parametrize('output', ['not json', '{"summary":"invented","source_ids":[9999]}',
    '{"summary":"invented","source_ids":[true]}'])
async def test_invalid_summary_falls_back_without_invented_facts(tmp_path, output):
    store = Store(tmp_path / 'memory.db')
    seed(store)
    class Client:
        async def complete(self, *args):
            return {'content': output}
    result = await TopicSummary(store).refresh(Client())
    assert result['mode'] == 'extractive' and 'invented' not in result['text']
    assert len(result['text']) <= 1600
    assert all(type(i) is int for i in result['source_ids'])
    store.close()


async def test_summary_cancellation_does_not_commit(tmp_path):
    store = Store(tmp_path / 'memory.db')
    seed(store)
    class Client:
        async def complete(self, *args):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await TopicSummary(store).refresh(Client())
    assert store.get('conversation_summary') is None
    store.close()


async def test_failed_drafts_and_unclaimed_receipts_never_enter_summary(tmp_path):
    store = Store(tmp_path / 'memory.db')
    store.continuity.append('assistant', 'PRIVATE FAILED DRAFT', 'wechat', delivery='failed')
    store.continuity.append('assistant', 'PRIVATE SUPERSEDED', 'wechat', delivery='superseded')
    store.continuity.admit('test', 'inbound', 'PENDING INPUT')
    seed(store)
    class Client:
        async def complete(self, messages, schemas):
            assert 'PRIVATE' not in messages[-1]['content'] and 'PENDING INPUT' not in messages[-1]['content']
            raise TimeoutError()
    assert (await TopicSummary(store).refresh(Client()))['mode'] == 'extractive'
    store.close()


def test_receipt_atomic_dedup_states_survive_restart_without_replay(tmp_path):
    path = tmp_path / 'memory.db'
    store = Store(path)
    mid = store.continuity.admit('test', 'same', '补充一句')
    assert store.continuity.admit('test', 'same', '重复') is None
    store.continuity.inbox_state([mid], 'processing')
    store.close()
    store = Store(path)
    assert store.db.execute('SELECT state FROM conversation_inbox').fetchone()[0] == 'interrupted'
    assert [r['text'] for r in store.continuity.recent()] == ['补充一句']
    assert store.continuity.admit('test', 'same', '重复') is None
    assert not store.tasks()
    store.close()


async def test_summary_reaches_wechat_after_desktop_conversation(tmp_path):
    from little_blue.cognition import Agent
    from little_blue.models import Settings
    from little_blue.persona import SYSTEM_PERSONA
    store = Store(tmp_path / 'memory.db')
    ids = seed(store)
    store.growth.control('off')
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            calls.append(messages)
            if len(calls) == 1:
                return {'content': json.dumps({'summary': '用户的论文主题是海洋生态。', 'source_ids': [ids[0]]})}
            assert messages[0]['content'] == SYSTEM_PERSONA
            context = json.loads(messages[2]['content'].split('：', 1)[1])
            assert '海洋生态' in context['conversation_summary']['text']
            assert {'role': 'assistant', 'content': '讨论进展21'} in messages
            assert messages[-1]['content'] == '接着说论文'
            return {'content': '嗯，接着说。'}
    assert await Agent(store, Settings(online=True), None, Client()).run('接着说论文', {}, chat_only=True) == '嗯，接着说。'
    assert len(calls) == 2
    store.close()
