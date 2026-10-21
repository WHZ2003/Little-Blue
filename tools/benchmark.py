"""Ten-second offscreen Qt throughput check with a pending cognitive request."""
import asyncio
import os
from pathlib import Path
import sys
import time
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication
from little_blue.storage import Store
from little_blue.ui import Controller

class Counter(QObject):
    paints = 0
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint:
            self.paints += 1
        return False

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
out = root / 'qa-output'
out.mkdir(exist_ok=True)
c = Controller(app, Store(out / 'benchmark.db'), root / 'assets', out)
counter = Counter()
c.pet.installEventFilter(counter)
c.settings.online = True
async def pending(*args):
    await asyncio.sleep(60)
c.worker.agent.client.complete = pending
c.submit('benchmark')
started = time.monotonic()
report = {}
def finish():
    elapsed = time.monotonic() - started
    report.update(seconds=round(elapsed,2), paint_events=counter.paints,
                  average_fps=round(counter.paints / elapsed,2), network_pending=c.busy)
    c.quit()
QTimer.singleShot(10000, finish)
app.exec()
import json
(out / 'benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
