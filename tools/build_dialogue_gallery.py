"""Render the fictional style library as a self-contained, searchable page."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    data = json.loads((ROOT / 'little_blue/dialogue_examples.json').read_text(encoding='utf-8'))
    groups = data['groups']
    count = sum(len(g['examples']) for g in groups)
    sections = []
    for group in groups:
        cards = []
        for n, (user, reply) in enumerate(group['examples'], 1):
            ident = f"{group['id']}.{n:02}"
            cards.append(f'<article><small>{ident}</small><p class="user">你：{html.escape(user)}</p>'
                         f'<p class="reply">她：{html.escape(reply)}</p></article>')
        sections.append(f'<section id="{group["id"]}"><h2>{html.escape(group["title"])}</h2>'
                        f'<div class="grid">{"".join(cards)}</div></section>')
    options = ''.join(f'<option value="{g["id"]}">{html.escape(g["title"])}</option>' for g in groups)
    page = '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeepSeek · 300 句相处语气</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f7fb;color:#233651;font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif}
main{max-width:1080px;margin:auto;padding:40px 24px}h1{font-size:30px;margin:0}h2{font-size:21px;margin:28px 0 12px}
.intro{max-width:800px;color:#50617a}.controls{position:sticky;top:0;background:#f3f7fbef;padding:14px 0;display:flex;gap:12px;flex-wrap:wrap}
input,select{font:inherit;padding:8px 12px;border:1px solid #afc3d9;border-radius:9px;background:white}input{flex:1;min-width:180px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}article{background:white;border:1px solid #dce6f0;border-radius:14px;padding:16px}
small{color:#7588a0}p{margin:8px 0}.user{color:#596b7c}.reply{color:#233e66}#count{color:#50617a} [hidden]{display:none!important}
</style><main><h1>DeepSeek · 相处语气范例</h1>
<p class="intro">COUNT 条宠物回复，20 个场景。每条配有用户原话，全部是虚构示例，不是你们真实发生过的经历。日常亲近，也允许认真、普通和安静。聊天每轮最多参考 3 条，学习语气，不背台词。</p>
<div class="controls"><input id="query" aria-label="搜索对话" placeholder="搜索：撒娇、作业、晚安……"><select id="group" aria-label="选择场景"><option value="">全部场景</option>OPTIONS</select><span id="count"></span></div>
SECTIONS
<p class="intro">真实用户要求、当前情境及能力限制优先。范例不写进 SQLite 记忆或向量索引；重启只保留最近参考过的编号，减少连续重复。</p>
</main><script>
const q=document.querySelector('#query'),g=document.querySelector('#group'),c=document.querySelector('#count');
function filter(){let total=0;for(const s of document.querySelectorAll('section')){let shown=0;for(const a of s.querySelectorAll('article')){const ok=(!g.value||g.value===s.id)&&a.textContent.toLowerCase().includes(q.value.toLowerCase());a.hidden=!ok;if(ok)shown++}s.hidden=!shown;total+=shown}c.textContent=total+' / COUNT 条'}
q.addEventListener('input',filter);g.addEventListener('change',filter);filter();
</script></html>'''
    page = page.replace('COUNT', str(count)).replace('OPTIONS', options).replace('SECTIONS', '\n'.join(sections))
    (ROOT / 'docs/dialogue-examples.html').write_text(page, encoding='utf-8')
    print(f'{count} replies in {len(groups)} scenes -> docs/dialogue-examples.html')


if __name__ == '__main__':
    build()
