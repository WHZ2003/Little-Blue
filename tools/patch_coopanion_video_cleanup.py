"""Install ownership-safe video cleanup in a candidate or the stopped local plugin."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def write_atomic(path, content):
    temporary = path.with_name(path.name + '.owned-video-tmp')
    temporary.write_bytes(content)
    temporary.replace(path)


def patch(package):
    src = package / 'src'
    browser = (src / 'browser.ts').read_text(encoding='utf-8')
    world = (src / 'world.ts').read_text(encoding='utf-8')
    if 'DaFeiYu owned video pages v1' in browser:
        if 'DaFeiYu finish owned video v1' not in world:
            raise ValueError('Partial patch; inspect before continuing')
        return
    def replace(text, old, new):
        if text.count(old) != 1:
            raise ValueError('Upstream changed: '+old[:100])
        return text.replace(old, new)
    browser = replace(browser, "import puppeteer,", "// DaFeiYu owned video pages v1\nimport { OwnedVideoPage } from './owned-page.ts';\nimport puppeteer,")
    browser = replace(browser, '  private launched = false;', '  private owned: OwnedVideoPage | null = null;')
    start = browser.index('  async stop(): Promise<void> {')
    end = browser.index('  /* ---------- connection ---------- */', start)
    browser = browser[:start] + '''  ownershipTicket() { return this.owned?.ticket() ?? null; }
  ticketMatches(ticket: ReturnType<VideoBrowser['ownershipTicket']>) { return !!this.owned?.matches(ticket); }
  releaseToOperator() { this.owned?.revoke(); }

  async finishOwnedVideo(ticket: ReturnType<VideoBrowser['ownershipTicket']>) {
    const owned = this.owned;
    if (!owned || !ticket || !owned.matches(ticket)) return 'retained';
    const result = await owned.close(ticket);
    if (result === 'closed' && this.owned === owned) {
      this.page = null;
      this.owned = null;
      await this.cdp?.detach().catch(() => {});
      this.cdp = null;
    }
    return this.owned && this.owned !== owned ? 'retained' : result;
  }

  async stop(): Promise<void> {
    await this.starting?.catch(() => {});
    const b = this.browser;
    const ticket = this.ownershipTicket();
    if (ticket) await this.finishOwnedVideo(ticket);
    this.browser = null;
    this.page = null;
    this.owned = null;
    await this.cdp?.detach().catch(() => {});
    this.cdp = null;
    // Never close the browser or kill it: even a launched browser can now contain user tabs.
    if (b) await b.disconnect().catch(() => {});
    this.process?.unref();
    this.process = null;
  }

''' + browser[end:]
    browser = replace(browser, "    if (this.cfg.cdpUrl) {", "    if (this.browser?.connected) {\n      browser = this.browser;\n    } else if (this.cfg.cdpUrl) {")
    browser = replace(browser, '      this.launched = false;\n', '')
    browser = replace(browser, '      this.launched = true;\n', '')
    browser = replace(browser, '        proc.kill();', '        proc.unref(); // Unknown/restored tabs must survive even a failed connection.')
    browser = replace(browser, "        'about:blank',", "        '--no-startup-window',")
    browser = replace(browser, "    browser.on('disconnected', () => {", "    if (this.browser !== browser) browser.on('disconnected', () => {")
    browser = replace(browser, "    const pages = (await browser.pages()).filter((p) => !p.url().startsWith('devtools://') && !p.isClosed());\n    const page = pages[0] ?? await browser.newPage();\n    for (const extra of pages.slice(1)) await extra.close().catch(() => {});", "    // Existing/restored tabs and popups are never adopted or closed.\n    await this.cdp?.detach().catch(() => {});\n    const page = await browser.newPage();\n    this.page = page;\n    const owned = this.owned = new OwnedVideoPage(page);\n    try { await owned.observeUser(); } catch { owned.revoke(); }\n    page.on('close', () => {\n      if (this.page !== page || owned.closing) return;\n      this.page = null; this.owned = null;\n      this.opts.onLost();\n    });")
    browser = replace(browser, '      if (frame !== page.mainFrame()) return;', '      if (this.page !== page || frame !== page.mainFrame()) return;')
    browser = replace(browser, '  private ownNavUntil = 0;', "  private ownNavUntil = 0;\n  private ownNavTarget = '';\n  private sameNavigation(url: string) {\n    try {\n      const a = new URL(url), b = new URL(this.ownNavTarget);\n      return a.origin === b.origin && a.pathname.replace(/\\/$/, '') === b.pathname.replace(/\\/$/, '') && (a.searchParams.get('p') || '1') === (b.searchParams.get('p') || '1');\n    } catch { return false; }\n  }")
    browser = replace(browser, '      const byWorld = Date.now() < this.ownNavUntil;', '      const byWorld = Date.now() < this.ownNavUntil && this.sameNavigation(url);\n      this.owned?.navigated(byWorld);')
    browser = replace(browser, "    cdp.on('Network.responseReceived', (e) => {", "    cdp.on('Network.responseReceived', (e) => {\n      if (this.page !== page) return;")
    browser = replace(browser, "    cdp.on('Network.loadingFinished', (e) => {", "    cdp.on('Network.loadingFinished', (e) => {\n      if (this.page !== page) return;")
    browser = replace(browser, '  async goto(url: string): Promise<void> {\n    const page = await this.ensure();', '''  async goto(url: string): Promise<void> {
    // A human-touched page remains theirs, including when the bot later opens another video.
    if (this.owned && !this.owned.eligible) this.page = null;
    const page = await this.ensure();''')
    browser = replace(browser, '    try {\n      await page.goto(url,', '    this.ownNavTarget = url;\n    try {\n      await page.goto(url,')
    world = replace(world, "import QRCode from 'qrcode';", "import QRCode from 'qrcode';\n// DaFeiYu finish owned video v1\nimport { autoCloseVideoEnabled } from './owned-page.ts';")
    world = replace(world, '    try {\n      await this.browser.goto(HOME_URL);', "    try {\n      await this.browser.goto(HOME_URL);\n      if (by === 'operator') this.browser.releaseToOperator();")
    world = replace(world, "    return '浏览器窗口已关闭';", "    return '已释放视频浏览器；只清理仍归我管理的标签页，用户页面保留。';")
    world = replace(world, '  private async poll(): Promise<void> {', '''  private polling = false;
  private async poll(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    try { await this.pollOnce(); }
    catch (err) { this.log?.warn('video poll failed', { err: String(err) }); }
    finally { this.polling = false; }
  }

  private async finishVideo(ticket: ReturnType<VideoBrowser['ownershipTicket']>): Promise<string> {
    if (!autoCloseVideoEnabled()) return '按当前偏好保留页面。';
    const result = await this.browser.finishOwnedVideo(ticket);
    if (result === 'closed') {
      this.mode = 'idle'; this.currentBvid = null; this.lastPlayback = null; this.resetVideo();
      return '已自动关闭我自己打开的视频标签页。';
    }
    return result === 'failed' ? '关闭视频标签页失败，页面仍保留。' : '页面已由用户接管或归属无法确认，已保留。';
  }

  private async pollOnce(): Promise<void> {''')
    world = replace(world, "    if (this.mode === 'idle' || !this.host) return;\n    const pb = await this.browser.playback();", "    if (this.mode === 'idle' || !this.host) return;\n    const ticket = this.browser.ownershipTicket();\n    const mode = this.mode;\n    const pb = await this.browser.playback();\n    if (mode !== this.mode || !this.browser.ticketMatches(ticket)) return;")
    world = replace(world, "        this.pushState(`[视频] 《${this.video?.title ?? ''}》播放完了(${fmt(pb.duration)})。`);", "        const title = this.video?.title ?? '';\n        const cleanup = await this.finishVideo(ticket);\n        this.pushState(`[视频] 《${title}》播放完了(${fmt(pb.duration)})。${cleanup}`);")
    world = replace(world, "    const pb = await this.browser.playback();\n    await this.leaveWatching();\n    return { text: `已退出观看,停在 ${fmt(pb?.t ?? 0)},页面留在这个视频。` };", "    const ticket = this.browser.ownershipTicket();\n    const pb = await this.browser.playback();\n    await this.leaveWatching();\n    const cleanup = await this.finishVideo(ticket);\n    return { text: `已退出观看,停在 ${fmt(pb?.t ?? 0)}。${cleanup}` };")
    for name, text in [('browser.ts', browser), ('world.ts', world)]:
        path = src / name
        backup = path.with_suffix('.ts.before-owned-video')
        if not backup.exists(): shutil.copy2(path, backup)
        write_atomic(path, text.encode('utf-8'))
    write_atomic(src / 'owned-page.ts', (ROOT / 'integrations/bilibili-video/owned-page.ts').read_bytes())
    prompt = src / 'ENV_PROMPT.md'
    text = prompt.read_text(encoding='utf-8')
    text = text.replace('`bilibili_video_exit` 回到逛页面。', '`bilibili_video_exit` 结束观看；开启自动清理时关闭自己的标签页。')
    text += '\n\n大肥鱼视频清理规则：开启共享偏好 auto_close_video_tabs 后，视频自然播放结束或 bilibili_video_exit 会自动关闭本插件新建且未被用户接管的标签页。暂停不关闭。关闭结果以状态事件/工具回执为准，失败不能说已关。只读字幕工具不开可见网页，不需要关。用户已有标签页、用户新建的页和接管后的页始终保留；不得通过关闭整个浏览器或杀进程来清理。\n'
    write_atomic(prompt,text.encode('utf-8'))
    tools = src / 'tools.ts'
    text = tools.read_text(encoding='utf-8')
    text = replace(text, '退出观看:退出网页全屏、暂停,回到可以逛页面的状态;页面留在这个视频。', '结束观看：退出全屏并暂停；若 auto_close_video_tabs 开启，关闭自己创建且未被用户接管的标签页，否则保留。以回执为准。')
    text = replace(text, '打开或关闭 B 站浏览器窗口(观众看到的画面)。窗口默认是关着的;open 打开并停在首页,close 关掉。操作员也能在 WebUI 开关,开关时你会收到事件。', '打开自己的 B 站标签页；close 只清理自己创建且未被用户接管的标签页并断开控制，保留用户网页，不关闭整个浏览器。')
    write_atomic(tools,text.encode('utf-8'))
    describe = package / 'describe.json'
    if describe.exists():
        import json
        data = json.loads(describe.read_text(encoding='utf-8'))
        for tool in data.get('tools', []):
            if tool['name'] == 'bilibili_video_exit': tool['description'] = '结束观看；根据共享偏好清理自己创建且未被用户接管的标签页，以回执为准。'
            if tool['name'] == 'bilibili_video_browser': tool['description'] = '仅打开或清理本插件自己的标签页，保留用户页面，不关闭整个浏览器。'
        for item in data.get('prompts', []):
            if 'content' in item: item['content'] = prompt.read_text(encoding='utf-8')
        write_atomic(describe,json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--package', required=True, type=Path)
    args=parser.parse_args()
    patch(args.package)
    print('Installed owned-page video cleanup')
