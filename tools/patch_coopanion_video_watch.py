"""Add the restricted WeChat playback bridge after the owned-tab cleanup patch."""
import argparse
from pathlib import Path
from patch_coopanion_video_cleanup import patch,write_atomic,ROOT
from patch_coopanion_storyboard import prepare_video, prepare_video_ui
import shutil
import time
import json


def install(package):
    if json.loads((package / 'package.json').read_text(encoding='utf-8'))['version'] != '0.2.2':
        raise ValueError('Review the video/storyboard integration for this plugin version first')
    patch(package)
    src=package/'src';path=src/'world.ts';text=path.read_text(encoding='utf-8')
    browser=src/'browser.ts';body=browser.read_text(encoding='utf-8')
    if 'async freshOwnedPage()' not in body:
        needle='  ownershipTicket() {'
        if body.count(needle)!=1:raise ValueError('Video browser ownership hook changed')
        body=body.replace(needle,'''  async freshOwnedPage() {
    // Never reuse an existing page for a remote request, even if its URL matches.
    this.page = null; this.owned = null;
    await this.cdp?.detach().catch(() => {}); this.cdp = null;
    return this.ensure();
  }
  ownershipTicket() {''')
        write_atomic(browser,body.encode())
    def replace(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError('Video world changed: '+old[:80])
        text=text.replace(old,new)
    if 'DaFeiYu WeChat video bridge v1' not in text:
        replace("import QRCode from 'qrcode';", "import QRCode from 'qrcode';\n// DaFeiYu WeChat video bridge v1\nimport { RemoteWatch, VIDEO_KEY } from './remote-watch.ts';")
        replace("  private mode: Mode = 'idle';", "  private mode: Mode = 'idle';\n  private remote = new RemoteWatch(this);")
        replace('    this.startError = null;\n    await this.refreshIdentity();', '    this.startError = null;\n    (globalThis as any)[VIDEO_KEY] = this.remote;\n    await this.refreshIdentity();')
        replace('  async stop(): Promise<void> {\n    this.stopObserving();', '  async stop(): Promise<void> {\n    if ((globalThis as any)[VIDEO_KEY] === this.remote) delete (globalThis as any)[VIDEO_KEY];\n    await this.remote.cancel();\n    this.stopObserving();')
        replace('  private async pollOnce(): Promise<void> {', '  private async pollOnce(): Promise<void> {\n    if (this.remote.active) return;')
        replace("  private async openBrowser(by: 'operator' | 'bot'): Promise<string> {", "  private async openBrowser(by: 'operator' | 'bot'): Promise<string> {\n    if (this.remote.active) return '正在执行微信观看任务，请等待结束或先关闭观看。';")
        replace("  private async closeBrowser(by: 'operator' | 'bot'): Promise<string> {", "  private async closeBrowser(by: 'operator' | 'bot'): Promise<string> {\n    await this.remote.cancel();")
        replace('      handler: async (args: Args) => {\n        try {', '''      handler: async (args: Args) => {
        try {
          if (this.remote.active) {
            if (['bilibili_video_exit','bilibili_video_pause'].includes(decl.name) || (decl.name==='bilibili_video_browser' && args.action==='close')) {
              await this.remote.cancel();
              return {text:'已中止微信发起的观看任务；结果由实际回执返回微信。'};
            }
            return {text:'正在执行微信观看任务，请等待结束或先退出观看。',failed:true};
          }''')
        write_atomic(path,text.encode('utf-8'))
    write_atomic(src/'remote-watch.ts',(ROOT/'integrations/bilibili-video/remote-watch.ts').read_bytes())
    prompt=src/'ENV_PROMPT.md';body=prompt.read_text(encoding='utf-8')
    if '微信观看任务通道' not in body:
        body+='\n\n微信观看任务通道会在桌面在线且视频插件空闲时自动打开专用视频页并播放，以实际播放器进度报告开始和结束。此时不得重复开视频或自行回微信说看完；只读字幕不等于观看。用户接管、断线、打不开或播放卡住会报告失败，不重复执行旧任务。\n'
        write_atomic(prompt,body.encode())
    names = ['src/world.ts', 'src/browser.ts', 'src/config.ts', 'src/ENV_PROMPT.md']
    prepared = prepare_video_ui(package, prepare_video({n: (package / n).read_text(encoding='utf-8') for n in names}))
    backup = ROOT / 'backups' / ('video-storyboard-' + str(time.time_ns()))
    for name, content in prepared.items():
        dest = package / name
        if dest.exists() and dest.read_text(encoding='utf-8') == content:
            continue
        if dest.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(dest, content.encode('utf-8'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True)
    install(parser.parse_args().package);print('Installed WeChat video bridge')
