"""Render only our own handset widgets with synthetic game data."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from little_blue.phone import Fish
from little_blue.storage import Store
from little_blue.ui import Controller

root = Path(__file__).resolve().parents[1]
out = root / 'qa-output'
out.mkdir(exist_ok=True)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
c = Controller(app, Store(':memory:'), root / 'assets', out)
c.phone.data.puzzle.board = [[4, 8, 16, 32], [0, 4, 16, 128], [0, 2, 8, 256], [0, 0, 4, 512]]
c.phone.data.puzzle.score = 2460
c.phone.data.puzzle.pet_best = c.phone.data.puzzle.best = 3488
c.phone.data.collection = {'minnow': 3, 'goldfish': 2, 'moonfish': 1}
c.phone.data.catches = 5
c.phone.data.tank.fish = [Fish(species=k, age=2.3, growth=18) for k in c.phone.data.collection]
c.phone.data.journal = ['我自己玩 2048 的最高纪录提高到 3488 分。', '钓到了月光鱼，已收入收藏。', '给小鱼喂了食。']
c.show_phone()

def capture():
    for i, name in enumerate(('2048', 'fishing', 'aquarium')):
        c.phone_dialog.apps.setCurrentIndex(i)
        c.phone_dialog.refresh()
        app.processEvents()
        c.phone_dialog.grab().save(str(out / ('phone-' + name + '.png')))
    c.quit()

QTimer.singleShot(300, capture)
app.exec()
