"""Real offline embedding integration check using only synthetic disposable memories."""
import asyncio
import gc
import json
from pathlib import Path
import tempfile
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from little_blue.storage import Store
from little_blue.retrieval import MemoryRetrieval


async def main():
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / 'memory.sqlite3')
        tea = store.remember('用户喜欢蓝莓茶，饮品不要加糖。', 'semantic')
        homework = store.remember('昨晚用户一直在写数学作业，抱怨题目太多。')
        store.remember('鲸鱼娘在虚拟水族箱里喂了两条金鱼。')
        store.remember('用户希望聊天少追问，多一点轻松的玩笑。', 'semantic')
        r = MemoryRetrieval(store)
        store.retriever = r
        await r.start()
        ticks = 0
        try:
            async with asyncio.timeout(45):
                while not r.ready or r.status()['pending']:
                    await asyncio.sleep(.02)
                    ticks += 1
            assert ticks > 0
            for query, target in [('我喜欢喝哪种饮料？', tea), ('昨天功课是不是很多？', homework)]:
                assert not store.search(query), 'Synthetic query unexpectedly matches literally'
                started = time.monotonic()
                rows = await store.search_hybrid(query, 3)
                assert rows and rows[0]['id'] == target, (query, r.status(), [(x['id'],x.get('similarity')) for x in rows])
                assert 'vector' in rows[0]['retrieval']['via']
                print(json.dumps({'query':query,'top_memory_id':rows[0]['id'],
                    'similarity':rows[0]['similarity'],'seconds':round(time.monotonic()-started,3)},ensure_ascii=False), flush=True)
            await r.control('off')
            assert not await store.search_hybrid('我喜欢喝哪种饮料？')
            assert store.search('蓝莓')
            await r.control('rebuild')
            assert store.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 4
            assert r.status()['indexed'] == 0
            await r.control('on')
            async with asyncio.timeout(45):
                while not r.ready or r.status()['pending']:
                    await asyncio.sleep(.02)
            print(json.dumps({'status':r.status(),'event_loop_ticks_during_initial_index':ticks}),flush=True)
        finally:
            await r.close()
            store.close()
            # Release deferred SQLite statements before Windows removes the
            # temporary database; a closed connection can still have cursors.
            gc.collect()


if __name__ == '__main__':
    asyncio.run(main())
