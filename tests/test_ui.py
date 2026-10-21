import asyncio
import os
from pathlib import Path
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from little_blue.models import Action
from little_blue.sprites import SpriteBank
from little_blue.storage import Store
from little_blue.ui import Controller


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_sprite_all_actions_and_animation_frames(app):
    bank = SpriteBank(Path(__file__).parents[1] / 'assets/blue/manifest.json')
    for action in Action:
        assert len(bank.clips[action].frames) == (8 if action == Action.WALK else 4)
        assert not bank.frame(action, .2).isNull()
    assert bank.frame(Action.IDLE, 0).cacheKey() != bank.frame(Action.IDLE, .5).cacheKey()


def test_missing_idle_and_path_escape_rejected(app, tmp_path):
    import json
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'animations': {}}))
    with pytest.raises(ValueError, match='idle'):
        SpriteBank(path)
    path.write_text(json.dumps({'sheet':'../outside.png','animations':{}}))
    with pytest.raises(ValueError, match='清单目录'):
        SpriteBank(path)


def test_atlas_rectangles_validate_bounds_and_slice(app, tmp_path):
    import json
    from PySide6.QtGui import QColor, QPixmap
    image = QPixmap(20, 30)
    image.fill(QColor('blue'))
    image.save(str(tmp_path / 'sheet.png'))
    path = tmp_path / 'manifest.json'

    def manifest(rects):
        path.write_text(json.dumps({'sheet': 'sheet.png', 'animations': {
            'idle': {'rects': rects, 'fps': 3}}}))

    manifest([[2, 3, 10, 20]])
    frame = SpriteBank(path, size=20).frame(Action.IDLE, 0)
    assert (frame.width(), frame.height()) == (10, 20)
    assert frame.toImage().pixelColor(0, 0) == QColor('blue')
    for rects in [[], [[-1, 0, 10, 20]], [[0, 0, 21, 30]],
                  [[0, 0, 20, 31]], [[0, 0, 0, 1]], [[0, 0, 1.5, 2]],
                  [[False, 0, 1, 2]], [[0, 0, 1]], [[0, 0, 1, 1]] * 65]:
        manifest(rects)
        with pytest.raises(ValueError):
            SpriteBank(path)


def test_clip_can_use_separate_atlas_without_changing_other_actions(app, tmp_path):
    import json
    from PySide6.QtGui import QColor, QPixmap
    for name, color in [('idle', 'blue'), ('walk', 'red')]:
        image = QPixmap(20, 20)
        image.fill(QColor(color))
        image.save(str(tmp_path / (name + '.png')))
    path = tmp_path / 'manifest.json'
    data = {'sheet': 'idle.png', 'animations': {
        'idle': {'rects': [[0, 0, 20, 20]]},
        'walk': {'sheet': 'walk.png', 'rects': [[0, 0, 20, 20]]},
        'talk': {'rects': [[0, 0, 20, 20]]}}}
    path.write_text(json.dumps(data))
    bank = SpriteBank(path)
    for action, color in [('idle', 'blue'), ('walk', 'red'), ('talk', 'blue')]:
        assert bank.frame(action, 0).toImage().pixelColor(0, 0) == QColor(color)
    data['animations']['walk']['sheet'] = '../outside.png'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='清单目录'):
        SpriteBank(path)


def test_walk_motion_matches_rendered_action_and_keeps_phase(app, monkeypatch):
    from types import SimpleNamespace
    from little_blue.life import Life
    from little_blue.models import Settings
    from little_blue.ui import PetWindow
    pet = PetWindow(Life(), Settings(), SpriteBank(Path(__file__).parents[1] / 'assets/blue/manifest.json'))
    pet.timer.stop()
    clock = [100.]
    monkeypatch.setattr('little_blue.ui.time', SimpleNamespace(monotonic=lambda: clock[0]))
    pet.bounds = lambda: (0, 0, 1000, 1000)
    pet.body.x, pet.body.y = 400, 820
    pet.last = clock[0]
    pet.life.override, pet.life.override_until = Action.WALK, float('inf')
    pet.life.direction = 1
    def step():
        clock[0] += .02
        pet.frame()
    try:
        for _ in range(50): step()
        assert pet.body.x == pytest.approx(448)
        assert pet.anim_action == Action.WALK
        assert pet.anim_elapsed == pytest.approx(1)
        elapsed = pet.anim_elapsed
        pet.life.direction = -1
        step()
        assert pet.anim_elapsed > elapsed  # Turning does not reset the stride.
        pet.talk_until = clock[0] + 1
        x = pet.body.x
        step()
        assert pet.anim_action == Action.TALK and pet.body.x == x
        pet.talk_until = 0
        pet.chat_open = True
        step()
        assert pet.anim_action == Action.IDLE and pet.body.x == x
        pet.chat_open = False
        pet.life.override = Action.IDLE
        pet.settings.follow_mouse = pet.settings.perception = True
        monkeypatch.setattr('little_blue.ui.QCursor', SimpleNamespace(pos=lambda: QPoint(900, 900)))
        step()
        assert pet.body.x > x and pet.anim_action == Action.WALK
    finally:
        pet.close()


