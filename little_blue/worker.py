import asyncio
from concurrent.futures import CancelledError
import threading
import uuid
from PySide6.QtCore import QObject, Signal
from .cognition import Agent
from .tools import ToolRuntime


class Worker(QObject):
    result = Signal(str, str, bool)
    approval = Signal(str, str, str)

    def __init__(self, store, settings):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._loop, name="little-blue-cognition", daemon=True)
        self.pending = {}
        self.jobs = {}
        self.store = store
        from .retrieval import MemoryRetrieval
        self.memory = MemoryRetrieval(store)
        store.retriever = self.memory
        self.tools = ToolRuntime(store, settings, self._approve)
        self.agent = Agent(store, settings, self.tools)
        self.thread.start()

    def _loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.memory.start())
        self.loop.run_forever()
        remaining = asyncio.all_tasks(self.loop)
        for task in remaining:
            task.cancel()
        if remaining:
            self.loop.run_until_complete(asyncio.gather(*remaining, return_exceptions=True))
        self.loop.run_until_complete(self.memory.close())
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    async def _approve(self, name, args, description):
        import json
        token = uuid.uuid4().hex
        future = self.loop.create_future()
        self.pending[token] = future
        self.approval.emit(token, name, description + "\n\n" + json.dumps(args, ensure_ascii=False, indent=2))
        try:
            return await future
        finally:
            self.pending.pop(token, None)

    def resolve(self, token, approved):
        def resolve():
            future = self.pending.get(token)
            if future is not None and not future.done():
                future.set_result(approved)
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(resolve)

    def submit(self, text, state, proactive=False, reflection=False):
        task_id = uuid.uuid4().hex
        future = asyncio.run_coroutine_threadsafe(self.agent.run(text, state, proactive, reflection, task_id), self.loop)
        self.jobs[task_id] = future
        def complete(done):
            try:
                self.result.emit(task_id, done.result(), True)
            except CancelledError:
                self.result.emit(task_id, "任务已取消。", False)
            except Exception as error:
                self.result.emit(task_id, str(error)[:500], False)
        future.add_done_callback(complete)
        return task_id

    def cancel(self):
        for future in list(self.jobs.values()):
            future.cancel()

    def stop(self):
        self.cancel()
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
