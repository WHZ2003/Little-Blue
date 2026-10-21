import asyncio
import os
from pathlib import Path
import time
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from aiohttp.test_utils import TestServer
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from little_blue.remote import RemoteController, connection
from little_blue.service import create_app, CORE_KEY
from little_blue.storage import Store


async def pump(app, predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        await asyncio.sleep(.02)
    raise AssertionError('Qt/core condition timed out')


async def test_remote_desktop_chat_memory_host_approval_and_disconnect(tmp_path):
    app = QApplication.instance() or QApplication([])
    token = 'test-token-' * 5
    service = create_app(Store(tmp_path / 'core.db'), token)
    core = service[CORE_KEY]
    server = TestServer(service)
    await server.start_server()
    c = RemoteController(app, core.snapshot(), str(server.make_url('')).rstrip('/'),
                         {'Authorization': 'Bearer ' + token},
                         Path(__file__).parents[1] / 'assets', tmp_path)
    try:
        await pump(app, lambda: c.connected)
        assert not c.panel.isVisible()
        c.show_identity()
        await pump(app, lambda: c.identity_dialog.snapshot is not None)
        assert c.identity_dialog.snapshot['enabled']
        assert not c.panel.isVisible()
        c.identity_dialog.auto.setChecked(False)
        await pump(app, lambda: not core.store.growth.snapshot()['enabled'] and c.identity_dialog.auto.isEnabled())
        assert not c.identity_dialog.snapshot['enabled']
        c.identity_dialog.hide()
        c.pet.open_memory.emit()
        await pump(app, lambda: c.memory_dialog.snapshot is not None)
        assert c.memory_dialog.isVisible() and not c.panel.isVisible()
        c.memory_dialog.key.setText('user.测试作业.状态')
        c.memory_dialog.text.setText('测试作业已交')
        c.memory_dialog.reason.setText('用户主动更正')
        c.memory_dialog.revise()
        await pump(app, lambda: c.memory_dialog.snapshot['revision'] == 1)
        assert core.store.ledger.active_facts()[0]['text'] == '测试作业已交'
        c.memory_dialog.rows.setCurrentRow(0)
        assert '用户主动更正' in c.memory_dialog.details.toPlainText()
        c.memory_dialog.request('search', query='测试作业已交')
        await pump(app, lambda: c.memory_dialog.snapshot.get('recall') is not None)
        assert '排序分' in c.memory_dialog.details.toPlainText()
        c.memory_dialog.request('undo')
        await pump(app, lambda: c.memory_dialog.snapshot['revision'] == 2)
        assert not core.store.ledger.active_facts()
        c.memory_dialog.auto.setChecked(False)
        await pump(app, lambda: not c.memory_dialog.snapshot['automatic'])
        c.memory_dialog.hide()
        c.pet.status_requested.emit()
        assert c.status_bubble.isVisible()
        assert '精力' in c.status_bubble.text()
        c.show_panel()
        assert not c.status_bubble.isVisible()
        c.submit('远程离线聊天')
        await pump(app, lambda: '离线模式' in c.panel.chat.toPlainText())
        assert core.store.search('远程离线聊天')
        c.store.remember('喜欢蓝色', 'semantic')
        await pump(app, lambda: bool(core.store.semantic()))
        c.panel.query.setText('喜欢蓝色')
        c.panel.refresh_memory()
        await pump(app, lambda: '喜欢蓝色' in c.panel.memories.toPlainText())
        c.setting('tools_enabled', True)
        await pump(app, lambda: core.settings.tools_enabled)
        executed = []
        async def fake_execute(name, args):
            executed.append((name, args.model_dump()))
            return {'locked': True}
        c.worker.host_tools.execute = fake_execute
        async def request_host(*args):
            await core.tools.call('system_action', {'action': 'lock_screen'})
            return 'host tool completed'
        core.agent._run = request_host
        c.panel.hide()
        c.pet.chat_requested.emit()
        c.chat_bubble.input.setText('synthetic host request')
        c.chat_bubble.send()
        await pump(app, lambda: bool(c.approvals))
        assert c.approvals[-1].parent() == c.chat_bubble
        assert not c.panel.isVisible()
        assert not executed
        c.approvals[-1].done(QMessageBox.StandardButton.Yes)
        await pump(app, lambda: 'host tool completed' in c.panel.chat.toPlainText())
        assert executed == [('system_action', {'action': 'lock_screen'})]
        await pump(app, lambda: 'host tool completed' in c.chat_bubble.chat.toPlainText())
        c.submit('request then revoke')
        await pump(app, lambda: bool(c.approvals))
        c.setting('tools_enabled', False)
        await pump(app, lambda: core.current is None and not core.settings.tools_enabled)
        assert len(executed) == 1
        # Simulate a new core process with a fresh event sequence.
        core.instance_id = uuid.uuid4().hex
        core.sequence = 0
        core.events.clear()
        core.emit('notice', text='new-instance-event')
        await pump(app, lambda: 'new-instance-event' in c.panel.chat.toPlainText())
        await asyncio.sleep(.6)
        app.processEvents()
        assert c.panel.chat.toPlainText().count('new-instance-event') == 1
        await server.close()
        await pump(app, lambda: not c.connected)
        c.panel.hide()
        c.pet.status_requested.emit()
        assert '核心断开' in c.status_bubble.text()
        elapsed = c.life.elapsed
        await pump(app, lambda: c.life.elapsed > elapsed + .4)
        assert c.pet.timer.isActive()
    finally:
        c.quit()
        await pump(app, lambda: not c.worker.thread.is_alive(), timeout=8)
        app.processEvents()
        await server.close()


def test_remote_connection_only_accepts_loopback_and_never_key_in_url(tmp_path):
    token = tmp_path / 'token'
    token.write_text('x' * 48)
    for url in ('https://example.com', 'http://user:pass@localhost', 'http://localhost/path',
                'http://127.0.0.1?key=test'):
        with pytest.raises(ValueError):
            connection(url, token)
    assert connection('http://127.0.0.1:8765', token)[0] == 'http://127.0.0.1:8765'


def test_stale_remote_direction_does_not_undo_local_screen_edge_turn():
    from types import SimpleNamespace
    from little_blue.life import Life
    life = Life()
    c = SimpleNamespace(store=SimpleNamespace(state={}), life=life,
                        last_core_direction=None, approvals=[])
    state = {'current': None, 'life': {
        'needs': life.needs.model_dump(), 'emotion': life.emotion.model_dump(),
        'elapsed': 10, 'goal': 'stroll', 'scores': {}, 'direction': 1, 'action': 'walk'}}
    RemoteController.apply_state(c, state)
    assert life.direction == 1
    life.direction = -1  # The native physics loop bounced at the right edge.
    RemoteController.apply_state(c, state)
    assert life.direction == -1
    state['life']['direction'] = -1  # Acknowledged by the core.
    RemoteController.apply_state(c, state)
    state['life']['direction'] = 1  # A later, genuinely new behavior direction.
    RemoteController.apply_state(c, state)
    assert life.direction == 1


async def test_remote_phone_manual_play_and_release_without_panel(tmp_path):
    from little_blue.phone import choose_direction
    app = QApplication.instance() or QApplication([])
    token = 'test-token-' * 5
    service = create_app(Store(tmp_path / 'core.db'), token)
    core = service[CORE_KEY]
    server = TestServer(service)
    await server.start_server()
    c = RemoteController(app, core.snapshot(), str(server.make_url('')).rstrip('/'),
                         {'Authorization': 'Bearer ' + token},
                         Path(__file__).parents[1] / 'assets', tmp_path)
    try:
        await pump(app, lambda: c.connected)
        c.show_phone()
        await pump(app, lambda: core.env.phone_open)
        assert not c.panel.isVisible()
        c.phone_dialog.send('take')
        await pump(app, lambda: not c.phone_dialog.pending and core.phone.owner == 'user')
        c.phone_dialog.send('move', direction=choose_direction(core.phone.data.puzzle.board))
        await pump(app, lambda: not c.phone_dialog.pending and core.phone.data.puzzle.moves == 1)
        assert c.phone_state()['puzzle']['moves'] == 1
        assert c.phone_state()['puzzle']['assisted']
        assert not core.settings.online
        c.phone_dialog.hide()
        await pump(app, lambda: core.phone.owner == 'none')
        c.show_phone()
        c.phone_dialog.send('auto_off')
        await pump(app, lambda: not c.phone_dialog.pending and not core.phone.data.autonomous)
        assert not c.phone_dialog.auto.isChecked()
    finally:
        c.quit()
        await pump(app, lambda: not c.worker.thread.is_alive(), timeout=8)
        app.processEvents()
        await server.close()
