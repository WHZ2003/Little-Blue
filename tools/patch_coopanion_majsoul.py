"""Apply the panel browser-start fix to the installed 1.1.1 World, with backups."""
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / '.research/Coopanion/data/extensions/node_modules/cortico-world-majsoul'
MARKER = '// DaFeiYu: dedicated-browser panel repair v1'


def navigation_fix(browser):
    browser = replace(browser,
        "this.disconnected('游戏页面已刷新，等待状态恢复。'); };",
        "this.disconnected(this.state.view.active ? '游戏页面已刷新，等待状态恢复。' : '浏览器桥接已就绪，等待手动登录并开始牌局。', this.state.view.active); };")
    browser = replace(browser, 'private disconnected(message: string): void',
                      'private disconnected(message: string, reportError = true): void')
    return replace(browser, 'this.state.block(message); this.error = message;',
                   "this.state.block(message); this.error = reportError ? message : ''; ")


def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Installed source differs; inspect before applying: ' + before[:80])
    return text.replace(before, after, 1)


def focus_fix(changed):
    world = changed['src/world.ts']
    if 'await this.game.showWindow()' not in world:
        world = replace(world,
            "    if (this.game.connected) return this.status();\n"
            "    if (!this.game.browserConnected) await ensureGameBrowser(this.ctx.dataDir, this.ctx.cfg.browserPort);\n"
            "    await this.game.connect(`http://127.0.0.1:${this.ctx.cfg.browserPort}`, this.ctx.cfg.gameUrl, method === 'connect');",
            "    if (method === 'connect') {\n"
            "      await ensureGameBrowser(this.ctx.dataDir, this.ctx.cfg.browserPort, true);\n"
            "      if (!this.game.browserConnected) await this.game.connect(`http://127.0.0.1:${this.ctx.cfg.browserPort}`, this.ctx.cfg.gameUrl, true);\n"
            "      await this.game.showWindow();\n"
            "      return this.status();\n"
            "    }\n"
            "    if (this.game.connected) return this.status();\n"
            "    if (!this.game.browserConnected) await ensureGameBrowser(this.ctx.dataDir, this.ctx.cfg.browserPort);\n"
            "    await this.game.connect(`http://127.0.0.1:${this.ctx.cfg.browserPort}`, this.ctx.cfg.gameUrl, false);")
    changed['src/world.ts'] = world
    browser = changed['src/browser.ts']
    if 'async showWindow()' not in browser:
        browser = replace(browser, '  async stop(): Promise<void> {',
            "  async showWindow(): Promise<void> {\n"
            "    const page = this.page;\n"
            "    if (!this.browserConnected || !page) throw new Error('游戏窗口尚未连接。');\n"
            "    const session = await page.context().newCDPSession(page);\n"
            "    try {\n"
            "      const { windowId, bounds } = await session.send('Browser.getWindowForTarget');\n"
            "      if (bounds.windowState === 'minimized') await session.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });\n"
            "      await page.bringToFront();\n"
            "    } finally { await session.detach(); }\n"
            "  }\n"
            "  async stop(): Promise<void> {")
    changed['src/browser.ts'] = browser
    changed['src/webui/client.js'] = changed['src/webui/client.js'].replace(
        'connect.disabled = loading || !!last?.connected || !!last?.busy;', 'connect.disabled = loading;')
    for target, source in [('src/browser-launch.ts', 'browser-launch.ts'), ('setup/start-browser.ps1', 'start-browser.ps1')]:
        changed[target] = (ROOT / 'integrations/majsoul' / source).read_text(encoding='utf-8')
    return changed


def save(changed):
    changed = {name: content for name, content in changed.items()
               if not (PACKAGE / name).exists() or (PACKAGE / name).read_text(encoding='utf-8') != content}
    if not changed:
        print('Installed panel repair already present')
        return
    backup = ROOT / 'backups' / ('majsoul-panel-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=True)
    for name, content in changed.items():
        path = PACKAGE / name
        if path.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
        temporary = path.with_suffix(path.suffix + '.dafeiyu-tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    print(json.dumps({'patched': list(changed), 'backup': str(backup)}))


def main():
    metadata = json.loads((PACKAGE / 'package.json').read_text(encoding='utf-8'))
    if metadata['version'] != '1.1.1':
        raise ValueError('This patch must be reviewed for a different plugin version')
    if MARKER in (PACKAGE / 'src/world.ts').read_text(encoding='utf-8'):
        changed = {name: (PACKAGE / name).read_text(encoding='utf-8')
                   for name in ('src/world.ts', 'src/browser.ts', 'src/webui/client.js')}
        if 'reportError = true' not in changed['src/browser.ts']:
            changed['src/browser.ts'] = navigation_fix(changed['src/browser.ts'])
        save(focus_fix(changed))
        return
    changed = {}
    world = (PACKAGE / 'src/world.ts').read_text(encoding='utf-8')
    world = MARKER + "\nimport { ensureGameBrowser } from './browser-launch.ts';\n" + world
    world = replace(world, 'return { connected: this.game.connected, busy:',
                    'return { browserConnected: this.game.browserConnected, connected: this.game.connected, busy:')
    world = replace(world, "this.game.connected ? 'online' : 'offline'", "this.game.browserConnected ? 'online' : 'offline'")
    world = replace(world, "    await this.game.connect(`http://127.0.0.1:${this.ctx.cfg.browserPort}`, this.ctx.cfg.gameUrl, method === 'connect');",
                    "    if (this.game.connected) return this.status();\n"
                    "    if (!this.game.browserConnected) await ensureGameBrowser(this.ctx.dataDir, this.ctx.cfg.browserPort);\n"
                    "    await this.game.connect(`http://127.0.0.1:${this.ctx.cfg.browserPort}`, this.ctx.cfg.gameUrl, method === 'connect');")
    changed['src/world.ts'] = world
    browser = (PACKAGE / 'src/browser.ts').read_text(encoding='utf-8')
    browser = replace(browser, '  get connected(): boolean',
                      '  get browserConnected(): boolean { return !!this.browser?.isConnected() && !!this.page && !this.page.isClosed(); }\n  get connected(): boolean')
    browser = replace(browser, "      if (navigate) await this.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });\n      else await this.page.evaluate(script);\n      this.publish();",
                      "      if (navigate || this.page.url() === 'about:blank') await this.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });\n"
                      "      else await this.page.evaluate(script);\n"
                      "      if (this.socket === null) { this.error = ''; this.state.block('浏览器桥接已就绪，等待手动登录并开始牌局。'); }\n"
                      "      this.publish();")
    changed['src/browser.ts'] = navigation_fix(browser)
    ui = (PACKAGE / 'src/webui/client.js').read_text(encoding='utf-8')
    ui = replace(ui, "{k: '牌局桥接', v: state.connected ? '已连接' : '未连接'},",
                 "{k: '游戏浏览器', v: state.browserConnected ? '已连接' : '未连接'},\n"
                 "              {k: '牌局桥接', v: state.connected ? '已连接' : state.browserConnected ? '已就绪，等待开局' : '未连接'},")
    changed['src/webui/client.js'] = ui
    changed['src/browser-launch.ts'] = (ROOT / 'integrations/majsoul/browser-launch.ts').read_text(encoding='utf-8')
    changed['setup/start-browser.ps1'] = (ROOT / 'integrations/majsoul/start-browser.ps1').read_text(encoding='utf-8')
    save(focus_fix(changed))


if __name__ == '__main__':
    main()
