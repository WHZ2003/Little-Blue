import json
import logging
from pathlib import Path
import time
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QIcon, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QTextEdit, QVBoxLayout, QWidget)
from .cognition import ProactiveGate
from .life import Body, Life
from .models import Action, Emotion, Needs, Settings
from .perception import perceive
from .sprites import SpriteBank
from .worker import Worker
from .identity_ui import IdentityDialog
from .memory_ui import MemoryDialog
from .chat_ui import ChatBubble
from .phone import Phone
from .phone_ui import PhoneDialog

LOG = logging.getLogger(__name__)
LABELS = {"energy": "精力", "boredom": "无聊", "curiosity": "好奇", "social": "社交需求", "mood": "心情", "trust": "亲密度"}
ACTION_NAMES = {"idle": "发呆", "walk": "散步", "run": "奔跑", "jump": "跳跃", "sleep": "睡觉", "happy": "开心", "angry": "生气", "talk": "说话", "drag": "拖动"}
EMOTION_NAMES = {"happy": "开心", "calm": "平静", "curious": "好奇", "sleepy": "困倦", "angry": "不高兴", "surprised": "惊讶"}
GOAL_NAMES = {"settle": "适应桌面", "rest": "休息", "explore": "探索", "play": "玩耍", "stroll": "散步", "observe": "观察"}
WECHAT_NAMES = {'unbound': '未绑定', 'disabled': '已关闭', 'connecting': '连接中',
                'connected': '已连接', 'reconnecting': '重连中', 'expired': '需重新扫码',
                'failed': '连接失败', 'invalid_config': '配置无效'}
STYLE = """
QWidget { background: #0d1729; color: #e6efff; font-family: 'Microsoft YaHei'; font-size: 13px; }
QLabel#title { font-size: 28px; font-weight: 700; }
QLabel#muted { color: #8fa8c9; }
QFrame#card { background: #15233b; border-radius: 14px; padding: 10px; }
QPushButton { background: #233b5c; border: 1px solid #345478; padding: 8px 15px; border-radius: 8px; }
QPushButton:hover { background: #2d507b; }
QPushButton#primary { background: #367dca; border-color: #539ce7; }
QPushButton:disabled { color: #6f8097; }
QLineEdit,QTextEdit,QListWidget,QSpinBox { background: #101e33; border: 1px solid #2c4260; border-radius: 8px; padding: 8px; selection-background-color: #367dca; }
QProgressBar { background: #22324a; border: none; border-radius: 4px; height: 12px; text-align: center; }
QProgressBar::chunk { background: #64bde1; border-radius: 4px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #15233b; padding: 12px 20px; margin: 3px; border-radius: 7px; }
QTabBar::tab:selected { background: #284b73; color: #bcecff; }
QCheckBox { padding: 5px; }
QCheckBox::indicator { width: 17px; height: 17px; }
"""


