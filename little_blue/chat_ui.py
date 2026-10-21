"""A small, explicitly opened chat beside the pet; never steals focus on replies."""
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout)


class ChatBubble(QDialog):
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.c = controller
        self.setWindowTitle('和 DeepSeek 聊聊')
        self.resize(360, 440)
        self.setStyleSheet('''QWidget {background:#eef8ff;color:#16345b;font:12px 'Microsoft YaHei';}
            QPushButton {background:#dceefe;border:1px solid #9bc4de;border-radius:6px;padding:5px;}
            QPushButton:disabled {color:#879baa;} QLineEdit,QTextEdit {background:white;
            border:1px solid #b6d1e5;border-radius:6px;padding:6px;}
            QLabel#muted {color:#486980;}''')
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status = QLabel('DeepSeek')
        header.addWidget(self.status, 1)
        phone = QPushButton('手机')
        phone.clicked.connect(controller.show_phone)
        header.addWidget(phone)
        status = QPushButton('状态')
        status.clicked.connect(controller.status_bubble.toggle)
        close = QPushButton('收起')
        close.clicked.connect(self.hide)
        header.addWidget(status)
        header.addWidget(close)
        layout.addLayout(header)
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setMinimumHeight(100)
        layout.addWidget(self.chat, 1)
        self.activity = QLabel()
        self.activity.setTextFormat(Qt.TextFormat.PlainText)
        self.activity.setWordWrap(True)
        self.activity.setMaximumHeight(44)
        layout.addWidget(self.activity)
        row = QHBoxLayout()
        self.done_button = QPushButton('做完了')
        self.later = QPushButton('晚点再问')
        self.done_button.clicked.connect(lambda: self.send_text('/done'))
        self.later.clicked.connect(lambda: self.send_text('/later'))
        self.followups = QCheckBox('适时跟进')
        self.followups.clicked.connect(lambda enabled: self.send_text('/followup ' + ('on' if enabled else 'off')))
        for widget in (self.done_button, self.later, self.followups):
            row.addWidget(widget)
        layout.addLayout(row)
        self.input = QLineEdit()
        self.input.setMaxLength(2000)
        self.input.setPlaceholderText('聊聊天，或说“我正在写论文”')
        self.input.returnPressed.connect(self.send)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.send_button = QPushButton('发送')
        self.send_button.clicked.connect(self.send)
        self.cancel_button = QPushButton('取消任务')
        self.cancel_button.clicked.connect(controller.cancel)
        row.addWidget(self.send_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.tools = QCheckBox('允许申请工具')
        self.tools.setToolTip('搜索、提醒等需开启；有副作用的操作仍逐次审批。')
        self.tools.clicked.connect(lambda enabled: controller.setting('tools_enabled', enabled))
        self.dnd = QCheckBox('勿扰')
        self.dnd.clicked.connect(lambda enabled: controller.setting('dnd', enabled))
        row.addWidget(self.tools)
        row.addWidget(self.dnd)
        layout.addLayout(row)
        self.feedback = QLabel('')
        self.feedback.setObjectName('muted')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.rendered = None

    def send_text(self, text):
        if self.c.send_message(text):
            self.feedback.setText('已提交，正在处理…')
            self.refresh()
            return True
        self.feedback.setText('当前无法发送，请等待任务结束或核心恢复连接。')
        return False

    def send(self):
        text = self.input.text().strip()
        if text and self.send_text(text):
            self.input.clear()

    def refresh(self):
        data = self.c.relationship()
        messages = data.get('messages', [])
        if messages != self.rendered:
            self.rendered = messages
            lines = []
            for m in messages:
                who = '你' if m['role'] == 'user' else 'DeepSeek'
                source = '微信' if m['origin'] == 'wechat' else '桌面'
                if m.get('delivery') in ('pending', 'failed'):
                    source += ' · ' + ('待送达' if m['delivery'] == 'pending' else '未确认送达')
                stamp = datetime.fromtimestamp(m['created']).strftime('%m-%d %H:%M')
                lines.append(f"{who} · {source} · {stamp}\n{m['text']}")
            self.chat.setPlainText('\n\n'.join(lines) or '我在这里。可以聊聊天，也可以告诉我你正在忙什么。')
            self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)
        connected = getattr(self.c, 'connected', True)
        ready = connected and not self.c.busy
        mode = '核心断开' if not connected else '正在处理…' if self.c.busy else '联网' if self.c.settings.online else '离线'
        self.status.setText('DeepSeek · ' + mode)
        a = data.get('activity')
        self.activity.setText(('记着：' + a['title'] + ' · ' +
            {'active': '进行中', 'done': '已完成', 'paused': '已暂停'}[a['status']]) if a else '还没记下当前的事 · 说“我正在……”即可')
        self.activity.setToolTip((f"依据：{a['source']}\n来自{'微信' if a['origin'] == 'wechat' else '桌面'} · " +
            datetime.fromtimestamp(a['updated']).strftime('%m-%d %H:%M') + '\n说 /doing 新内容 可以更正。') if a else '')
        self.done_button.setEnabled(bool(ready and a and a['status'] == 'active'))
        self.later.setEnabled(self.done_button.isEnabled())
        self.send_button.setEnabled(ready)
        self.cancel_button.setEnabled(connected and self.c.busy)
        self.followups.setEnabled(ready)
        self.followups.setChecked(data.get('followups', True))
        self.tools.setEnabled(connected)
        self.dnd.setEnabled(connected)
        self.tools.setChecked(self.c.settings.tools_enabled)
        self.dnd.setChecked(self.c.settings.dnd)

    def toggle(self):
        if self.isVisible():
            self.hide()
            return
        self.c.bubble.hide()
        self.c.status_bubble.hide()
        self.refresh()
        pet = self.c.pet
        rect = (QApplication.screenAt(pet.geometry().center()) or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(360, rect.width()), min(440, rect.height()))
        x = max(rect.left(), min(pet.x(), rect.right() - self.width() + 1))
        y = max(rect.top(), min(pet.y() - self.height() - 8, rect.bottom() - self.height() + 1))
        self.move(x, y)
        pet.chat_open = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def hideEvent(self, event):
        self.c.pet.chat_open = False
        super().hideEvent(event)
