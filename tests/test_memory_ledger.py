import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from little_blue.storage import Store
from little_blue.memory_ledger import Facts, FactLearner
from little_blue.models import Settings
from little_blue.retrieval import MemoryRetrieval
from little_blue.vector_index import VectorIndex


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'memory.db')
    yield s
    s.close()


def revise(s, text, **kwargs):
    return s.ledger.control(dict(action='revise', revision=s.ledger.state()['revision'],
        key='user.概率课作业1.状态', text=text, reason='本人更正了进展', **kwargs))


def proposal(mid, quote, key='user.概率课作业1.状态'):
    return dict(key=key, text=quote, quote=quote, source_id=mid, stable=False,
                reason='用户明确报告了这项作业的新进展', confidence=.95)


def prepare(s, text, now=None):
    mid = s.continuity.append('user', text, 'desktop')
    ticket, sources = s.ledger.prepare(now or time.time())
    return mid, ticket, sources


def test_migrate_old_rows_vectors_persona_unchanged_and_start_from_new(tmp_path):
    path = tmp_path / 'old.db'
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE memories(id INTEGER PRIMARY KEY,kind TEXT,text TEXT,created TEXT);"
                     "INSERT INTO memories VALUES(1,'semantic','喜欢茶','2020-01-01T00:00:00+00:00');")
    db.close()
    s = Store(path)
    original = tuple(s.db.execute('SELECT * FROM memories').fetchone())
    profile = s.growth.snapshot()['profile']
    old = s.continuity.append('user', '我喜欢咖啡', 'desktop')
    s.close()
    # Simulate migration on an archive already containing conversation history.
    db = sqlite3.connect(path)
    db.execute('DELETE FROM memory_controls')
    db.commit()
    db.close()
    s = Store(path)
    assert tuple(s.db.execute('SELECT * FROM memories').fetchone()) == original
    assert s.ledger.state()['cursor'] == old
    assert s.ledger.prepare(time.time()) is None
    assert s.growth.snapshot()['profile'] == profile
    s.close()


def test_replace_and_repeated_undo_preserve_all_versions(store):
    legacy = store.remember('作业没写完', 'semantic')
    original = dict(store.db.execute('SELECT * FROM memories WHERE id=?', (legacy,)).fetchone())
    first = revise(store, '作业写完了', memory_id=legacy)
    second_id = first['rows'][0]['id']
    second = revise(store, '作业交了')
    newest = second['rows'][0]['id']
    assert [r['id'] for r in store.search('作业')] == [newest]
    assert store.semantic() == []
    store.ledger.control(dict(action='undo', revision=second['revision']))
    assert [r['id'] for r in store.search('作业')] == [second_id]
    store.ledger.control(dict(action='undo', revision=store.ledger.state()['revision']))
    assert [r['id'] for r in store.search('作业')] == [legacy]
    assert store.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 3
    assert dict(store.db.execute('SELECT * FROM memories WHERE id=?', (legacy,)).fetchone()) == original
    assert all(r['undone'] for r in store.db.execute('SELECT * FROM memory_changes'))


def test_stale_edit_and_different_key_cannot_clobber(store):
    state = revise(store, '作业没写完')
    with pytest.raises(ValueError):
        store.ledger.control(dict(action='undo', revision=0))
    with pytest.raises(ValueError):
        store.ledger.control(dict(action='revise', revision=state['revision'], memory_id=state['rows'][0]['id'],
                                 key='user.另一份作业.状态', text='完成', reason='不同事项'))
    assert store.ledger.state()['revision'] == state['revision']


def test_decay_changes_ranking_not_relevance_and_stable_does_not_decay(store):
    old = store.remember('茶 旧经历')
    stable = store.remember('茶 长期偏好', 'semantic')
    fresh = store.remember('茶 新经历')
    with store.db:
        store.db.execute("UPDATE memories SET created='2020-01-01T00:00:00+00:00' WHERE id IN (?,?)", (old, stable))
    rows = store.search_candidates('茶', 20)
    # Hold base relevance equal to verify only the age factor.
    rows = [{**r, 'retrieval': {'via': ['keyword','vector'], 'score': .02}} for r in rows]
    ranked = store.ledger.rank(rows, '茶', 3)
    details = {r['id']:r['retrieval'] for r in ranked}
    assert details[old]['freshness'] < .1
    assert details[stable]['freshness'] == 1
    assert details[fresh]['score'] > details[old]['score']
    assert len(ranked) == 3  # Even very old but relevant evidence is retained.
    assert all(r['retrieval']['selected'] for r in ranked)
    store.ledger.control(dict(action='decay_off', revision=0))
    assert all(r['retrieval']['freshness'] == 1 for r in store.ledger.rank(rows, '茶', 3))
    assert store.get('memory_last_recall')['candidates'][0]['via'] == ['keyword','vector']


def test_automatic_evidence_and_no_persona_growth_side_effect_after_restart(store):
    mid, ticket, sources = prepare(store, '我的概率课作业1写完了')
    assert store.ledger.apply(Facts(facts=[proposal(mid, sources[0]['text'])]), ticket, sources)
    row = store.ledger.snapshot()['rows'][0]
    assert row['change']['evidence'][0]['quote'] == sources[0]['text']
    assert store.db.execute('SELECT count(*) FROM growth_sources').fetchone()[0] == 0
    # Growth's startup import must not promote extracted facts into persona instructions.
    from little_blue.growth import GrowthMemory
    GrowthMemory(store)
    assert store.db.execute('SELECT count(*) FROM growth_sources').fetchone()[0] == 0


