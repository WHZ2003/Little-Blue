"""Native Qt adapter. All HTTP and host tools run off the GUI thread."""
import asyncio
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx
from PySide6.QtCore import QObject, Signal
from .models import Action, Emotion, Environment, Needs, Settings
from .perception import perceive
from .tools import ToolRuntime
from .ui import Controller


class PhoneRuleError(RuntimeError):
    pass


def connection(url, token_file):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('核心连接只允许本机 HTTP 地址。')
    token = Path(token_file).read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise ValueError('核心认证令牌无效，请运行 tools/init_core.py。')
    return url.rstrip('/'), {'Authorization': 'Bearer ' + token}


def initial_state(url, headers):
    # Called before constructing/showing Qt windows; bounded startup only.
    with httpx.Client(timeout=3, trust_env=False) as client:
        response = client.get(url + '/state', headers=headers)
        response.raise_for_status()
        return response.json()


class RemoteStore:
    def __init__(self, state):
        self.state = state
        self.worker = None

    def get(self, key, default=None):
        return self.state.get(key, default)

    def put(self, key, value):
        if key == 'settings' and self.worker:
            self.worker.send('configure', settings=value)

    def remember(self, text, kind='episodic'):
        self.worker.send('remember', text=str(text)[:4000], kind=kind)

    def search_async(self, query):
        self.worker.search(query)

    def tasks(self):
        return self.state.get('tasks', [])

    def budget_usage(self):
        return self.state.get('budget', {'requests': 0, 'reserved': 0})

    def task(self, *args):
        pass  # The core owns task state, including approval transitions.

    def audit(self, tool, decision):
        self.worker.send('audit', text=tool[:64], value=decision[:64])

    def close(self):
        pass  # Desktop exit does not close the container's database.


