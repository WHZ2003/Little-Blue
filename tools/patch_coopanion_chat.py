"""Reapply the narrow local speech patch after a portable Coopanion update."""
from pathlib import Path
import argparse
import shutil


def write_changed(path, content):
    """Replace the directory entry, never mutate a pnpm/shared hardlink target."""
    if path.exists() and path.read_bytes() == content:
        return
    temporary = path.with_name(path.name + '.dafeiyu-tmp')
    temporary.write_bytes(content)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app', type=Path, help='Coopanion portable application directory')
    args = parser.parse_args()
    source = args.app/'resources/app/node_modules/cortico-world-desktop-pet/src'
    world = source/'world.ts'
    text = world.read_text(encoding='utf-8')
    marker = "import { shortBeats, validQuestion } from './dafeiyu-chat-format.mjs';"
    if marker not in text:
        before = 'const { beats, dropped } = parseScript(script, this.vocab());'
        question = "const question = typeof args.question === 'string' ? args.question.trim() : '';"
        if text.count(before) != 1 or text.count(question) != 1:
            raise ValueError('Upstream changed; inspect before patching')
        backup = world.with_suffix('.ts.before-short-chat')
        if not backup.exists():
            shutil.copyfile(world, backup)
        text = marker+'\n'+text.replace(before, 'const { beats: originalBeats, dropped } = parseScript(script, this.vocab());\n    const beats = shortBeats(originalBeats);')
        text = text.replace(question, question+"\n    if (!validQuestion(question)) return { text: '请把问题改为不换行、最多30个字，不使用库这个口癖，再调用 pet_ask。', failed: true };")
        write_changed(world, text.encode('utf-8'))
    integration = Path(__file__).resolve().parents[1]/'integrations/coopanion'
    write_changed(source/'dafeiyu-chat-format.mjs', (integration/'chat-format.mjs').read_bytes())
    extension = args.app/'data/extensions/node_modules/cortico-world-dafeiyu-sync'
    if extension.is_dir():
        for name in ('index.mjs', 'chat-format.mjs', 'stickers.mjs'):
            target = extension/name
            write_changed(target, (integration/name).read_bytes())
    print('Local speech formatting patch installed')


if __name__ == '__main__':
    main()
