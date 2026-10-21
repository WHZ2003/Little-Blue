"""An on-demand virtual handset. Painting is independent from the game clock."""
import math
import time
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)
from .phone import APPS, DECORS, SPECIES


class GameScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.data = None
        self.app = '2048'
        self.setMinimumHeight(220)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)

    def paintEvent(self, event):
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        d = self.data
        if self.app == '2048':
            colors = {0: '#dae5ef', 2: '#e9f5f7', 4: '#c6e7ef', 8: '#a2d6e9',
                      16: '#78b9df', 32: '#569acc', 64: '#647fd0', 128: '#7a73ba',
                      256: '#9b7fbd', 512: '#c888b0', 1024: '#d99180', 2048: '#e3b15a'}
            size = min(w, h) - 6
            cell = size / 4
            x0, y0 = (w - size) / 2, (h - size) / 2
            p.setFont(QFont('Microsoft YaHei', 17, QFont.Weight.Bold))
            for r in range(4):
                for c in range(4):
                    value = d['puzzle']['board'][r][c]
                    rect = QRectF(x0 + c * cell + 3, y0 + r * cell + 3, cell - 6, cell - 6)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(colors.get(value, '#516899')))
                    p.drawRoundedRect(rect, 10, 10)
                    p.setPen(QColor('#244159' if value <= 16 else '#ffffff'))
                    if value:
                        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(value))
        else:
            gradient = QLinearGradient(0, 0, 0, h)
            gradient.setColorAt(0, QColor('#b9edee'))
            gradient.setColorAt(1, QColor('#346c9f'))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gradient)
            p.drawRoundedRect(QRectF(0, 0, w, h), 16, 16)
            clock = time.monotonic()
            if self.app == 'fishing':
                phase = d['fishing']['phase']
                p.setPen(QColor('#285675'))
                p.setFont(QFont('Microsoft YaHei', 12))
                names = {'idle': '抛竿，让浮漂落在水面', 'waiting': '水面很安静……耐心等待',
                         'nibble': '浮漂轻轻动了，再观察一下', 'bite': '浮漂下沉了！现在收竿！',
                         'caught': d['fishing']['last_catch'], 'empty': '太早了，还没有咬钩',
                         'escaped': '晚了一点，小鱼游走了'}
                p.drawText(QRectF(8, 18, w - 16, 45), Qt.AlignmentFlag.AlignCenter, names[phase])
                if phase in ('waiting', 'nibble', 'bite'):
                    x = w / 2 + (math.sin(clock * 10) * 6 if phase == 'nibble' else 0)
                    y = h * .6 + (15 if phase == 'bite' else math.sin(clock * 2) * 2)
                    p.setPen(QColor('#426582'))
                    p.drawLine(QPointF(w - 15, 60), QPointF(x, y))
                    p.setBrush(QColor('#ec806d' if phase == 'bite' else '#ffffff'))
                    p.drawEllipse(QPointF(x, y), 7, 13)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(x, y + 10), 24, 5)
                self.draw_fish(p, w * .3 + math.sin(clock) * 25, h * .85, 17, '#a6d7e8')
            else:
                t = d['tank']
                colors = {'garden': '#548b85', 'coral': '#d498ac', 'moon': '#9eaada'}
                p.setBrush(QColor('#c9cfb4'))
                p.drawRoundedRect(QRectF(0, h - 22, w, 22), 10, 10)
                p.setPen(QColor(colors[t['decor']]))
                for i in range(9):
                    x = 14 + i * (w - 28) / 8
                    p.drawLine(QPointF(x, h - 12), QPointF(x + math.sin(clock + i) * 6, h - 40 - i % 3 * 12))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(colors[t['decor']]))
                p.drawRoundedRect(QRectF(w * .6, h - 66, 55, 48), 22, 22)
                p.setBrush(QColor('#406387'))
                p.drawEllipse(QRectF(w * .6 + 16, h - 48, 23, 30))
                for i, fish in enumerate(t['fish']):
                    x = w / 2 + math.sin(clock * (.3 + i * .02) + i * 2) * (w / 2 - 40)
                    y = 50 + (i * 31) % max(60, h - 115) + math.sin(clock + i) * 5
                    self.draw_fish(p, x, y, 12 + fish['growth'] * .12, SPECIES[fish['species']][1])
                p.setPen(QColor('#285675'))
                p.drawText(QRectF(8, 10, w - 16, 25), Qt.AlignmentFlag.AlignCenter,
                           DECORS[t['decor']] + ' · ' + str(len(t['fish'])) + ' 位住客')
        p.end()

    @staticmethod
    def draw_fish(p, x, y, size, color):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF([QPointF(x - size, y), QPointF(x - size * 1.8, y - size * .7),
                                 QPointF(x - size * 1.8, y + size * .7)]))
        p.drawEllipse(QPointF(x, y), size, size * .65)
        p.setBrush(QColor('#254761'))
        p.drawEllipse(QPointF(x + size * .55, y - size * .14), 2, 2)