class RemoteWorker(QObject):
    result = Signal(str, str, bool)
    approval = Signal(str, str, str)
    state = Signal(object)
    event = Signal(object)
    memory = Signal(object)
    connectivity = Signal(bool)
    error = Signal(str)
    identity_result = Signal(object)
    identity_error = Signal(str)
    phone_result = Signal(object)
    phone_error = Signal(str)

    def __init__(self, store, url, headers):
        super().__init__()
        self.store, self.url, self.headers = store, url, headers
        store.worker = self
        self.host_id = uuid.uuid4().hex
        self.environment = Environment().model_dump()
        self.after = store.state['sequence']
        self.instance_id = store.state['instance_id']
        self.jobs = {}
        self.pending = {}
        self.host_jobs = {}
        self.shown = set()
        self.abandoned = set()
        self.connected = False
        self.stopping = False
        self.settings = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._thread_main, name='little-blue-core-client', daemon=True)

    def start(self, settings):
        self.settings = settings
        self.host_tools = ToolRuntime(self.store, settings, self._approve_host)
        self.thread.start()

    def _thread_main(self):
        asyncio.run(self._main())

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.request_lock = asyncio.Lock()
        self.shutdown = asyncio.Event()
        async with httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=2,
                                      trust_env=False) as client:
            self.client = client
            self.ready.set()
            if self.stopping:
                self.shutdown.set()
            poller = asyncio.create_task(self._poll())
            await self.shutdown.wait()
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            for task in self.host_jobs.values():
                task.cancel()
            await asyncio.gather(*self.host_jobs.values(), return_exceptions=True)
            for future in self.pending.values():
                future.cancel()
            try:
                await self.rpc('disconnect')
            except Exception:
                pass

    async def rpc(self, op, **payload):
        async with self.request_lock:
            response = await self.client.post('/rpc', json={'op': op, 'host': self.host_id, **payload})
            if response.status_code != 200:
                if op == 'phone' and response.status_code == 400:
                    data = response.json()
                    if data.get('error') == 'game_rule' and isinstance(data.get('message'), str):
                        raise PhoneRuleError(data['message'][:160])
                raise RuntimeError(f'核心请求未完成（HTTP {response.status_code}）。')
            return response.json()

    def _schedule(self, coroutine):
        if self.stopping or not self.ready.is_set():
            coroutine.close()
            return None
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def send(self, op, **payload):
        async def run():
            try:
                if not self.connected:
                    raise RuntimeError('核心已断开，此操作未发送。')
                await self.rpc(op, **payload)
            except Exception:
                self.error.emit('核心操作未完成；没有自动重试，请确认连接后再操作。')
        self._schedule(run())

    def identity(self, action, revision=None):
        async def run():
            try:
                if not self.connected:
                    raise RuntimeError('disconnected')
                snapshot = await self.rpc('identity', identity_action=action, revision=revision)
                self.identity_result.emit(snapshot)
            except Exception:
                self.identity_error.emit('身份操作未确认；不会自动重试。')
        if self._schedule(run()) is None:
            self.identity_error.emit('核心尚未连接。')

    def phone_command(self, command):
        async def run():
            try:
                if not self.connected:
                    raise RuntimeError('disconnected')
                result = await self.rpc('phone', phone=command)
                self.phone_result.emit(result)
            except PhoneRuleError as error:
                self.phone_error.emit(str(error))
            except Exception:
                self.phone_error.emit('操作未确认：请检查连接、接管状态或游戏条件，刷新后再试；不会自动重做。')
        if self._schedule(run()) is None:
            self.phone_error.emit('核心尚未连接，未执行操作。')

    def search(self, text):
        async def run():
            try:
                rows = await self.rpc('search', text=text[:4000])
                self.memory.emit(rows['rows'])
            except Exception:
                self.error.emit('记忆查询失败，请检查核心连接。')
        self._schedule(run())

    def submit(self, text, state, proactive=False, reflection=False):
        task_id = uuid.uuid4().hex
        async def run():
            try:
                if not self.connected:
                    raise RuntimeError('disconnected')
                await self.rpc('submit', text=text[:2000], task_id=task_id, reflection=reflection)
            except Exception:
                self.result.emit(task_id, '核心未确认任务；不会自动重试，请查看任务列表。', False)
        future = self._schedule(run())
        if future is not None:
            self.jobs[task_id] = future
        return task_id

    async def _poll(self):
        while True:
            try:
                state = await self.rpc('sync', environment=dict(self.environment), after=self.after)
                self.connected = True
                self.connectivity.emit(True)
                self.state.emit(state)
                restarted = state['instance_id'] != self.instance_id
                if restarted:
                    self.instance_id = state['instance_id']
                    self.after = 0  # New process has a new event sequence; never lose future events.
                else:
                    self.after = state['sequence']
                if not restarted:
                    for event in state['events']:
                        self.event.emit(event)
                active = {request['token'] for request in state['pending']}
                self.shown.intersection_update(active)
                self.abandoned.intersection_update(active)
                for token, job in list(self.host_jobs.items()):
                    if token not in active:
                        job.cancel()
                        self.host_jobs.pop(token, None)
                for request in state['pending']:
                    token = request['token']
                    if token in self.abandoned:
                        await self.rpc('resolve', token=token, value=False,
                                       error='connection_lost_no_replay' if request['kind'] == 'host_tool' else None)
                        continue
                    if token in self.shown:
                        continue
                    self.shown.add(token)
                    if request['kind'] == 'approval':
                        self.approval.emit(token, request['name'], request['description'] + '\n' +
                                           json.dumps(request['args'], ensure_ascii=False, indent=2))
                    else:
                        self.host_jobs[token] = asyncio.create_task(self._execute_host(request))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                self.connectivity.emit(False)
                # Never retain an approval/execution across a lost connection.
                for job in self.host_jobs.values():
                    job.cancel()
                self.abandoned.update(self.shown)
            await asyncio.sleep(.25 if self.connected else 1)

    async def _approve_host(self, name, args, description):
        token = 'native:' + uuid.uuid4().hex
        future = self.loop.create_future()
        self.pending[token] = future
        self.approval.emit(token, name, description + '\n' + json.dumps(args, ensure_ascii=False, indent=2))
        try:
            return await future
        finally:
            self.pending.pop(token, None)

    async def _execute_host(self, request):
        value, error = None, None
        try:
            if not self.connected:
                raise PermissionError('disconnected')
            value = await self.host_tools.call(request['name'], request['args'])
        except asyncio.CancelledError:
            return
        except Exception as exc:
            error = type(exc).__name__
        try:
            await self.rpc('resolve', token=request['token'], value=value, error=error)
        except Exception:
            # No replay of host actions after an ambiguous response.
            self.error.emit('本机工具结果未被核心确认；不会重复执行。')

    def resolve(self, token, approved):
        if token.startswith('native:'):
            def resolve_native():
                future = self.pending.get(token)
                if future and not future.done():
                    future.set_result(bool(approved and self.connected and self.settings.tools_enabled))
            if self.ready.is_set() and not self.stopping:
                self.loop.call_soon_threadsafe(resolve_native)
        else:
            self.send('resolve', token=token, value=bool(approved))

    def cancel(self):
        def cancel_native():
            for job in self.host_jobs.values():
                job.cancel()
            for future in self.pending.values():
                future.cancel()
        if self.ready.is_set() and not self.stopping:
            self.loop.call_soon_threadsafe(cancel_native)
        self.send('cancel')

    def stop(self):
        self.stopping = True
        if self.ready.is_set():
            self.loop.call_soon_threadsafe(self.shutdown.set)