class PetWindow(QWidget):
    interaction = Signal(str)
    status_requested = Signal()
    chat_requested = Signal()
    open_panel = Signal()
    open_identity = Signal()
    open_memory = Signal()
    open_phone = Signal()
    open_browser = Signal()
    quit_requested = Signal()

    def __init__(self, life, settings, sprites):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.life, self.settings, self.sprites = life, settings, sprites
        self.setFixedSize(settings.size, settings.size)
        self.body = Body()
        self.press = None
        self.dragging = False
        self.chat_open = False
        self.anim_action = Action.IDLE
        self.anim_elapsed = 0.0
        self.talk_until = 0
        self.last = time.monotonic()
        self.reset_position()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.frame)
        self.timer.start(round(1000 / settings.fps))

    def bounds(self):
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        return rect.left(), rect.top(), rect.left() + rect.width(), rect.top() + rect.height()

    def reset_position(self):
        rect = QApplication.primaryScreen().availableGeometry()
        self.body.x = rect.left() + rect.width() * .72
        self.body.y = rect.bottom() - self.height() + 1
        self.body.vy = 0
        self.move(round(self.body.x), round(self.body.y))

    def frame(self):
        now = time.monotonic()
        dt, self.last = min(.05, now - self.last), now
        action = self.life.action
        if now < self.talk_until and action not in (Action.DRAG, Action.SLEEP):
            action = Action.TALK
        # The body and renderer must use the same action: a talking/standing
        # sprite must not continue translating with a hidden walking command.
        old_x = self.body.x
        if not self.dragging and self.press is None and not self.chat_open:
            target = QCursor.pos().x() - self.width() / 2 if self.settings.follow_mouse and self.settings.perception and not self.settings.dnd and not self.life.phone_active and action != Action.TALK else None
            self.life.direction = self.body.tick(dt, action, self.bounds(), self.width(), self.life.direction, target)
            self.move(round(self.body.x), round(self.body.y))
        distance = abs(self.body.x - old_x)
        if self.dragging:
            action = Action.DRAG
        elif action in (Action.WALK, Action.RUN) and (self.chat_open or self.press is not None):
            action = Action.IDLE
        elif action == Action.IDLE and distance > 0:
            action = Action.WALK  # Mouse-follow movement needs an actual gait.
        if action != self.anim_action:
            self.anim_action, self.anim_elapsed = action, 0
        # Clamp/boundary pauses cannot keep feet walking in place. Advance the
        # gait from actual local distance; all other clips remain time-driven.
        self.anim_elapsed += distance / (130 if action == Action.RUN else 48) if action in (Action.WALK, Action.RUN) else dt
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pix = self.sprites.frame(self.anim_action, self.anim_elapsed)
        if self.body.direction < 0:
            painter.translate(self.width(), 0)
            painter.scale(-1, 1)
        painter.drawPixmap((self.width() - pix.width()) // 2, self.height() - pix.height(), pix)
        if self.life.phone_active:
            painter.resetTransform()
            painter.setPen(QColor('#cee8fc'))
            painter.setBrush(QColor('#294868'))
            painter.drawRoundedRect(QRect(self.width() - 23, self.height() - 37, 18, 30), 4, 4)
            painter.setBrush(QColor('#8fd0e6'))
            painter.drawRect(QRect(self.width() - 20, self.height() - 32, 12, 19))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.globalPosition().toPoint()
            self.offset = event.position().toPoint()
            self.dragging = False

    def mouseMoveEvent(self, event):
        if self.press is not None and (event.globalPosition().toPoint() - self.press).manhattanLength() > 5:
            if not self.dragging:
                self.dragging = True
                self.interaction.emit("drag")
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            point = event.globalPosition().toPoint() - self.offset
            self.move(point)
            self.body.x, self.body.y, self.body.vy = point.x(), point.y(), 0

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        was_dragging = self.dragging
        kind = "release" if was_dragging else "pet" if event.position().y() < self.height() * .55 else None
        self.press, self.dragging = None, False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if kind:
            self.interaction.emit(kind)
        if not was_dragging:
            self.chat_requested.emit()

    def mouseDoubleClickEvent(self, event):
        # A double click must not unexpectedly open a large window.
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("聊聊天", self.chat_requested.emit)
        menu.addAction("她的手机…", self.open_phone.emit)
        menu.addAction("她的浏览器…", self.open_browser.emit)
        menu.addAction("查看状态", self.status_requested.emit)
        menu.addAction("身份与成长…", self.open_identity.emit)
        menu.addAction("记忆与依据…", self.open_memory.emit)
        menu.addAction("DeepSeek控制台", self.open_panel.emit)
        menu.addAction("摸摸头", lambda: self.interaction.emit("pet"))
        menu.addAction("跳一跳", lambda: self.interaction.emit("click"))
        menu.addAction("回到屏幕", self.reset_position)
        menu.addSeparator()
        menu.addAction("保存并退出", self.quit_requested.emit)
        menu.exec(event.globalPos())


class Bubble(QLabel):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setFixedWidth(290)
        self.setStyleSheet("background:#eef8ff;color:#16345b;border:1px solid #90c9e8;border-radius:14px;padding:14px;font-size:14px;")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def say(self, text, pet):
        self.setText(text[:220])
        self.adjustSize()
        rect = (QApplication.screenAt(pet.pos()) or QApplication.primaryScreen()).availableGeometry()
        self.move(max(rect.left(), min(pet.x(), rect.right() - self.width())), max(rect.top(), pet.y() - self.height() - 8))
        self.show()
        self.timer.start(9000)


class StatusBubble(QLabel):
    """Small, live, non-activating status card, available without network or panel."""
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.c = controller
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setFixedWidth(250)
        self.setStyleSheet("background:#eef8ff;color:#16345b;border:1px solid #90c9e8;"
                          "border-radius:12px;padding:12px;font:12px 'Microsoft YaHei';")
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.hide)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(250)
        self.refresh_timer.timeout.connect(self.refresh)

    def toggle(self):
        if self.isVisible():
            self.hide()
            return
        self.c.bubble.hide()
        self.refresh()
        self.show()
        self.expiry.start(5000)
        self.refresh_timer.start()

    def refresh(self):
        c, n = self.c, self.c.life.needs
        wechat = getattr(c.store, 'state', {}).get('wechat')
        wechat_line = f"微信：{WECHAT_NAMES.get(wechat, '状态未知')}\n" if wechat else ''
        mode = ("核心断开 · 离线生活" if getattr(c, "connected", True) is False else
                "正在思考" if c.busy else "联网认知已开启" if c.settings.online else "离线生活")
        self.setText(
            f"DeepSeek · {ACTION_NAMES[c.life.action]} · {EMOTION_NAMES[c.life.emotion.name]}\n"
            f"精力 {n.energy:.0f}    心情 {n.mood:.0f}    亲密 {n.trust:.0f}\n"
            f"无聊 {n.boredom:.0f}    好奇 {n.curiosity:.0f}    社交需求 {n.social:.0f}\n"
            f"{mode}\n"
            f"感知{'开' if c.settings.perception else '关'} · 勿扰{'开' if c.settings.dnd else '关'}\n"
            f"{wechat_line}"
            "5 秒后收起 · 再次点击可关闭")
        self.adjustSize()
        rect = (QApplication.screenAt(c.pet.geometry().center()) or
                QApplication.primaryScreen()).availableGeometry()
        x = max(rect.left(), min(c.pet.x(), rect.right() - self.width() + 1))
        y = c.pet.y() - self.height() - 8
        if y < rect.top():
            y = c.pet.y() + c.pet.height() + 8
        y = max(rect.top(), min(y, rect.bottom() - self.height() + 1))
        self.move(x, y)

    def mousePressEvent(self, event):
        self.hide()

    def hideEvent(self, event):
        self.expiry.stop()
        self.refresh_timer.stop()
        super().hideEvent(event)


