"""On-demand identity viewer; it never occupies the desktop until opened."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QTextEdit, QVBoxLayout
from .growth import describe, version_text




class IdentityDialog(QDialog):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.snapshot = None
        self.setWindowTitle('DeepSeek · 身份与成长')
        self.resize(560, 640)
        layout = QVBoxLayout(self)
        self.summary = QLabel('正在读取…')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.auto = QCheckBox('根据长期记忆自动更新成长设定')
        self.auto.toggled.connect(lambda on: self.request('on' if on else 'off'))
        layout.addWidget(self.auto)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton('刷新')
        self.refresh_button.clicked.connect(lambda: self.request('view'))
        self.undo = QPushButton('撤销最近一次成长更新')
        self.undo.clicked.connect(lambda: self.request('undo'))
        self.core_button = QPushButton('查看核心身份')
        self.core_button.clicked.connect(self.show_core)
        for button in (self.refresh_button, self.undo, self.core_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.history = QListWidget()
        self.history.setMaximumHeight(160)
        self.history.currentRowChanged.connect(self.show_version)
        layout.addWidget(self.history)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        note = QLabel('只在联网时整理，最多每 6 小时一次，计入 API 预算。关闭自动更新保留现有设定。')
        note.setWordWrap(True)
        layout.addWidget(note)

    def request(self, action):
        for widget in (self.auto, self.undo, self.refresh_button):
            widget.setEnabled(False)
        revision = self.snapshot['revision'] if self.snapshot else None
        if hasattr(self.c.worker, 'identity'):
            self.c.worker.identity(action, revision)
        else:
            try:
                growth = self.c.store.growth
                self.render(growth.snapshot() if action == 'view' else growth.control(action, revision))
            except Exception as error:
                self.failed(str(error))

    def render(self, snapshot):
        self.snapshot = snapshot
        self.summary.setText(describe(snapshot))
        self.auto.blockSignals(True)
        self.auto.setChecked(snapshot['enabled'])
        self.auto.blockSignals(False)
        self.auto.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.undo.setEnabled(snapshot['can_undo'])
        self.history.clear()
        for row in snapshot['versions']:
            label = '自动成长' if row['kind'] == 'automatic' else '撤销'
            if row['undone_by'] is not None:
                label += '（已撤销）'
            self.history.addItem(f"v{row['revision']} · {label} · {row['created'][:19]} UTC")
        if snapshot['versions']:
            self.history.setCurrentRow(0)
        else:
            self.show_core()

    def show_version(self, index):
        if self.snapshot and 0 <= index < len(self.snapshot['versions']):
            self.details.setPlainText(version_text(self.snapshot['versions'][index]))

    def show_core(self):
        if self.snapshot:
            self.details.setPlainText('核心身份（自动成长不能修改）：\n\n' + self.snapshot['core'])

    def failed(self, message):
        self.summary.setText(message + '\n请刷新后再操作。')
        self.refresh_button.setEnabled(True)
