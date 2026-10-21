"""Async hybrid retrieval with a cancellable, locally isolated embedding worker."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from .vector_index import fuse


class MemoryRetrieval:
    def __init__(self, store):
        self.store = store
        default = Path(__file__).resolve().parents[1] / 'models' / 'bge-small-zh-v1.5'
        self.folder = Path(os.getenv('LITTLE_BLUE_EMBEDDING_MODEL_DIR', str(default)))
        self.process = None
        self.lock = asyncio.Lock()
        self.task = None
        self.ready = False
        self.error = None
        self.model = store.get('vector_memory_model')
        self.retry_at = 0

    @property
    def enabled(self):
        return self.store.get('vector_memory_enabled', True) is True

    def status(self):
        with self.store.lock:
            total = self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0]
            indexed = self.store.db.execute('SELECT count(*) FROM memory_vector_docs WHERE model=?',
                                            (self.model,)).fetchone()[0] if self.model else 0
        return {'enabled': self.enabled, 'ready': self.ready, 'model': 'BAAI/bge-small-zh-v1.5',
                'total': total, 'indexed': indexed, 'pending': max(0, total - indexed), 'error': self.error}

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._maintain())

    async def _kill(self):
        p, self.process = self.process, None
        self.ready = False
        if p is not None:
            if p.returncode is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
            await p.wait()

    async def _request(self, payload):
        async with self.lock:
            if not self.enabled:
                return {}
            try:
                async with asyncio.timeout(15 if self.process is None else 8):
                    if self.process is None:
                        if not (self.folder / 'manifest.json').is_file() or getattr(sys, 'frozen', False):
                            raise FileNotFoundError('Local embedding model unavailable')
                        # Do not pass API secrets to the model process.
                        env = {k: v for k, v in os.environ.items() if k in
                               ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL')}
                        env.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                        kwargs = {'creationflags': 0x08000000} if os.name == 'nt' else {}
                        self.process = await asyncio.create_subprocess_exec(sys.executable, '-m',
                            'little_blue.vector_worker', self.store.path, str(self.folder),
                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL, env=env, **kwargs)
                        hello = json.loads(await self.process.stdout.readline())
                        if not hello.get('ready'):
                            raise RuntimeError('Worker not ready')
                        self.model, self.ready = hello['model'], True
                        self.store.put('vector_memory_model', self.model)
                    self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode())
                    await self.process.stdin.drain()
                    result = json.loads(await self.process.stdout.readline())
                    if 'error' in result:
                        raise RuntimeError('Embedding failed')
                    self.error = None
                    return result
            except asyncio.CancelledError:
                await self._kill()
                raise
            except Exception as error:
                self.error = type(error).__name__
                self.retry_at = time.monotonic() + 60
                self.store.audit('memory_vector', 'fallback_' + self.error)
                await self._kill()
                return {}

    async def _maintain(self):
        while True:
            if self.enabled and time.monotonic() >= self.retry_at:
                result = await self._request({'op': 'index'})
                delay = .5 if result.get('pending', 0) else 10
            else:
                delay = 1
            await asyncio.sleep(delay)

    async def search(self, query, limit=8):
        limit = max(1, min(limit, 30))
        lexical = self.store.search(query, min(60, limit * 3))
        if not str(query).strip() or not self.enabled or not self.ready:
            return lexical[:limit]
        try:
            # Leave room for the desktop adapter's 2-second HTTP timeout.
            async with asyncio.timeout(1.5):
                result = await self._request({'op': 'search', 'query': str(query)[:2000], 'limit': limit * 3})
        except TimeoutError:
            self.store.audit('memory_vector', 'query_timeout_keyword_fallback')
            return lexical[:limit]
        if not self.enabled:
            return lexical[:limit]
        semantic = []
        with self.store.lock:
            for hit in result.get('hits', []):
                row = self.store.db.execute('SELECT * FROM memories WHERE id=?', (hit['id'],)).fetchone()
                if row:
                    semantic.append({**dict(row), 'similarity': hit['similarity']})
        return fuse(lexical, semantic, limit)

    async def control(self, action):
        if action not in ('status', 'on', 'off', 'rebuild'):
            raise ValueError('Unknown memory action')
        if action == 'status':
            return self.status()
        if action in ('on', 'off'):
            self.store.put('vector_memory_enabled', action == 'on')
        await self.close()
        if action == 'rebuild':
            with self.store.lock, self.store.db:
                self.store.db.execute('DELETE FROM memory_vectors')
                self.store.db.execute('DELETE FROM memory_vector_docs')
        self.retry_at, self.error = 0, None
        self.store.audit('memory_vector', action)
        await self.start()
        return self.status()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        async with self.lock:
            await self._kill()
