"""Self-contained, side-by-side sprite playback (no desktop capture or network)."""
import base64
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    raw = path.read_bytes()
    width, height = struct.unpack('>II', raw[16:24])
    return 'data:image/png;base64,' + base64.b64encode(raw).decode(), width, height


def main():
    folder = ROOT / 'assets/blue'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    old, w, _ = source(folder / 'atlas-v3.png')
    config = manifest['animations']['walk']
    new, _, _ = source(folder / config.get('sheet', manifest['sheet']))
    old_rects = [[int(i * w / 4), 245, w // 4, 209] for i in range(4)]
    data = [{'image': old, 'rects': old_rects, 'fps': 8},
            {'image': new, 'rects': config.get('rects', old_rects), 'fps': config['fps']}]
    html = '''<!doctype html><meta charset="utf-8"><title>DeepSeek · 步态对照</title>
<style>body{background:#122239;color:#e1f2ff;font:16px system-ui;margin:32px}
.pair{display:flex;gap:20px;flex-wrap:wrap}section{background:#243b52;border-radius:16px;padding:20px}
canvas{background:#e8eef4;border-radius:12px;display:block}button,input{margin:12px}
small{color:#b2c5dc}h1{font-size:24px}</style>
<h1>走路动画：原版 / 调整后</h1><p>同为 180 px 桌面尺寸。可暂停或逐帧检查收腿、落脚和循环接点。</p>
<button id="pause">暂停</button><button id="step">下一帧</button>
<label><input id="move" type="checkbox">显示水平移动</label>
<label>播放速度 <input id="rate" type="range" min="0.25" max="2" step="0.25" value="1"><span id="speed">1×</span></label>
<div class="pair"><section><h2>原版 · 4 帧</h2><canvas width="460" height="240"></canvas><p class="frame"></p></section>
<section><h2>调整后</h2><canvas width="460" height="240"></canvas><p class="frame"></p></section></div>
<p><small>这是本地素材预览，不连接 DeepSeek、微信或正式记忆库。</small></p>
<script>const clips=DATA;let elapsed=0,last=performance.now(),playing=true;
const canvases=[...document.querySelectorAll('canvas')],labels=[...document.querySelectorAll('.frame')];
const images=clips.map(c=>{let i=new Image();i.src=c.image;return i});
function render(){clips.forEach((c,i)=>{const cv=canvases[i],ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);
const f=Math.floor(elapsed*c.fps+1e-8)%c.rects.length,r=c.rects[f],scale=180/Math.max(r[2],r[3]);
const width=r[2]*scale,height=r[3]*scale,x=document.querySelector('#move').checked?10+(elapsed*48)%250:140;
ctx.strokeStyle='#9caec0';ctx.beginPath();ctx.moveTo(0,221);ctx.lineTo(460,221);ctx.stroke();
if(images[i].complete)ctx.drawImage(images[i],...r,x+(180-width)/2,220-height,width,height);
labels[i].textContent=`第 ${f+1} / ${c.rects.length} 帧 · ${c.fps} FPS`})}
function tick(now){if(playing)elapsed+=Math.min((now-last)/1000,.05)*Number(document.querySelector('#rate').value);last=now;render();requestAnimationFrame(tick)}
document.querySelector('#pause').onclick=()=>{playing=!playing;document.querySelector('#pause').textContent=playing?'暂停':'播放'};
document.querySelector('#step').onclick=()=>{playing=false;document.querySelector('#pause').textContent='播放';elapsed+=1/clips[1].fps;render()};
document.querySelector('#rate').oninput=e=>document.querySelector('#speed').textContent=e.target.value+'×';requestAnimationFrame(tick);
</script>'''.replace('DATA', json.dumps(data))
    out = ROOT / 'qa-output/walk-preview.html'
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