@pytest.mark.parametrize('bad', ['quote', 'assistant', 'revoked', 'rejected', 'generic', 'duplicate'])
def test_invalid_model_proposals_are_atomic(store, bad):
    mid, ticket, sources = prepare(store, '我的概率课作业1写完了')
    facts = [proposal(mid, sources[0]['text'])]
    if bad == 'quote':
        facts.append(proposal(mid, '完全没说过', 'user.茶.偏好'))
    elif bad == 'assistant':
        with store.db:
            store.db.execute("UPDATE conversation SET role='assistant' WHERE id=?", (mid,))
    elif bad == 'revoked':
        store.ledger.control(dict(action='auto_off', revision=0))
    elif bad == 'rejected':
        store.continuity.inbox_state([mid], 'rejected')
    elif bad == 'generic':
        facts[0]['key'] = 'user.内容'
    elif bad == 'duplicate':
        facts.append(facts[0])
    if bad == 'revoked':
        assert not store.ledger.apply(Facts(facts=facts), ticket, sources)
    else:
        with pytest.raises(ValueError):
            store.ledger.apply(Facts(facts=facts), ticket, sources)
    assert not store.ledger.active_facts()
    assert store.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 0


def test_undo_blocks_same_evidence_but_accepts_new_confirmation(store):
    mid, ticket, sources = prepare(store, '我的概率课作业1写完了')
    store.ledger.apply(Facts(facts=[proposal(mid, sources[0]['text'])]), ticket, sources)
    store.ledger.control(dict(action='undo', revision=1))
    # Even if old input is offered again, persistent evidence cutoff rejects it.
    replay = {**store.ledger.state(), 'cursor': 0}
    with store.db:
        store.db.execute('UPDATE memory_controls SET cursor=0 WHERE id=1')
    with pytest.raises(ValueError):
        store.ledger.apply(Facts(facts=[proposal(mid, sources[0]['text'])]), replay, sources)
    with store.db:
        store.db.execute('UPDATE memory_controls SET cursor=? WHERE id=1', (mid,))
    later, ticket, sources = prepare(store, '我确认概率课作业1交了', time.time()+601)
    assert store.ledger.apply(Facts(facts=[proposal(later, sources[0]['text'])]), ticket, sources)
    assert len(store.ledger.active_facts()) == 1


async def test_vector_and_keyword_both_exclude_superseded_and_undo_without_reindex(store):
    class Encoder:
        def embed(self, texts, **kwargs):
            return iter([[1,0] for _ in texts])
        def query_embed(self, text):
            return iter([[1,0]])
    first = revise(store, 'tea old')['rows'][0]['id']
    newest = revise(store, 'tea new')['rows'][0]['id']
    index = VectorIndex(store.path, Encoder(), 'test', dim=2)
    index.backfill()
    vector_count = store.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0]
    r = MemoryRetrieval(store)
    r.ready = True
    async def request(payload):
        return {'hits': index.search(payload['query'])}
    r._request = request
    assert [x['id'] for x in await r.search('tea')] == [newest]
    trace = store.get('memory_last_recall')
    assert any(x['id']==first and x['reason']=='已被新事实替代' for x in trace['candidates'])
    store.ledger.control(dict(action='undo', revision=2))
    assert [x['id'] for x in await r.search('tea')] == [first]
    assert store.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0] == vector_count
    r.ready = False
    assert [x['id'] for x in await r.search('tea')] == [first]
    index.close()


async def test_background_learner_cooldown_and_offline(store):
    class Client:
        calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            assert tools is None
            return {'content': '{"facts":[]}'}
    client = Client()
    settings = Settings(online=False)
    learner = FactLearner(store, settings, client)
    store.continuity.append('user', '今天聊天很开心', 'desktop')
    await learner.once()
    assert client.calls == 0
    settings.online = True
    await learner.once()
    await learner.once()
    assert client.calls == 1
    assert store.ledger.state()['cursor'] == 1


async def test_cancel_learner_preserves_source_cursor_and_facts(store):
    started = asyncio.Event()
    class Client:
        async def complete(self, messages, tools):
            started.set()
            await asyncio.Event().wait()
    learner = FactLearner(store, Settings(online=True), Client())
    store.continuity.append('user', '我喜欢茶', 'desktop')
    task = asyncio.create_task(learner.once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.ledger.state()['cursor'] == 0
    assert not store.ledger.active_facts()


@pytest.mark.parametrize('failure', ['timeout', 'invalid_json', 'tools'])
async def test_failed_extraction_does_not_write_facts(store, failure):
    class Client:
        async def complete(self, messages, tools):
            if failure == 'timeout':
                raise TimeoutError()
            return {'content': 'invalid', 'tool_calls': ['not allowed'] if failure == 'tools' else []}
    learner = FactLearner(store, Settings(online=True), Client())
    store.continuity.append('user', '我喜欢茶', 'desktop')
    await learner.once()
    assert store.ledger.state()['cursor'] == 0
    assert not store.ledger.active_facts()


def test_controls_and_undo_survive_reopen(tmp_path):
    path = tmp_path/'persistent.db'
    s = Store(path)
    revise(s, '偏好茶', stable=True)
    s.ledger.control(dict(action='auto_off', revision=1))
    s.close()
    s = Store(path)
    assert not s.ledger.state()['automatic']
    assert s.ledger.snapshot()['can_undo']
    assert s.ledger.snapshot()['rows'][0]['memory']['stable']
    s.ledger.control(dict(action='undo', revision=2))
    assert not s.ledger.active_facts()
    assert s.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 1
    s.close()
