"""Render our own widgets for visual QA; does not capture the user's desktop."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from little_blue.models import Action
from little_blue.storage import Store
from little_blue.ui import Controller

root = Path(__file__).resolve().parents[1]
out = root / 'qa-output'
out.mkdir(exist_ok=True)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
c = Controller(app, Store(':memory:'), root / 'assets', out)
c.show_panel()

def capture():
    c.panel.grab().save(str(out / 'panel.png'))
    c.panel.hide()
    conversation = c.store.continuity
    mid = conversation.append('user', '我正在准备答辩', 'desktop')
    conversation.observe('我正在准备答辩', 'desktop', mid)
    conversation.append('assistant', '记住了。准备到哪一步了？', 'desktop')
    conversation.append('user', '刚才说的开场，我想从海洋生态讲起。', 'wechat')
    conversation.append('assistant', '好，接着答辩的开场聊。先用一个具体的问题引出主题怎么样？', 'wechat')
    c.chat_bubble.toggle()
    c.chat_bubble.grab().save(str(out / 'chat.png'))
    sheet = QPixmap(len(Action) * 180, 210)
    sheet.fill(QColor('#e6efff'))
    painter = QPainter(sheet)
    painter.setPen(QColor('#15233b'))
    for index, action in enumerate(Action):
        painter.drawPixmap(index * 180, 0, c.pet.sprites.frame(action, .2))
        painter.drawText(index * 180 + 10, 200, action)
    painter.end()
    sheet.save(str(out / 'animation-preview.png'))
    c.quit()

QTimer.singleShot(1000, capture)
app.exec()
