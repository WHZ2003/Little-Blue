"""Install the narrow pre-reply preference hook without replacing other patches."""
from pathlib import Path
import argparse
import shutil
from patch_coopanion_chat import write_changed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('app',type=Path)
    app=parser.parse_args().app
    persona=app/'resources/app/core/persona.ts'
    content=persona.read_text(encoding='utf-8')
    old='    override onDelivery(ctx: { events: EventEnvelope[] }): void {'
    new='''    override async onDelivery(ctx: { events: EventEnvelope[] }): Promise<void> {
      // DaFeiYu: finish the preference write before the model may promise success.
      const bridge = (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')];
      if (bridge?.before) {
        try {
          const note = await bridge.before(ctx.events);
          if (note) this.core?.injectInternal(note, 'notice');
        } catch {
          bridge.receipt = '偏好处理失败，未确认保存或生效，请稍后重试。';
          bridge.spoke = false;
          this.core?.injectInternal('偏好处理没有完成，不能声称保存或设置已生效。', 'notice');
        }
      }'''
    if 'DaFeiYu: finish the preference write' not in content:
        if content.count(old)!=1: raise ValueError('Persona delivery hook changed; inspect before patching')
        backup=persona.with_suffix('.ts.before-shared-preferences')
        if not backup.exists():shutil.copyfile(persona,backup)
        write_changed(persona,content.replace(old,new).encode())
    integration=Path(__file__).resolve().parents[1]/'integrations/coopanion'
    extension=app/'data/extensions/node_modules/cortico-world-dafeiyu-sync'
    if not extension.is_dir():raise ValueError('Shared memory extension is not installed')
    for name in ('index.mjs','preferences.mjs','video-watch.mjs','ENV_PROMPT.md','chat-format.mjs','package.json','webui.mjs'):
        write_changed(extension/name,(integration/name).read_bytes())
    speech=app/'resources/app/node_modules/cortico-world-desktop-pet/src/dafeiyu-chat-format.mjs'
    write_changed(speech,(integration/'chat-format.mjs').read_bytes())
    world=speech.with_name('world.ts')
    text=world.read_text(encoding='utf-8')
    record="    void this.record({ kind: 'say', beats: beats.map((b) => ({ text: b.text, ...moodOf(b.actions, this.vocab()) })) }, beats.map((b) => b.text).join(''), shown.blobs);"
    if 'DaFeiYu preference receipt history' not in text:
        if text.count(record)!=1:raise ValueError('Pet recording hook changed; inspect before patching')
        shutil.copyfile(world,world.with_suffix('.ts.before-shared-preferences'))
        text=text.replace(record,"    // DaFeiYu preference receipt history was already recorded before the model ran.\n    if (!(globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]?.historyReceipt)\n"+record)
        write_changed(world,text.encode())
    print('Installed shared preferences pre-reply hook and speech receipt guard')


if __name__=='__main__':main()