class Panel(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.setWindowTitle("DeepSeek · 桌面生命")
        self.resize(740, 700)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        heading = QHBoxLayout()
        title = QLabel("DeepSeek")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        self.mode = QLabel("●  离线生活中")
        self.mode.setStyleSheet("color:#81d9c3;font-weight:600")
        heading.addWidget(self.mode)
        layout.addLayout(heading)
        subtitle = QLabel("她有自己的节奏，也愿意陪你一会儿。")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.build_life()
        self.build_chat()
        self.build_memory()
        self.build_tasks()
        self.build_settings()
        self.footer = QLabel("点击桌宠聊聊天  ·  点击头部同时摸头  ·  右键查看状态或控制台  ·  拖动移动")
        self.footer.setObjectName("muted")
        layout.addWidget(self.footer)

    def page(self, name):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 18, 10, 10)
        self.tabs.addTab(page, name)
        return layout

    def build_life(self):
        layout = self.page("生活")
        top = QHBoxLayout()
        self.portrait = QLabel()
        self.portrait.setFixedSize(175, 175)
        top.addWidget(self.portrait)
        self.feeling = QLabel()
        self.feeling.setWordWrap(True)
        top.addWidget(self.feeling, 1)
        layout.addLayout(top)
        self.bars = {}
        form = QFormLayout()
        for key, name in LABELS.items():
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.bars[key] = bar
            form.addRow(name, bar)
        layout.addLayout(form)
        row = QHBoxLayout()
        for name, kind in [("摸摸头", "pet"), ("跳一跳", "click")]:
            b = QPushButton(name)
            b.clicked.connect(lambda checked=False, k=kind: self.c.interact(k))
            row.addWidget(b)
        b = QPushButton("找回桌宠")
        b.clicked.connect(self.c.pet.reset_position)
        row.addWidget(b)
        layout.addLayout(row)
        self.debug = QLabel()
        self.debug.setWordWrap(True)
        self.debug.setObjectName("muted")
        layout.addWidget(self.debug)
        layout.addStretch()

    def build_chat(self):
        layout = self.page("交流")
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlainText("DeepSeek：你好，我是DeepSeek。即使没有网络，我也会在桌面上生活。\n")
        layout.addWidget(self.chat)
        self.input = QLineEdit()
        self.input.setMaxLength(2000)
        self.input.setPlaceholderText("说点什么，或交给DeepSeek一个任务…")
        self.input.returnPressed.connect(self.send)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        cancel = QPushButton("取消当前任务")
        cancel.clicked.connect(self.c.cancel)
        row.addWidget(self.send_button)
        row.addWidget(cancel)
        layout.addLayout(row)
        label = QLabel("联网后，对话、相关记忆和已批准工具的结果会发送到 DeepSeek。")
        label.setWordWrap(True)
        label.setObjectName("muted")
        layout.addWidget(label)

    def send(self):
        text = self.input.text().strip()
        if text and self.c.send_message(text):
            self.input.clear()

    def append_chat(self, who, text):
        # Model and tool text is never rendered as HTML or executable rich text.
        self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)
        self.chat.insertPlainText(f"\n{who}：{text}\n")
        self.chat.ensureCursorVisible()

    def build_memory(self):
        layout = self.page("记忆")
        self.query = QLineEdit()
        self.query.setPlaceholderText("检索经历或偏好（支持中文子串）")
        self.query.returnPressed.connect(self.refresh_memory)
        layout.addWidget(self.query)
        self.memories = QTextEdit()
        self.memories.setReadOnly(True)
        layout.addWidget(self.memories)
        row = QHBoxLayout()
        search = QPushButton("检索")
        search.clicked.connect(self.refresh_memory)
        remember = QPushButton("记住这条偏好")
        remember.clicked.connect(self.remember)
        reflect = QPushButton("总结近期经历")
        reflect.clicked.connect(lambda: self.c.submit("请简短总结近期经历、未完成事项和可以改进的互动方式。区分事实和推测。", reflection=True))
        for b in (search, remember, reflect):
            row.addWidget(b)
        layout.addLayout(row)
        layout.addWidget(QLabel("偏好由你明确保存；模型总结不会自动变成用户事实。"))

    def remember(self):
        text = self.query.text().strip()
        if text:
            self.c.store.remember(text, "semantic")
            self.refresh_memory()

    def refresh_memory(self):
        if hasattr(self.c.store, "search_async"):
            self.c.store.search_async(self.query.text())
            return
        rows = self.c.store.search(self.query.text(), 20)
        self.show_memories(rows)

    def show_memories(self, rows):
        self.memories.setPlainText("\n\n".join(f"[{r['kind']}] {r['created'][:16]}\n{r['text']}" for r in rows))

    def build_tasks(self):
        layout = self.page("任务")
        self.tasks = QListWidget()
        layout.addWidget(self.tasks)
        layout.addWidget(QLabel("工具调用逐次审批。中断的任务不会在重启后自动执行。"))
        cancel = QPushButton("取消任务与待审批请求")
        cancel.clicked.connect(self.c.cancel)
        layout.addWidget(cancel)

    def build_settings(self):
        layout = self.page("设置")
        self.checks = {}
        items = [("online", "启用 DeepSeek 联网认知（需环境变量中的 API Key）"),
                 ("proactive", "允许主动交流"), ("dnd", "勿扰 / 专注模式"),
                 ("wechat_silence", "微信允许记住但不回复（避免重复回应连发消息）"),
                 ("perception", "感知空闲时间、全屏状态与鼠标（不读取窗口标题）"),
                 ("follow_mouse", "跟随鼠标（需开启环境感知）"),
                 ("tools_enabled", "允许申请使用工具（外部操作仍需逐次审批）")]
        for key, label in items:
            check = QCheckBox(label)
            check.setChecked(getattr(self.c.settings, key))
            check.toggled.connect(lambda value, k=key: self.c.setting(k, value))
            self.checks[key] = check
            layout.addWidget(check)
        form = QFormLayout()
        model = QLineEdit(self.c.settings.model)
        model.setMaxLength(100)
        model.editingFinished.connect(lambda: self.c.setting("model", model.text().strip() or "deepseek-flash"))
        form.addRow("DeepSeek 模型", model)
        for key, label, low, high in [("daily_requests", "每日请求上限", 0, 1000),
                                     ("daily_tokens", "每日 Token 预算（含未结算预留）", 0, 2000000),
                                     ("proactive_cooldown", "主动交流间隔 / 秒", 60, 86400)]:
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(self.c.settings, key))
            spin.valueChanged.connect(lambda value, k=key: self.c.setting(k, value))
            form.addRow(label, spin)
        layout.addLayout(form)
        row = QHBoxLayout()
        imp = QPushButton("导入角色 manifest.json")
        imp.clicked.connect(self.import_sprite)
        shot = QPushButton("单次屏幕截图…")
        shot.clicked.connect(self.capture)
        row.addWidget(imp)
        row.addWidget(shot)
        layout.addLayout(row)
        note = QLabel("API Key 只从 DEEPSEEK_API_KEY 环境变量读取。截图仅按次保存到你指定的位置，不自动上传。关闭联网会取消当前认知任务。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.budget_label = QLabel()
        self.budget_label.setObjectName("muted")
        layout.addWidget(self.budget_label)
        layout.addStretch()

    def import_sprite(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择角色清单", "", "JSON (*.json)")
        if path:
            try:
                sprites = SpriteBank(path, self.c.settings.size)
                self.c.pet.sprites = sprites
                self.c.setting("sprite_manifest", path)
            except Exception as error:
                QMessageBox.warning(self, "角色导入失败", str(error))

    def capture(self):
        path, _ = QFileDialog.getSaveFileName(self, "仅此次授权：将主屏幕截图保存到本机", "screenshot.png", "PNG (*.png)")
        if path:
            self.c.store.audit("screen_capture", "explicit_user_save")
            if not QApplication.primaryScreen().grabWindow(0).save(path):
                QMessageBox.warning(self, "截图失败", "无法保存截图。")

    def refresh(self):
        life = self.c.life
        self.mode.setText("●  正在思考" if self.c.busy else "●  联网认知就绪" if self.c.settings.online else "●  离线生活中")
        self.feeling.setText(f"DeepSeek · {ACTION_NAMES[life.action]}\n\n情绪  {EMOTION_NAMES[life.emotion.name]}\n自主目标  {GOAL_NAMES[life.goal]}\n\n{self.c.pet.sprites.name}")
        self.portrait.setPixmap(self.c.pet.sprites.frame(life.action, life.elapsed).scaled(175,175,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        for key, bar in self.bars.items():
            bar.setValue(round(getattr(life.needs, key)))
        self.debug.setText(f"Valence {life.emotion.valence:+.2f}  ·  Arousal {life.emotion.arousal:+.2f}\nUtility " + "  /  ".join(f"{k} {v:.0f}" for k,v in life.scores.items()))
        self.send_button.setEnabled(not self.c.busy)
        usage = self.c.store.budget_usage()
        self.budget_label.setText(f"今日请求 {usage['requests']} / {self.c.settings.daily_requests}  ·  Token 已用与预留 {usage['reserved']} / {self.c.settings.daily_tokens}")
        if self.tabs.currentIndex() == 3:
            self.tasks.clear()
            for t in self.c.store.tasks():
                self.tasks.addItem(f"{t['status']}  ·  {t['detail']}\n{t['updated'][:19]}  {t['id'][:8]}")

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class Controller:
    def __init__(self, app, store, asset_root, data_dir, worker=None):
        self.app, self.store, self.data_dir = app, store, Path(data_dir)
        # Offscreen Qt on Windows may not enumerate installed fonts automatically.
        import os
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
            app.setFont(QFont("Microsoft YaHei", 10))
        self.settings = self.restore(Settings, "settings")
        state = store.get("life", {})
        try:
            self.life = Life(Needs.model_validate(state.get("needs", {})), Emotion.model_validate(state.get("emotion", {})), self.settings.personality)
        except Exception:
            LOG.warning("Invalid saved life state; restoring defaults")
            self.life = Life(personality=self.settings.personality)
        manifest = self.settings.sprite_manifest or str(Path(asset_root) / "blue" / "manifest.json")
        try:
            sprites = SpriteBank(manifest, self.settings.size)
        except Exception:
            LOG.warning("Custom sprite unavailable; using bundled sprite")
            sprites = SpriteBank(Path(asset_root) / "blue" / "manifest.json", self.settings.size)
        self.pet = PetWindow(self.life, self.settings, sprites)
        self.bubble = Bubble()
        self.busy = False
        self.current_id = None
        self.current_proactive = False
        self.current_reflection = False
        self.closing = False
        self.approvals = []
        self.worker = worker or Worker(store, self.settings)
        self.phone = None if worker is not None else Phone(store)
        self.worker.result.connect(self.result)
        self.worker.approval.connect(self.approve)
        self.panel = Panel(self)
        self.status_bubble = StatusBubble(self)
        self.chat_bubble = ChatBubble(self)
        self.phone_dialog = PhoneDialog(self)
        self.pet.open_phone.connect(self.show_phone)
        self.pet.open_browser.connect(self.show_browser)
        self.pet.chat_requested.connect(self.chat_bubble.toggle)
        self.pet.interaction.connect(lambda kind: self.chat_bubble.hide() if kind == 'drag' else None)
        self.identity_dialog = IdentityDialog(self)
        self.identity_dialog.setStyleSheet(STYLE)
        self.pet.open_identity.connect(self.show_identity)
        self.memory_dialog = MemoryDialog(self)
        self.memory_dialog.setStyleSheet(STYLE)
        self.pet.open_memory.connect(self.show_memory)
        if hasattr(self.worker, 'memory_result'):
            self.worker.memory_result.connect(self.memory_dialog.render)
            self.worker.memory_error.connect(self.memory_dialog.failed)
        self.pet.status_requested.connect(self.status_bubble.toggle)
        self.pet.interaction.connect(lambda kind: self.status_bubble.hide() if kind == "drag" else None)
        self.pet.interaction.connect(self.interact)
        self.pet.open_panel.connect(self.show_panel)
        self.pet.quit_requested.connect(self.quit)
        self.gate = ProactiveGate(store)
        self.last = time.monotonic()
        self.last_save = 0
        self.last_reflection = time.time()
        self.work_seconds = 0
        self.last_break = time.time()
        self.env = perceive(self.settings.perception, self.settings.dnd)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)
        self.tray = QSystemTrayIcon(QIcon(sprites.frame(Action.IDLE, 0)), app)
        menu = QMenu()
        menu.addAction("聊聊天", self.chat_bubble.toggle)
        menu.addAction("她的手机…", self.show_phone)
        menu.addAction("她的浏览器…", self.show_browser)
        menu.addAction("打开控制台", self.show_panel)
        menu.addAction("身份与成长…", self.show_identity)
        menu.addAction("记忆与依据…", self.show_memory)
        menu.addAction("找回DeepSeek", self.pet.reset_position)
        menu.addAction("保存并退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.chat_bubble.toggle() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.setToolTip("DeepSeek · 桌面生命")
        self.tray.show()
        self.pet.show()
        self.panel.refresh()

    def restore(self, model, key):
        try:
            return model.model_validate(self.store.get(key, {}))
        except Exception:
            LOG.warning("Invalid saved %s; restoring defaults", key)
            return model()

    def show_memory(self):
        self.chat_bubble.hide()
        self.status_bubble.hide()
        self.memory_dialog.show()
        self.memory_dialog.raise_()
        self.memory_dialog.activateWindow()
        self.memory_dialog.request('view')

    def show_identity(self):
        self.chat_bubble.hide()
        self.status_bubble.hide()
        self.identity_dialog.show()
        self.identity_dialog.raise_()
        self.identity_dialog.activateWindow()
        self.identity_dialog.request('view')

    def show_phone(self):
        self.chat_bubble.hide()
        self.status_bubble.hide()
        self.phone_dialog.refresh()
        self.phone_dialog.show()
        self.phone_dialog.raise_()
        self.phone_dialog.activateWindow()
        if hasattr(self.worker, 'environment'):
            self.worker.environment['phone_open'] = True

    def show_browser(self):
        if hasattr(self.worker, 'browser_view'):
            self.worker.browser_view()
        else:
            self.show_feedback('独立浏览器需要 Docker 核心模式；请先运行 start-core.ps1。')

    def phone_state(self):
        return self.phone.snapshot() if self.phone else getattr(self.store, 'state', {}).get('phone')

    def phone_command(self, command):
        try:
            result = self.phone.command(command, self.life)
            self.phone_dialog.completed(result)
        except (ValueError, RuntimeError, PermissionError) as error:
            self.phone_dialog.failed(str(error))

    def show_panel(self):
        self.chat_bubble.hide()
        self.status_bubble.hide()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def setting(self, key, value):
        setattr(self.settings, key, value)
        if (key == "online" and not value) or (key == "tools_enabled" and not value):
            self.cancel()
        if (key == "dnd" and value) or (key == "proactive" and not value):
            self.bubble.hide()
            if self.current_proactive:
                self.cancel()
        self.save()

    def interact(self, kind):
        self.life.interact(kind)
        if kind == "pet":
            now = time.monotonic()
            if now - getattr(self, "last_pet_memory", -100) > 20:
                self.store.remember("用户摸了摸我的头，我感到安心。")
                self.last_pet_memory = now

    def snapshot(self):
        return {"needs": self.life.needs.model_dump(), "emotion": self.life.emotion.model_dump(),
                "personality": self.settings.personality.model_dump(), "action": self.life.action,
                "environment": self.env.model_dump()}

    def relationship(self):
        return self.store.continuity.snapshot()

    def send_message(self, text):
        if self.busy or self.closing or not getattr(self, 'connected', True):
            return False
        self.panel.append_chat('你', text)
        self.submit(text)
        return True

    def show_feedback(self, text):
        self.panel.append_chat('系统', text)
        self.chat_bubble.feedback.setText(text[:220])

    def submit(self, text, proactive=False, reflection=False):
        if self.busy or self.closing:
            return
        self.busy = True
        self.current_proactive, self.current_reflection = proactive, reflection
        self.current_id = self.worker.submit(text, self.snapshot(), proactive, reflection)

    def result(self, task_id, text, ok):
        self.worker.jobs.pop(task_id, None)
        if task_id != self.current_id or self.closing:
            return
        self.busy = False
        self.dismiss_approvals()
        quiet = self.current_proactive and (not self.settings.proactive or self.settings.dnd or self.env.focused)
        if not quiet:
            self.panel.append_chat("DeepSeek" if ok else "系统", text)
            self.chat_bubble.feedback.setText('' if ok else text[:220])
            self.chat_bubble.refresh()
            if ok and not self.settings.dnd and not self.current_reflection and not self.chat_bubble.isVisible():
                self.bubble.say(text, self.pet)
                self.pet.talk_until = time.monotonic() + 5
        if ok and not self.current_reflection:
            self.life.needs.change("social", -20)
        self.panel.refresh()

    def approve(self, token, name, detail):
        if self.closing or not self.settings.tools_enabled or not self.busy:
            self.worker.resolve(token, False)
            return
        self.store.task(self.current_id, "approval", f"等待审批：{name}")
        box = QMessageBox(self.chat_bubble if self.chat_bubble.isVisible() else self.panel)
        box.setWindowTitle("DeepSeek请求一次工具权限")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"工具：{name}\n\n{detail}\n\n仅批准本次参数。文件/网页内容将返回 DeepSeek。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("批准这一次")
        box.button(QMessageBox.StandardButton.No).setText("拒绝")
        task_id = self.current_id
        def resolve(result):
            if self.current_id == task_id and self.busy and not self.closing:
                self.store.task(task_id, "running", "审批结束，继续处理任务")
            self.worker.resolve(token, result == QMessageBox.StandardButton.Yes)
        box.finished.connect(resolve)
        timeout = QTimer(box)
        timeout.setSingleShot(True)
        timeout.timeout.connect(box.reject)
        timeout.start(115000)
        self.approvals.append(box)
        box.open()

    def dismiss_approvals(self):
        for box in self.approvals:
            box.reject()
            box.deleteLater()
        self.approvals.clear()

    def cancel(self):
        self.worker.cancel()
        self.dismiss_approvals()

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(now - self.last, 1), now
        self.env = perceive(self.settings.perception, self.settings.dnd)
        self.env.interacting = self.chat_bubble.isVisible()
        self.env.phone_open = self.phone_dialog.isVisible()
        previous_goal = self.life.goal
        self.life.tick(dt, self.env)
        self.phone.tick(dt, self.life, self.env, busy=self.busy, manual_present=self.env.phone_open)
        if previous_goal != self.life.goal:
            self.store.working.append(f"自主目标：{self.life.goal}")
        wall = time.time()
        if self.env.idle_seconds is not None:
            if self.env.idle_seconds < 60:
                self.work_seconds += dt
            elif self.env.idle_seconds > 300:
                self.work_seconds = 0
        if (self.work_seconds > 3000 and wall - self.last_break > 3000 and
                self.settings.proactive and not self.settings.dnd and not self.env.focused and not self.env.interacting
                and not self.busy and self.env.idle_seconds is not None and self.env.idle_seconds >= 45):
            if (not self.gate.times or wall - self.gate.times[-1] >= self.settings.proactive_cooldown) and len(self.gate.times) < self.settings.proactive_per_hour:
                self.last_break = wall
                self.work_seconds = 0
                self.gate.claim(wall)
                self.panel.append_chat("DeepSeek", "已经忙了很久啦。方便的时候，起来活动一下吧。")
                self.bubble.say("已经忙了很久啦。方便的时候，起来活动一下吧。", self.pet)
        followup = None if self.busy or self.chat_bubble.isVisible() else self.store.continuity.claim_followup(wall, self.settings, self.env, self.gate)
        if followup:
            self.store.continuity.append('assistant', followup, 'desktop')
            self.panel.append_chat('DeepSeek', followup)
            self.bubble.say(followup, self.pet)
        elif not self.busy and self.gate.eligible(wall, self.settings, self.env, self.life.needs):
            self.gate.claim(wall)
            self.submit("现在适合简短主动交流。结合自己的状态和记忆说一句话，不调用工具。", proactive=True)
        if not self.settings.dnd:
            for reminder in self.store.due_reminders(wall):
                self.store.continuity.append('assistant', '提醒：' + reminder['text'], 'desktop')
                self.panel.append_chat("提醒", reminder["text"])
                self.bubble.say("提醒：" + reminder["text"], self.pet)
                self.tray.showMessage("DeepSeek提醒", reminder["text"])
        if now - self.last_save > 15:
            self.save()
            self.last_save = now
        # Reflection is event-driven and only at a low frequency while idle.
        if (wall - self.last_reflection > 21600 and self.settings.online and not self.busy
                and not self.env.focused and not self.settings.dnd and not self.env.interacting):
            self.last_reflection = wall
            self.submit("总结近期经历，区分事实和推测，不调用工具。", reflection=True)
        if self.panel.isVisible():
            self.panel.refresh()
        if self.chat_bubble.isVisible():
            self.chat_bubble.refresh()

    def save(self):
        self.store.put("settings", self.settings.model_dump())
        self.store.put("life", {"needs": self.life.needs.model_dump(), "emotion": self.life.emotion.model_dump()})
        if self.phone:
            self.phone.persist()

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        self.pet.timer.stop()
        self.dismiss_approvals()
        self.worker.stop()
        self.shutdown_timer = QTimer()
        self.shutdown_timer.timeout.connect(self.finish_quit)
        self.shutdown_timer.start(50)

    def finish_quit(self):
        if self.worker.thread.is_alive():
            return
        self.shutdown_timer.stop()
        self.save()
        self.tray.hide()
        self.bubble.hide()
        self.status_bubble.hide()
        self.chat_bubble.hide()
        self.phone_dialog.hide()
        self.pet.hide()
        self.panel.hide()
        self.identity_dialog.hide()
        self.memory_dialog.hide()
        self.store.close()
        self.app.quit()
