"""Render the real feeding clip at desktop size and make a standalone player."""
import base64
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from little_blue.models import Action
from little_blue.sprites import SpriteBank


def main():
    app = QApplication.instance() or QApplication([])
    folder = ROOT / 'assets/blue'
    output = ROOT / 'qa-output/feed-preview'
    output.mkdir(parents=True, exist_ok=True)
    bank = SpriteBank(folder / 'manifest.json', size=180)
    # Capture precisely the pixmaps used by the real renderer, on light/dark backgrounds.
    times = [0, .2, .4, .6, 1.0, 1.6, 2.0, 2.4]
    strip = QPixmap(10 * 180, 2 * 210)
    painter = QPainter(strip)
    for row, bg in enumerate(('#edf2f7', '#162238')):
        painter.fillRect(0, row * 210, strip.width(), 210, QColor(bg))
        painter.setPen(QColor('#24354b' if row == 0 else '#d8e6f7'))
        for col, (action, elapsed) in enumerate([(Action.IDLE, 0)] + [(Action.FEED, t) for t in times] + [(Action.IDLE, 0)]):
            pix = bank.frame(action, elapsed)
            painter.drawPixmap(col * 180 + (180 - pix.width()) // 2, row * 210 + 180 - pix.height(), pix)
            painter.drawText(col * 180 + 8, row * 210 + 200, 'idle' if action == Action.IDLE else f'{elapsed:.1f}s')
    painter.end()
    strip.save(str(output / 'contact.png'))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    feed = manifest['animations']['feed']
    def data_url(path):
        return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
    from PySide6.QtCore import QBuffer, QIODevice
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    bank.frame(Action.IDLE, 0).save(buffer, 'PNG')
    data = {'feed': data_url(folder / feed['sheet']), 'rects': feed['rects'], 'fps': feed['fps'],
            'idle': 'data:image/png;base64,' + base64.b64encode(bytes(buffer.data())).decode()}
    html = '''<!doctype html><meta charset="utf-8"><title>大肥鱼 · 喂食动画检查</title>
<style>body{background:#111d30;color:#e7f1ff;font:16px system-ui;margin:32px}h1{font-size:24px}
.pair{display:flex;gap:24px;flex-wrap:wrap}canvas{border-radius:16px}button{padding:10px 16px;margin:16px 8px 16px 0;cursor:pointer}
input{width:min(350px,80vw)}small{color:#a9bfd7}</style>
<h1>喂食 · 小饼干</h1><p>180 px 实际桌宠尺寸 · 接过 → 咬一口 → 咀嚼 → 收手 · 3.2 秒</p>
<div class="pair"><canvas width="300" height="220"></canvas><canvas width="300" height="220"></canvas></div>
<button id="play">暂停</button><button id="step">下一帧</button><button id="restart">重新喂食</button>
<label><input id="flip" type="checkbox" style="width:auto">向左</label>
<p><input id="seek" type="range" min="0" max="4.4" step="0.2" value="0"><span id="label"></span></p>
<small>两侧分别检查浅色／深色背景。前后各 0.6 秒为原始 idle；不连接微信、模型或记忆库。</small>
<script>const data=DATA; const images=[new Image(),new Image()];images[0].src=data.idle;images[1].src=data.feed;
const canvases=[...document.querySelectorAll('canvas')];let elapsed=0,playing=true,last=performance.now();
function render(){const feeding=elapsed>=.6&&elapsed<3.8, frame=Math.min(15,Math.floor(Math.max(0,elapsed-.6)*data.fps+1e-7));
canvases.forEach((cv,i)=>{const c=cv.getContext('2d');c.fillStyle=i?'#17263d':'#edf2f7';c.fillRect(0,0,300,220);c.save();
c.translate(60,20);if(document.querySelector('#flip').checked){c.translate(180,0);c.scale(-1,1)}
if(feeding&&images[1].complete){const r=data.rects[frame],s=180/Math.max(r[2],r[3]),w=r[2]*s,h=r[3]*s;c.drawImage(images[1],...r,(180-w)/2,180-h,w,h)}
else if(images[0].complete){const im=images[0];c.drawImage(im,(180-im.width)/2,180-im.height)}c.restore()});
document.querySelector('#label').textContent=feeding?`喂食 · 第 ${frame+1}/16 拍`:'原始 idle';document.querySelector('#seek').value=elapsed;}
function tick(now){if(playing)elapsed=(elapsed+Math.min(.05,(now-last)/1000))%4.4;last=now;render();requestAnimationFrame(tick)}
document.querySelector('#play').onclick=()=>{playing=!playing;document.querySelector('#play').textContent=playing?'暂停':'播放'};
document.querySelector('#step').onclick=()=>{playing=false;elapsed=(elapsed+.2)%4.4;document.querySelector('#play').textContent='播放';render()};
document.querySelector('#restart').onclick=()=>{elapsed=0;playing=true;document.querySelector('#play').textContent='暂停'};
document.querySelector('#seek').oninput=e=>{playing=false;elapsed=Number(e.target.value);document.querySelector('#play').textContent='播放';render()};
requestAnimationFrame(tick);</script>'''.replace('DATA', json.dumps(data))
    (output / 'index.html').write_text(html, encoding='utf-8')
    print(output / 'index.html')


if __name__ == '__main__':
    main()