class PhoneDialog(QDialog):
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.c = controller
        self.setWindowTitle('DeepSeek 的虚拟手机')
        self.resize(390, 690)
        self.setStyleSheet('''QWidget {background:#15263e;color:#e1edf8;font:12px 'Microsoft YaHei';}
            QPushButton {background:#284560;border:1px solid #547391;border-radius:7px;padding:6px;}
            QPushButton:disabled {color:#728598;} QTextEdit,QComboBox {background:#20364e;
            border:1px solid #45617b;border-radius:6px;padding:4px;}''')
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.choose = QPushButton('她来选')
        self.choose.clicked.connect(lambda: self.send('choose'))
        self.take = QPushButton('我来玩')
        self.take.clicked.connect(lambda: self.send('take'))
        self.release = QPushButton('交还给她')
        self.release.clicked.connect(lambda: self.send('release'))
        for widget in (self.choose, self.take, self.release):
            row.addWidget(widget)
        layout.addLayout(row)
        self.apps = QComboBox()
        for key, name in APPS.items():
            self.apps.addItem(name, key)
        self.apps.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.apps)
        self.score = QLabel()
        self.score.setWordWrap(True)
        layout.addWidget(self.score)
        self.screen = GameScreen()
        layout.addWidget(self.screen, 1)
        self.controls = QStackedWidget()
        self.controls.setFixedHeight(142)
        layout.addWidget(self.controls)
        puzzle = QWidget()
        grid = QGridLayout(puzzle)
        grid.setContentsMargins(0, 0, 0, 0)
        for label, direction, r, c in [('↑', 'up', 0, 1), ('←', 'left', 1, 0),
                                      ('↓', 'down', 1, 1), ('→', 'right', 1, 2)]:
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False, d=direction: self.send('move', direction=d))
            grid.addWidget(b, r, c)
        new = QPushButton('新一局')
        new.clicked.connect(lambda: self.send('new'))
        grid.addWidget(new, 0, 2)
        self.controls.addWidget(puzzle)
        fishing = QWidget()
        row = QHBoxLayout(fishing)
        for label, action in [('抛竿', 'cast'), ('收竿', 'reel')]:
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False, a=action: self.send(a))
            row.addWidget(b)
        self.controls.addWidget(fishing)
        tank = QWidget()
        grid = QGridLayout(tank)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for i, (label, action) in enumerate([('喂食', 'feed'), ('换水', 'clean')]):
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False, a=action: self.send(a))
            grid.addWidget(b, 0, i)
        self.decor = QComboBox()
        for k, v in DECORS.items():
            self.decor.addItem(v, k)
        grid.addWidget(self.decor, 1, 0)
        b = QPushButton('布置小窝')
        b.clicked.connect(lambda: self.send('decorate', decor=self.decor.currentData()))
        grid.addWidget(b, 1, 1)
        self.species = QComboBox()
        for k, v in SPECIES.items():
            self.species.addItem(v[0], k)
        grid.addWidget(self.species, 2, 0)
        b = QPushButton('放入鱼缸')
        b.clicked.connect(lambda: self.send('add_fish', species=self.species.currentData()))
        grid.addWidget(b, 2, 1)
        b = QPushButton('移回收藏')
        b.clicked.connect(lambda: self.send('return_fish', species=self.species.currentData()))
        grid.addWidget(b, 3, 1)
        self.controls.addWidget(tank)
        self.auto = QCheckBox('允许她自主玩手机')
        self.auto.clicked.connect(lambda enabled: self.send('auto_on' if enabled else 'auto_off'))
        layout.addWidget(self.auto)
        self.journal = QTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setMaximumHeight(78)
        layout.addWidget(self.journal)
        self.feedback = QLabel('离线小游戏 · 进度自动保存')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.pending = False
        self.last_app = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)

    def refresh(self, *args):
        d = self.c.phone_state()
        if not d:
            self.feedback.setText('等待核心提供手机状态…')
            return
        app = self.apps.currentData()
        if d['app'] and d['app'] != self.last_app:
            self.last_app = d['app']
            self.apps.blockSignals(True)
            self.apps.setCurrentIndex(list(APPS).index(d['app']))
            self.apps.blockSignals(False)
            app = d['app']
        self.screen.app, self.screen.data = app, d
        self.screen.update()
        self.controls.setCurrentIndex(list(APPS).index(app))
        mode = '她在玩' if d['owner'] == 'pet' else '你在玩' if d['owner'] == 'user' else '待机'
        self.status.setText('DeepSeek 的手机 · ' + mode + '\n' + d['reason'])
        p, t = d['puzzle'], d['tank']
        if app == '2048':
            tag = '本局有人帮忙，不计自主纪录' if p['assisted'] else '本局可计入她的自主纪录'
            tag = ('无路可走 · 可以开新一局' if p['over'] else '已合出 2048！可以继续挑战' if max(map(max, p['board'])) >= 2048 else tag)
            self.score.setText(f"本局 {p['score']}  ·  自主最高 {p['pet_best']}  ·  全部最高 {p['best']}\n{tag}")
        elif app == 'fishing':
            self.score.setText(f"已钓到 {d['catches']} 条 · 等浮漂下沉再收竿\n收藏：" +
                '、'.join(f'{SPECIES[k][0]}×{v}' for k, v in d['collection'].items() if v))
        else:
            growth = max((f['growth'] for f in t['fish']), default=0)
            age = max((f['age'] for f in t['fish']), default=0)
            self.score.setText(f"饱食 {t['food']:.0f} / 100  ·  清洁 {t['clean']:.0f} / 100\n"
                               f"最早入住 {age:.1f} 天  ·  最大成长 {growth:.0f}%  ·  {len(t['fish'])}/8 位住客")
        ready = getattr(self.c, 'connected', True) and not self.pending
        self.controls.setEnabled(ready and d['owner'] == 'user' and app == d['app'])
        for b in (self.choose, self.take, self.release, self.auto):
            b.setEnabled(ready)
        self.auto.setChecked(d['autonomous'])
        content = '\n'.join(reversed(d['journal'][-8:])) or '手机已准备好，还送了一条银色小鱼。'
        if self.journal.toPlainText() != content:
            self.journal.setPlainText(content)

    def send(self, action, **kwargs):
        if self.pending or not getattr(self.c, 'connected', True):
            return
        d = self.c.phone_state()
        if not d:
            return
        self.pending = True
        self.feedback.setText('正在操作…')
        self.c.phone_command({'action': action, 'app': self.apps.currentData(),
                              'revision': d['revision'], **kwargs})
        self.refresh()

    def completed(self, snapshot):
        self.pending = False
        self.feedback.setText('进度已保存 · 你可以继续玩，也可以交还给她')
        self.refresh()

    def failed(self, text):
        self.pending = False
        self.feedback.setText(text)
        self.refresh()

    def hideEvent(self, event):
        d = self.c.phone_state()
        if d and d['owner'] == 'user' and not self.c.closing:
            self.send('release')
        super().hideEvent(event)

    def keyPressEvent(self, event):
        directions = {Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right',
                      Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down'}
        d = self.c.phone_state()
        if event.key() in directions and self.apps.currentData() == '2048' and d and d['owner'] == 'user':
            self.send('move', direction=directions[event.key()])
            event.accept()
        else:
            super().keyPressEvent(event)
