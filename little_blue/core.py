"""Headless life, cognition, memory and task runtime. No Qt imports."""
import asyncio
from collections import deque
import json
import time
import uuid

from .cognition import Agent, ProactiveGate
from .life import Life
from .models import Needs, Emotion, Environment, Settings
from .tools import SPECS, ToolRuntime
from .phone import Phone
from .errors import public_failure

HOST_TOOLS = frozenset({'get_time', 'get_activity', 'read_file', 'browser_open',
                        'browser_read', 'system_action'})


class DelegatingTools(ToolRuntime):
    def __init__(self, core):
        super().__init__(core.store, core.settings, core.approve)
        self.core = core

    async def call(self, name, raw):
        if name not in HOST_TOOLS:
            return await super().call(name, raw)
        if not self.settings.tools_enabled or not self.core.host_connected:
            raise PermissionError('本机工具不可用：权限关闭或桌面端未连接。')
        args = SPECS[name][0].model_validate(raw).model_dump()
        self.store.audit(name, 'host_requested')
        try:
            # Windows paths and approval must be validated on Windows, never Linux.
            result = await self.core.ask('host_tool', name, args, SPECS[name][1], 150)
            if not self.settings.tools_enabled:
                raise PermissionError('工具权限已撤回。')
            self.store.audit(name, 'succeeded')
            return result
        except asyncio.CancelledError:
            self.store.audit(name, 'cancelled')
            raise
        except Exception:
            self.store.audit(name, 'failed')
            raise


