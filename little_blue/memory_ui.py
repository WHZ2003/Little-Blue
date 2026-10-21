"""On-demand archive, fact revision and recall evidence viewer."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                              QListWidget, QPushButton, QTextEdit, QVBoxLayout)
from .memory_ledger import MemoryCommand


STATUS = {'active': '有效', 'superseded': '已替代', 'retracted': '已撤销'}


class MemoryDialog(QDialog):
    def __init__(self, controller):
        super().__init__()
        self.c, self.snapshot, self.selected = controller, None, None
        self.setWindowTitle('DeepSeek · 记忆与依据')
        self.resize(670, 760)
        layout = QVBoxLayout(self)
        self.summary = QLabel('正在读取…')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        switches = QHBoxLayout()
        self.auto = QCheckBox('自动整理新对话中的明确事实')
        self.decay = QCheckBox('旧经历逐渐降低召回权重')
        self.auto.toggled.connect(lambda on: self.request('auto_on' if on else 'auto_off'))
        self.decay.toggled.connect(lambda on: self.request('decay_on' if on else 'decay_off'))
        switches.addWidget(self.auto)
        switches.addWidget(self.decay)
        layout.addLayout(switches)
        self.query = QLineEdit()
        self.query.setPlaceholderText('输入话题，查看关键词与向量召回依据')
        self.query.returnPressed.connect(lambda: self.request('search', query=self.query.text()))
        layout.addWidget(self.query)
        buttons = QHBoxLayout()
        self.refresh = QPushButton('最新记忆')
        self.refresh.clicked.connect(lambda: self.request('view'))
        self.older = QPushButton('更早记忆')
        self.older.clicked.connect(lambda: self.request('view', before=self.snapshot.get('next_before')))
        self.search = QPushButton('检索并解释')
        self.search.clicked.connect(lambda: self.request('search', query=self.query.text()))
        self.trace = QPushButton('最近召回 / 操作记录')
        self.trace.clicked.connect(self.show_trace)
        for button in (self.refresh, self.older, self.search, self.trace):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.rows = QListWidget()
        self.rows.setMaximumHeight(130)
        self.rows.currentRowChanged.connect(self.choose)
        layout.addWidget(self.rows)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.key = QLineEdit()
        self.key.setPlaceholderText('事实键，例如 user.概率课作业1.状态（不同事项用不同键）')
        self.text = QLineEdit()
        self.text.setMaxLength(500)
        self.text.setPlaceholderText('新的事实（旧原文保留）')
        self.reason = QLineEdit()
        self.reason.setMaxLength(300)
        self.reason.setPlaceholderText('修改依据，例如：我已交作业，之前的状态过时了')
        self.stable = QCheckBox('这是长期身份或稳定偏好，不随时间衰减')
        for widget in (self.key, self.text, self.reason, self.stable):
            layout.addWidget(widget)
        changes = QHBoxLayout()
        self.new = QPushButton('新增事实')
        self.new.clicked.connect(self.new_fact)
        self.save = QPushButton('保存为新版本')
        self.save.clicked.connect(self.revise)
        self.undo = QPushButton('撤销最近一次事实更新')
        self.undo.clicked.connect(lambda: self.request('undo'))
        for button in (self.new, self.save, self.undo):
            changes.addWidget(button)
        layout.addLayout(changes)
        note = QLabel('自动整理最多每 10 分钟一次、24 小时最多 8 次，计入现有 API 预算。'
                      '旧原文和向量保留；关闭自动整理不撤回已有事实。撤销不抹除原始聊天或网页。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.controls = (self.auto, self.decay, self.refresh, self.older, self.search, self.new, self.save, self.undo)

    def request(self, action, **kwargs):
        try:
            cmd = MemoryCommand(action=action, revision=self.snapshot['revision'] if self.snapshot else None, **kwargs)
            for widget in self.controls:
                widget.setEnabled(False)
            if hasattr(self.c.worker, 'manage_memory'):
                self.c.worker.manage_memory(cmd.model_dump())
            else:
                if action == 'search':
                    self.c.store.search(cmd.query, 20)
                self.render(self.c.store.ledger.control(cmd))
        except Exception as error:
            self.failed(str(error))

    def render(self, snapshot):
        self.snapshot = snapshot
        self.summary.setText(f"记忆版本 {snapshot['revision']} · 旧记录保留 · 仅用于当前事实与召回，不改人格")
        for widget in self.controls:
            widget.setEnabled(True)
        for checkbox, key in ((self.auto, 'automatic'), (self.decay, 'decay')):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(snapshot[key]))
            checkbox.blockSignals(False)
        self.undo.setEnabled(snapshot['can_undo'])
        self.older.setEnabled(snapshot['next_before'] is not None)
        self.rows.clear()
        for row in snapshot['rows']:
            self.rows.addItem(f"#{row['id']} · {STATUS[row['memory']['status']]} · {row['created'][:10]} · {row['text'][:45]}")
        self.new_fact()
        self.show_trace()

    def choose(self, index):
        if not self.snapshot or not 0 <= index < len(self.snapshot['rows']):
            return
        row = self.snapshot['rows'][index]
        self.selected = row['id']
        self.key.setText(row['memory']['fact_key'])
        self.text.setText(row['text'][:500])
        self.reason.clear()
        self.stable.setChecked(bool(row['memory']['stable']))
        self.save.setEnabled(row['memory']['status'] == 'active')
        change = row['change']
        details = f"记忆 #{row['id']} · {STATUS[row['memory']['status']]}\n保存时间：{row['created']}\n\n原文：\n{row['text']}"
        if change:
            details += (f"\n\n修改来源：{'自动整理' if change['actor']=='automatic' else '本人修改'}"
                        f"\n替代记忆：{change['previous_id']}\n依据：{change['reason']}\n证据：\n"
                        + json.dumps(change['evidence'], ensure_ascii=False, indent=2))
        else:
            details += '\n\n这是原始记录，未重新推断事实键。'
        self.details.setPlainText(details)

    def new_fact(self):
        self.selected = None
        self.rows.clearSelection()
        self.key.clear()
        self.text.clear()
        self.reason.clear()
        self.stable.setChecked(False)
        self.save.setEnabled(True)

    def revise(self):
        self.request('revise', memory_id=self.selected, key=self.key.text(), text=self.text.text(),
                     reason=self.reason.text(), stable=self.stable.isChecked())

    def show_trace(self):
        if not self.snapshot:
            return
        trace = self.snapshot.get('recall')
        lines = ['召回解释：']
        if trace:
            lines.extend([f"检索：{trace['query']} · 当时版本 {trace['revision']}", trace['note']])
            for row in trace['candidates']:
                lines.append(f"\n#{row['id']} {'选中' if row['selected'] else '未选中'} · {'+'.join(row['via'])}"
                             f"\n原始分 {row['base_score']:.5f} → 排序分 {row['score']:.5f}"
                             f" · 鲜活度 {row['freshness']:.0%}\n{row['reason']}\n{row['text']}")
        else:
            lines.append('尚未检索；可以输入一个话题。')
        lines.append('\n最近操作（revise=新增/更新，undo=撤销）：')
        for event in self.snapshot['events']:
            lines.append(f"{event['created']} · {event['action']} · {event['detail']}")
        self.details.setPlainText('\n'.join(lines))

    def failed(self, message):
        self.summary.setText(message + '\n操作未确认，不会自动重试；请刷新。')
        for widget in self.controls:
            widget.setEnabled(False)
        self.refresh.setEnabled(True)