class RemoteController(Controller):
    def __init__(self, app, state, url, headers, asset_root, data_dir):
        self.connected = False
        store = RemoteStore(state)
        worker = RemoteWorker(store, url, headers)
        super().__init__(app, store, asset_root, data_dir, worker=worker)
        worker.state.connect(self.apply_state)
        worker.identity_result.connect(self.identity_dialog.render)
        worker.identity_error.connect(self.identity_dialog.failed)
        worker.phone_result.connect(self.phone_completed)
        worker.phone_error.connect(self.phone_dialog.failed)
        worker.event.connect(self.on_event)
        worker.memory.connect(self.panel.show_memories)
        worker.connectivity.connect(self.connection_changed)
        worker.error.connect(self.show_feedback)
        worker.start(self.settings)

    def connection_changed(self, connected):
        if self.connected and not connected:
            self.dismiss_approvals()
            self.busy = False
            self.life.override = None
            self.show_feedback('核心连接中断；桌面动画和临时离线生命继续运行。草稿保留，恢复后请手动发送。')
        self.connected = connected

    def apply_state(self, state):
        self.store.state = state
        life = state['life']
        self.life.needs = Needs.model_validate(life['needs'])
        self.life.emotion = Emotion.model_validate(life['emotion'])
        self.life.elapsed, self.life.goal = life['elapsed'], life['goal']
        self.life.scores, self.life.direction = life['scores'], life['direction']
        self.life.override, self.life.override_until = Action(life['action']), float('inf')
        current = state['current']
        self.busy = current is not None
        if current:
            self.current_id = current['id']
            self.current_proactive = current['proactive']
            self.current_reflection = current['reflection']
        elif self.approvals:
            self.dismiss_approvals()

    def relationship(self):
        return self.store.state.get('relationship', {})

    def phone_command(self, command):
        self.worker.phone_command(command)

    def phone_completed(self, snapshot):
        self.store.state['phone'] = snapshot
        self.phone_dialog.completed(snapshot)

    def on_event(self, event):
        if self.closing:
            return
        if event['kind'] == 'wechat_incoming':
            self.panel.append_chat('你（微信）', event['text'])
        if event['kind'] == 'result':
            self.worker.jobs.pop(event['id'], None)
            if event.get('silent'):
                self.chat_bubble.refresh()
                return
            quiet = event['proactive'] and (not self.settings.proactive or self.settings.dnd or self.env.focused)
            if not quiet:
                self.panel.append_chat('DeepSeek' if event['ok'] else '系统', event['text'])
                self.chat_bubble.feedback.setText('' if event['ok'] else event['text'][:220])
                self.chat_bubble.refresh()
                if event['ok'] and not self.settings.dnd and not event['reflection'] and not self.chat_bubble.isVisible():
                    self.bubble.say(event['text'], self.pet)
                    self.pet.talk_until = time.monotonic() + 5
        elif event['kind'] in ('reminder', 'notice', 'followup') and not self.settings.dnd:
            self.panel.append_chat('提醒' if event['kind'] == 'reminder' else 'DeepSeek', event['text'])
            self.bubble.say(event['text'], self.pet)
            if event['kind'] == 'reminder':
                self.tray.showMessage('DeepSeek提醒', event['text'])

    def interact(self, kind):
        # Immediate local reaction hides the poll interval; core remains authoritative.
        self.life.interact(kind)
        if self.connected:
            self.worker.send('interact', interaction=kind)

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(now - self.last, 1), now
        self.env = perceive(self.settings.perception, self.settings.dnd)
        self.env.interacting = self.chat_bubble.isVisible()
        self.env.phone_open = self.phone_dialog.isVisible()
        self.life.phone_active = self.store.state.get('phone', {}).get('owner') == 'pet' and self.connected
        self.worker.environment = self.env.model_dump()
        if not self.connected:
            self.life.tick(dt, self.env)
        elif self.life.direction != self.store.state['life']['direction']:
            self.worker.send('direction', direction=self.life.direction)
        if self.panel.isVisible():
            self.panel.refresh()
            if not self.connected:
                self.panel.mode.setText('●  核心断开 · 临时离线生活')
                self.panel.send_button.setEnabled(False)
        if self.chat_bubble.isVisible():
            self.chat_bubble.refresh()

    def save(self):
        if self.connected and not self.closing:
            self.store.put('settings', self.settings.model_dump())
