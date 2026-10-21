"""Package approved image_gen outputs without redrawing or cropping them.

Input: generated/prompts.json with each item's original_png absolute path.
Preserves original PNGs and encodes WebP for the existing image transport.
"""
import hashlib
import html
import json
from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'memes'
CONTEXTS = {
    '901': '自己被夸奖或确有成绩时俏皮得意，不编造纪录',
    '902': '用户明确完成事情或确有成功结果时庆祝，不冒充任务完成',
    '903': '双方轻松玩笑中善意怀疑，不质疑用户的痛苦或真实求助',
    '904': '用户主动聊摸鱼或休息时接梗，不鼓动逃避重要任务',
    '905': '自己被善意逗弄时假装小委屈，不给用户负罪感',
    '906': '用户希望安静陪伴时温柔回应，不连续追问',
    '907': '自然打招呼，不重复打断正在进行的话题',
    '908': '用户道晚安或明确准备睡觉时，不假定用户已睡着',
    '909': '用户愿意接受的温柔安慰或主动求抱抱，认真回应而非玩笑',
    '910': '聊零食或吃饭时友好分享的角色表情，不声称实际送出食物',
    '911': '早晨打招呼，结合实际时间或用户的早安',
    '912': '开始一次自然交流或回应召唤，不重复自我介绍',
    '913': '中断后的自然续聊，不编造外出或实际经历',
    '914': '用户道别或结束聊天时，不强留或追问',
    '915': '用户明确说回来了或久别后重聊时欢迎',
    '916': '用户说忙完了或累了时体谅，不自动讲道理',
    '917': '确有必要确认的轻松回应，已决定沉默时不用它补发收到',
    '918': '轻松赞同且确实能答应时，不虚假承诺工具能力',
    '919': '确有进行中的任务时简短说明，不以图片假装已开始执行',
    '920': '确实需要思考的轻松话题，不代替应给出的答案',
    '921': '讨论努力过程或鼓励自己，不伪称后台任务正在执行',
    '922': '轻松玩笑或被用户逗笑，不嘲笑困难',
    '923': '双方都在开玩笑时夸张大笑，不用于认真求助',
    '924': '轻松好消息、期待或成功时开心',
    '925': '用户分享成果时鼓掌，不夸张吹捧',
    '926': '认可用户的努力或有趣想法时点赞',
    '927': '双方友善亲近的互动，适当表达喜欢',
    '928': '确有小成绩时俏皮求夸，不要求用户负责自己的情绪',
    '929': '被善意夸奖或亲近玩笑时害羞',
    '930': '轻松意外或惊讶，不以追问硬续聊',
    '931': '轻松困惑或双方玩梗，不嘲讽用户能力',
    '932': '意外但非危急的趣事，不夸大坏消息',
    '933': '用户邀请围观或轻松探头接话，不声称监控屏幕',
    '934': '一起吐槽小事时无语，不漠视用户感受',
    '935': '共同吐槽荒诞小事，不否定用户本人',
    '936': '被友善调侃时假装气鼓鼓，不真的生气或指责用户',
    '937': '聊困倦或角色确实困了时打哈欠',
    '938': '自己精力低或用户轻松说累瘫时，不用于真实健康问题',
    '939': '聊吃饭或角色轻松惦记食物时，不反复催用户',
    '940': '用户主动聊喝水或自然休息话题，不反复提醒说教',
    '941': '一起围观轻松趣事，不对真实灾难或痛苦幸灾乐祸',
}


def main():
    plan = json.loads((ROOT / 'generated/prompts.json').read_text(encoding='utf-8'))
    catalog_path = ROOT / 'catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    existing = {i['id']: i for i in catalog['items']}
    folder = ROOT / 'generated'
    originals = folder / 'originals'
    originals.mkdir(exist_ok=True)
    for item in plan['items']:
        ident = item['id']
        if ident not in CONTEXTS:
            raise ValueError('Unreviewed meme ID')
        if ident in existing and existing[ident].get('source') != 'generated':
            raise ValueError('Refusing to replace community asset')
        source = Path(item['original_png'])
        target = originals / (ident + '.png')
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        output = folder / (ident + '.webp')
        with Image.open(target) as im:
            im.save(output, format='WEBP', quality=92, method=6)
        data = output.read_bytes()
        if not 0 < len(data) <= 2 * 1024 * 1024:
            raise ValueError('Generated meme exceeds transport size')
        existing[ident] = {'id': ident, 'title': item['title'], 'source': 'generated',
            'group': item['group'], 'auto_context': CONTEXTS[ident], 'bytes': len(data),
            'git_blob_sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
    catalog['items'] = list(existing.values())
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    cards = []
    for item in catalog['items']:
        source = item.get('source', 'community')
        if source not in ('community', 'generated'):
            raise ValueError('Unknown gallery source')
        mode = '可自动选择' if item.get('auto_context') else '手动点发'
        cards.append(f'<figure><a href="{source}/{item["id"]}.webp"><img loading="lazy" '
                     f'src="{source}/{item["id"]}.webp" alt="{html.escape(item["title"], quote=True)}"></a>'
                     f'<figcaption><b>{item["id"]} · {html.escape(item["title"])}</b>'
                     f'<small>{mode} · /meme {item["id"]}</small></figcaption></figure>')
    (ROOT / 'gallery.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>DeepSeek 表情库</title>'
        '<style>body{font:16px system-ui;margin:32px;background:#f2f7fc;color:#193351}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}'
        'figure{margin:0;background:white;padding:12px;border-radius:16px}img{width:100%;aspect-ratio:1;object-fit:contain}'
        'small{display:block;margin-top:6px;color:#52677e}h1{margin-bottom:8px}p{margin-bottom:28px}</style>'
        f'<h1>DeepSeek 表情库 · {len(existing)} 张</h1><p>点击查看原图。在微信输入 /meme 编号可点发。'
        '自动配图按情绪选择，至少间隔 1 分钟、每小时最多 8 张。</p><main>'
        + ''.join(cards) + '</main></html>', encoding='utf-8')
    print(f"Registered {len(plan['items'])} generated memes; total {len(existing)}.")


if __name__ == '__main__':
    main()
