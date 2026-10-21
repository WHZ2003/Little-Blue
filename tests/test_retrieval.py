import asyncio
import struct
import pytest
from little_blue.storage import Store
from little_blue.vector_index import VectorIndex, chunks, fuse
from little_blue.retrieval import MemoryRetrieval


class Encoder:
    def embed(self, texts, **kwargs):
        for text in texts:
            yield [1, 0, 0] if 'tea' in text or '饮料' in text else [0, 1, 0]

    def query_embed(self, text):
        return self.embed([text])


def test_legacy_backfill_persistence_semantic_match_and_model_isolation(tmp_path):
    path = tmp_path / 'memory.db'
    store = Store(path)
    wanted = store.remember('I prefer tea', 'semantic')
    store.remember('I like walking')
    assert store.search('饮料') == []
    index = VectorIndex(path, Encoder(), 'v1', dim=3)
    assert index.backfill()['indexed'] == 2
    assert index.search('饮料')[0]['id'] == wanted
    index.close()
    index = VectorIndex(path, Encoder(), 'v1', dim=3)
    assert index.status()['indexed'] == 2
    index.close()
    other = VectorIndex(path, Encoder(), 'v2', dim=3)
    assert other.status()['indexed'] == 0
    assert not other.search('饮料')
    other.close()
    store.close()


def test_edits_deletes_invalidate_vectors_and_fts(tmp_path):
    store = Store(tmp_path / 'memory.db')
    ident = store.remember('tea')
    index = VectorIndex(store.path, Encoder(), 'v1', dim=3)
    index.backfill()
    with store.db:
        store.db.execute('UPDATE memories SET text=? WHERE id=?', ('walking', ident))
    assert not store.search('tea')
    assert not index.search('tea')
    assert index.status()['pending'] == 1
    index.backfill()
    assert index.search('walking')[0]['id'] == ident
    with store.db:
        store.db.execute('DELETE FROM memories WHERE id=?', (ident,))
    assert not index.search('walking')
    assert not store.search('walking')
    assert store.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0] == 0
    index.close()
    store.close()


def test_no_stale_vector_commit_if_source_changes_during_inference(tmp_path):
    store = Store(tmp_path / 'memory.db')
    ident = store.remember('tea')
    class RacingEncoder(Encoder):
        def embed(self, texts, **kwargs):
            with store.db:
                store.db.execute('UPDATE memories SET text=? WHERE id=?', ('changed', ident))
            return super().embed(texts, **kwargs)
    index = VectorIndex(store.path, RacingEncoder(), 'v1', dim=3)
    assert index.backfill()['indexed'] == 0
    assert not index.search('tea')
    index.close()
    store.close()


def test_chunk_tail_and_duplicate_memory_fusion(tmp_path):
    store = Store(tmp_path / 'memory.db')
    ident = store.remember('走' * 2000 + 'tea')
    assert chunks('走' * 2000 + 'tea')[-1].endswith('tea')
    index = VectorIndex(store.path, Encoder(), 'v1', dim=3)
    index.backfill()
    assert index.search('饮料') == [{'id': ident, 'similarity': 1.0}]
    rows = fuse([{'id':1}, {'id':2}], [{'id':2}, {'id':3}, {'id':3}], 3)
    assert rows[0]['id'] == 2
    assert rows[0]['retrieval']['via'] == ['keyword', 'vector']
    assert len(rows) == 3
    index.close()
    store.close()


@pytest.mark.parametrize('value', [[0,0,0], [float('nan'),0,1], [1,2]])
def test_invalid_embeddings_do_not_mark_indexed(tmp_path, value):
    store = Store(tmp_path / 'memory.db')
    store.remember('tea')
    class Bad(Encoder):
        def embed(self, texts, **kwargs):
            return [value for _ in texts]
    index = VectorIndex(store.path, Bad(), 'v1', dim=3)
    with pytest.raises(ValueError):
        index.backfill()
    assert index.status()['pending'] == 1
    index.close()
    store.close()


def test_corrupt_vector_is_skipped(tmp_path):
    store = Store(tmp_path / 'memory.db')
    store.remember('tea')
    index = VectorIndex(store.path, Encoder(), 'v1', dim=3)
    index.backfill()
    for blob in (b'bad', struct.pack('<fff', float('nan'), 0, 1)):
        with store.db:
            store.db.execute('UPDATE memory_vectors SET vector=?', (blob,))
        assert not index.search('tea')
    index.close()
    store.close()


async def test_missing_model_falls_back_and_controls_preserve_originals(tmp_path):
    store = Store(tmp_path / 'memory.db')
    ident = store.remember('tea')
    r = MemoryRetrieval(store)
    store.retriever = r
    await r._request({'op':'index'})
    assert r.error == 'FileNotFoundError'
    assert (await store.search_hybrid('tea'))[0]['id'] == ident
    await r.control('off')
    assert not r.enabled
    await r.control('rebuild')
    assert store.search('tea')[0]['id'] == ident
    assert not r.enabled
    await r.close()
    store.close()


async def test_hybrid_lookup_deduplicates_resolves_current_rows_and_drops_deleted(tmp_path, monkeypatch):
    store = Store(tmp_path / 'memory.db')
    ident = store.remember('tea')
    other = store.remember('walking')
    r = MemoryRetrieval(store)
    r.ready = True
    async def fake(payload):
        return {'hits':[{'id':other,'similarity':.9}, {'id':ident,'similarity':.8}, {'id':999,'similarity':.7}]}
    monkeypatch.setattr(r, '_request', fake)
    result = await r.search('tea')
    assert result[0]['id'] == ident
    assert {x['id'] for x in result} == {ident, other}
    assert result[0]['retrieval']['via'] == ['keyword', 'vector']
    store.close()


async def test_cancel_kills_worker_and_propagates(tmp_path):
    store = Store(tmp_path / 'memory.db')
    r = MemoryRetrieval(store)
    class Pipe:
        def write(self, data): pass
        async def drain(self): pass
        async def readline(self): await asyncio.Event().wait()
    class Process:
        returncode = None
        stdin = stdout = Pipe()
        def kill(self): self.returncode = -9
        async def wait(self): return self.returncode
    process = r.process = Process()
    task = asyncio.create_task(r._request({'op':'search','query':'tea','limit':3}))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert process.returncode == -9 and r.process is None
    store.close()