def test_network_pending_does_not_block_life_or_frames(app, tmp_path):
    store = Store(tmp_path / 'pet.db')
    c = Controller(app, store, Path(__file__).parents[1] / 'assets', tmp_path)
    c.settings.online = True
    async def pending(*args):
        await asyncio.sleep(60)
    c.worker.agent.client.complete = pending
    c.show_panel()
    c.submit('test')
    start = c.life.elapsed
    QTest.qWait(1100)
    assert c.busy
    assert c.life.elapsed > start + .5
    assert c.pet.anim_elapsed > .5
    c.cancel()
    QTest.qWait(200)
    assert not c.busy
    assert store.tasks()[0]['status'] == 'cancelled'
    c.quit()
    for _ in range(100):
        if not c.worker.thread.is_alive():
            break
        QTest.qWait(20)
    QTest.qWait(100)
    assert not c.worker.thread.is_alive()


def test_click_drag_and_shutdown(app, tmp_path):
    store = Store(tmp_path / 'pet.db')
    c = Controller(app, store, Path(__file__).parents[1] / 'assets', tmp_path)
    trust = c.life.needs.trust
    assert not c.panel.isVisible()
    QTest.mouseClick(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(90, 30))
    assert c.life.needs.trust > trust
    assert c.chat_bubble.isVisible()
    assert c.pet.chat_open
    assert not c.panel.isVisible()
    assert not c.worker.jobs
    # Body clicks toggle chat without forcing a jump or opening the panel.
    previous = c.life.action
    QTest.mouseClick(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(90, 140))
    assert not c.chat_bubble.isVisible()
    assert not c.pet.chat_open
    assert c.life.action == previous
    QTest.mouseClick(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(90, 140))
    assert c.chat_bubble.isVisible()
    c.chat_bubble.input.setText('我正在准备答辩')
    c.chat_bubble.send()
    for _ in range(100):
        QTest.qWait(20)
        if not c.busy:
            break
    assert '准备答辩' in c.chat_bubble.chat.toPlainText()
    assert not c.panel.isVisible()
    before = c.pet.pos()
    c.life.direction = 1
    QTest.qWait(300)
    assert c.pet.pos() == before  # Typing target stays put; sprite animation continues.
    c.chat_bubble.input.setText('未发送草稿')
    QTest.keyClick(c.chat_bubble, Qt.Key.Key_Escape)
    assert not c.chat_bubble.isVisible()
    c.pet.status_requested.emit()
    c.life.needs.energy = 42
    c.status_bubble.refresh()
    assert '精力 42' in c.status_bubble.text()
    c.status_bubble.expiry.start(20)
    QTest.qWait(60)
    assert not c.status_bubble.isVisible()
    assert not c.status_bubble.refresh_timer.isActive()
    QTest.mouseDClick(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(90, 140))
    assert not c.panel.isVisible()
    c.status_bubble.hide()
    QTest.mousePress(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(90,90))
    QTest.mouseMove(c.pet, QPoint(120,110), delay=50)
    QTest.mouseRelease(c.pet, Qt.MouseButton.LeftButton, pos=QPoint(120,110))
    assert not c.pet.dragging
    assert not c.chat_bubble.isVisible()
    assert c.chat_bubble.input.text() == '未发送草稿'
    assert c.life.action != Action.DRAG
    assert not c.status_bubble.isVisible()
    c.quit()
    for _ in range(100):
        if not c.worker.thread.is_alive():
            break
        QTest.qWait(20)
    QTest.qWait(100)
    assert not c.worker.thread.is_alive()


def test_compact_chat_reminder_requires_approval_without_panel(app, tmp_path):
    store = Store(tmp_path / 'pet.db')
    c = Controller(app, store, Path(__file__).parents[1] / 'assets', tmp_path)
    c.settings.online = c.settings.tools_enabled = True
    calls = []
    async def complete(messages, schemas=None):
        calls.append(messages)
        if len(calls) == 1:
            return {'tool_calls': [{'id': 'r', 'function': {'name': 'create_reminder',
                'arguments': '{"text":"休息","seconds":1200}'}}]}
        return {'content': '已设置20分钟后的休息提醒。'}
    c.worker.agent.client.complete = complete
    try:
        c.pet.chat_requested.emit()
        c.chat_bubble.input.setText('20分钟后提醒我休息')
        c.chat_bubble.send()
        for _ in range(100):
            QTest.qWait(20)
            if c.approvals:
                break
        assert c.approvals and c.approvals[-1].parent() == c.chat_bubble
        assert not store.db.execute('SELECT * FROM reminders').fetchall()
        assert not c.panel.isVisible()
        c.approvals[-1].done(QMessageBox.StandardButton.Yes)
        for _ in range(100):
            QTest.qWait(20)
            if not c.busy:
                break
        assert len(store.db.execute('SELECT * FROM reminders').fetchall()) == 1
        assert '已设置20分钟' in c.chat_bubble.chat.toPlainText(), c.chat_bubble.feedback.text()
        assert not c.panel.isVisible()
    finally:
        c.quit()
        for _ in range(100):
            QTest.qWait(20)
            if not c.worker.thread.is_alive():
                break
        QTest.qWait(100)