class Core:
    def __init__(self, store, client=None):
        self.store = store
        self.handoff_requested = False
        self.wechat_channel = None
        from .retrieval import MemoryRetrieval
        self.memory = MemoryRetrieval(store)
        store.retriever = self.memory
        self.instance_id = uuid.uuid4().hex
        try:
            self.settings = Settings.model_validate(store.get('settings', {}))
        except ValueError:
            self.settings = Settings()
        state = store.get('life', {})
        try:
            self.life = Life(Needs.model_validate(state.get('needs', {})),
                             Emotion.model_validate(state.get('emotion', {})),
                             self.settings.personality)
        except (ValueError, AttributeError):
            self.life = Life(personality=self.settings.personality)
        self.env = Environment()
        self.phone = Phone(store)
        self.tools = DelegatingTools(self)
        self.agent = Agent(store, self.settings, self.tools, client)
        from .memory_ledger import FactLearner
        self.fact_learner = FactLearner(store, self.settings, self.agent.client,
            lambda: self.current is not None or self.wechat_receiving or self.env.interacting or self.bili.active)
        self.gate = ProactiveGate(store)
        self.pending = {}
        self.events = deque(maxlen=100)
        self.sequence = 0
        self.job = None
        self.current = None
        self.seen_tasks = deque(maxlen=1000)
        self.host_id = None
        self.last_host = float('-inf')
        self.last_save = 0.0
        self.last_reflection = time.time()
        self.last_break = time.time()
        self.work_seconds = 0.0
        self.last_pet = float('-inf')
        self.loop_task = None
        self.wechat_state = 'unbound'
        self.wechat_ready = False
        self.wechat_receiving = False
        from .browser import BrowserRuntime
        self.browser = BrowserRuntime(self)
        from .browser_share import BrowserShare
        self.browser_share = BrowserShare(self)
        from .bridge import BridgeHub
        self.bridge = BridgeHub(self)
        from .bili import BiliRuntime
        self.bili = BiliRuntime(self)
        from .diary import Diary
        self.diary = Diary(self)

    @property
    def host_connected(self):
        return time.monotonic() - self.last_host < 5

    def emit(self, kind, **data):
        self.sequence += 1
        self.events.append({'seq': self.sequence, 'kind': kind, 'created': time.time(), **data})

    def claim_host(self, host_id, environment):
        if self.host_connected and self.host_id != host_id:
            raise RuntimeError('已有桌面端连接此核心。')
        self.host_id, self.last_host = host_id, time.monotonic()
        self.env = Environment.model_validate(environment)
        if not self.settings.perception:
            self.env.idle_seconds = None
            self.env.focused = self.settings.dnd
        else:
            self.env.focused = self.settings.dnd or self.env.focused

    def require_host(self, host_id):
        if not self.host_connected or self.host_id != host_id:
            raise PermissionError('桌面会话已断开，请重新连接。')

    def snapshot(self, after=0, host_id=None):
        visible = self.host_connected and self.host_id == host_id
        return {'version': 1, 'instance_id': self.instance_id, 'sequence': self.sequence,
                'harness': 'hermes' if self.agent.harness else 'little-blue',
                'handoff': self.handoff_status(),
                'wechat': self.wechat_state,
                'communication': {'wechat_session_ready': self.wechat_ready,
                                  'wechat_receiving': self.wechat_receiving,
                                  'proactive_enabled': self.settings.proactive},
                'relationship': self.store.continuity.snapshot(),
                'memory_index': self.memory.status(),
                'phone': self.phone.snapshot(),
                'browser': self.browser.snapshot(), 'bridge': self.bridge.snapshot(),
                'bili': self.bili.snapshot(),
                'diary': self.diary.status(),
                'settings': self.settings.model_dump(),
                'life': {'needs': self.life.needs.model_dump(),
                         'emotion': self.life.emotion.model_dump(),
                         'action': self.life.action, 'goal': self.life.goal,
                         'elapsed': self.life.elapsed, 'direction': self.life.direction,
                         'scores': self.life.scores},
                'environment': self.env.model_dump(), 'current': self.current,
                'budget': self.store.budget_usage(), 'tasks': self.store.tasks(),
                'pending': [item['request'] for item in self.pending.values()] if visible else [],
                'events': [e for e in self.events if e['seq'] > after] if visible else []}

    async def ask(self, kind, name, args, description, timeout):
        if not self.host_connected:
            raise PermissionError('桌面端不在线，无法审批或执行本机操作。')
        token = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        request = {'token': token, 'kind': kind, 'name': name,
                   'args': json.loads(json.dumps(args)), 'description': description}
        self.pending[token] = {'request': request, 'future': future, 'host': self.host_id}
        if self.current:
            self.store.task(self.current['id'], 'approval', f'等待桌面端：{name}')
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(token, None)
            if self.current and not future.cancelled():
                self.store.task(self.current['id'], 'running', '审批结束')

    async def approve(self, name, args, description):
        return await self.ask('approval', name, args, description, 120)

    def resolve(self, token, value, error=None):
        item = self.pending.get(token)
        if item is None or item['future'].done():
            raise ValueError('审批/本机请求已失效。')
        if item['request']['kind'] == 'approval' and type(value) is not bool:
            raise ValueError('审批结果必须是布尔值。')
        if error:
            item['future'].set_exception(RuntimeError(str(error)[:400]))
        else:
            item['future'].set_result(value)

    async def cancel(self):
        await self.bili.cancel()
        if self.job and not self.job.done():
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
        if self.current:
            self.store.task(self.current['id'], 'cancelled', '任务已取消')
            self.current = None

    async def configure(self, raw):
        updated = Settings.model_validate(raw)
        revoke = ((self.settings.online and not updated.online) or
                  (self.settings.tools_enabled and not updated.tools_enabled))
        quiet = self.current and self.current['proactive'] and (updated.dnd or not updated.proactive)
        # Keep shared Settings identity for Agent and ToolRuntime.
        for key in Settings.model_fields:
            setattr(self.settings, key, getattr(updated, key))
        self.life.personality = self.settings.personality
        if revoke or quiet:
            await self.cancel()
        if not updated.perception:
            self.env.idle_seconds = None
            self.env.focused = updated.dnd
        self.save()

    def interact(self, kind):
        self.life.interact(kind)
        if kind == 'pet' and time.monotonic() - self.last_pet > 20:
            self.store.remember('用户摸了摸我的头，我感到安心。')
            self.last_pet = time.monotonic()

    def submit(self, text, task_id=None, proactive=False, reflection=False, origin='desktop', received_ids=None):
        if self.handoff_requested and (origin != 'wechat' or proactive or reflection):
            raise RuntimeError('Handoff is draining; new tasks are paused')
        if origin not in ('desktop', 'wechat'):
            raise ValueError('Invalid origin')
        if self.job and not self.job.done():
            raise RuntimeError('核心正在处理另一个任务。')
        task_id = task_id or uuid.uuid4().hex
        if task_id in self.seen_tasks or any(t['id'] == task_id for t in self.store.tasks()):
            raise ValueError('此任务已经提交，禁止重放。')
        self.seen_tasks.append(task_id)
        self.current = {'id': task_id, 'proactive': proactive, 'reflection': reflection, 'origin': origin,
                        'started': time.time()}
        self.store.task(task_id, 'pending', text)
        self.job = asyncio.create_task(self._run(text, dict(self.current), received_ids))
        return task_id

    async def _run(self, text, info, received_ids=None):
        state = self.snapshot()['life'] | {'personality': self.settings.personality.model_dump(),
                                         'environment': self.env.model_dump(),
                                         'communication': {'微信状态': self.wechat_state, '微信会话可用': self.wechat_ready}}
        try:
            await self.bili.cancel()  # Yield the single Hermes worker to a user/chat task first.
            if info['origin'] == 'wechat':
                kwargs = {'received_ids': received_ids} if received_ids else {}
                answer = await self.agent.run(text, state, info['proactive'], info['reflection'], info['id'], chat_only=True, **kwargs)
            else:
                answer = await self.agent.run(text, state, info['proactive'], info['reflection'], info['id'])
            if not info['reflection'] and answer:
                self.life.needs.change('social', -20)
            self.emit('result', **info, text=answer, ok=True,
                      silent=not bool(answer),
                      message_id=self.agent.assistant_message_id,
                      meme_id=self.agent.meme_id, meme_generation=self.agent.meme_generation)
            return answer
        except asyncio.CancelledError:
            self.store.task(info['id'], 'cancelled', '用户取消、桌面断开或核心关闭')
            self.emit('result', **info, text='任务已取消。', ok=False)
            return '任务已取消。'
        except Exception as error:
            # Do not send raw exception bodies/URLs/secrets over the API.
            self.store.task(info['id'], 'failed', type(error).__name__)
            answer = public_failure(error)
            self.emit('result', **info, text=answer, ok=False)
            return answer
        finally:
            self.current = None

    def save(self):
        self.store.put('settings', self.settings.model_dump())
        self.store.put('life', {'needs': self.life.needs.model_dump(),
                               'emotion': self.life.emotion.model_dump()})

    def handoff_status(self):
        channel = self.wechat_channel
        drained = (not self.current and (channel is None or channel.stopping or
                   (channel.poll_paused and channel.queue.empty() and not channel.processing
                    and channel.held is None and (not channel.image_job or channel.image_job.done()))))
        return {'requested': self.handoff_requested, 'drained': self.handoff_requested and drained}

    async def prepare_handoff(self):
        if self.bridge.active():
            raise RuntimeError('Handoff currently supports the iLink channel only')
        from pathlib import Path
        from .handoff_guard import read_state, write_state
        folder = Path(self.store.path).parent
        state = read_state(folder)
        if state.get('phase') not in (None, 'active', 'draining'):
            raise RuntimeError('This copy has already been handed off')
        write_state(folder, {**state, 'phase': 'draining'})
        self.handoff_requested = True
        await self.bili.close()
        await self.fact_learner.close()
        await self.browser.close()
        await self.browser_share.close()
        self.save()
        return self.handoff_status()

    async def start(self):
        await self.memory.start()
        await self.fact_learner.start()
        await self.browser.start()
        await self.bili.start()
        self.loop_task = asyncio.create_task(self._loop())

    async def _loop(self):
        last = time.monotonic()
        while True:
            await asyncio.sleep(.25)
            if self.handoff_requested:
                continue
            now = time.monotonic()
            dt, last = min(now - last, 1), now
            if not self.host_connected:
                if self.current and self.current.get('origin') != 'wechat':
                    await self.cancel()
                if self.life.action == 'drag':
                    self.life.interact('release')
                self.env = Environment(hour=time.localtime().tm_hour, focused=self.settings.dnd)
            before = self.life.goal
            self.life.tick(dt, self.env)
            self.phone.tick(dt, self.life, self.env, busy=self.current is not None or self.browser.snapshot()['active'] or self.bili.active,
                            manual_present=self.host_connected and self.env.phone_open)
            if before != self.life.goal:
                self.store.working.append(f'自主目标：{self.life.goal}')
            wall = time.time()
            self.diary.tick(wall)
            self.bridge.tick()
            idle = self.env.idle_seconds
            if idle is not None:
                if idle < 60:
                    self.work_seconds += dt
                elif idle > 300:
                    self.work_seconds = 0
            quiet = self.settings.dnd or self.env.focused or not (self.host_connected or self.wechat_ready)
            if not quiet:
                for reminder in self.store.due_reminders(wall):
                    self.store.continuity.append('assistant', '提醒：' + reminder['text'],
                                                 'desktop' if self.host_connected else 'wechat')
                    self.emit('reminder', text=reminder['text'])
            if not quiet and not self.current and not self.wechat_receiving and not self.bili.active:
                followup = self.store.continuity.claim_followup(wall, self.settings, self.env, self.gate)
                if followup:
                    origin = 'desktop' if self.host_connected else 'wechat'
                    self.store.continuity.append('assistant', followup, origin)
                    self.emit('followup', text=followup, origin=origin,
                              source_id=self.store.get('current_activity')['source_id'])
                elif (self.settings.proactive and self.work_seconds > 3000 and wall - self.last_break > 3000
                        and idle is not None and idle >= 45 and not self.env.interacting
                        and self.gate.available(wall, self.settings, self.env)):
                    self.gate.claim(wall)
                    self.last_break, self.work_seconds = wall, 0
                    self.emit('notice', text='已经忙了很久啦。方便的时候，起来活动一下吧。')
                elif self.browser_share.tick(wall):
                    pass
                elif self.gate.eligible(wall, self.settings, self.env, self.life.needs):
                    self.gate.claim(wall)
                    self.submit('根据状态和记忆简短主动交流，不调用工具。', proactive=True,
                                origin='desktop' if self.host_connected else 'wechat')
                elif self.settings.online and wall - self.last_reflection > 21600 and not self.env.interacting:
                    self.last_reflection = wall
                    self.submit('总结近期经历，区分事实和推测，不调用工具。', reflection=True,
                                origin='desktop' if self.host_connected else 'wechat')
            if now - self.last_save > 15:
                self.save()
                self.last_save = now

    async def close(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        await self.bili.close()
        await self.cancel()
        await self.fact_learner.close()
        await self.browser.close()
        await self.browser_share.close()
        await self.bridge.close()
        await self.memory.close()
        self.phone.stop(time.time(), '手机已保存，休息一下。')
        for item in self.pending.values():
            item['future'].cancel()
        self.save()
        self.store.close()
