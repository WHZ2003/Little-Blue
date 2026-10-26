"""Install the maintained companion panel and its small read-only status summary."""
from patch_coopanion_majsoul import ROOT, replace

MARKER = '// DaFeiYu: companion UI v1'


def prepare_ui(changed):
    w = changed['src/world.ts']
    if MARKER not in w:
        w = MARKER + "\nimport { readFileSync } from 'node:fs';\n" + w
        w = replace(w, '  private companionTick(): void {', '''  private companionUiState() {
    const events = this.host?.store?.range({ source: 'desktop-pet', limit: 30 }) ?? [];
    const said = [...events].reverse().find(e => e.type === 'desktop-pet.self' && e.meta?.kind === 'say');
    const beats = said?.meta?.beats as Array<{ text?: string }> | undefined;
    let voiceEnabled: boolean | null = null;
    try {
      const config = JSON.parse(readFileSync(join(this.ctx.botDir, 'config.json'), 'utf8'));
      const voice = config.worlds?.fatfish;
      if (voice) voiceEnabled = voice.enabled !== false && voice.ttsEnabled !== false;
    } catch { /* Status must remain readable when optional config is unavailable. */ }
    return { version: 1, intervalSec: this.ctx.cfg.companionIntervalSec, windowHandle: this.ctx.cfg.nativeWindow || '',
      voiceEnabled, latestSpeech: said && beats ? { at: said.ts, text: beats.map(b => b.text || '').join(' ').slice(0, 2000) } : null };
  }
  private companionTick(): void {''')
        w = replace(w, 'return { companionSource:', 'return { companionUi: this.companionUiState(), companionSource:')
        w = replace(w, "lamps: [{ label: '牌局桥接', state: this.game.error ? 'error' : this.game.browserConnected ? 'online' : 'offline', hint: this.game.error || state.reason || '已接收公开牌局。' }],",
                    "lamps: this.nativeMode ? [{ label: '客户端陪玩', state: !this.ctx.cfg.companionMode ? 'offline' : this.native.status.error ? 'error' : this.native.status.watching ? 'online' : 'loading', hint: !this.ctx.cfg.companionMode ? '已停止观察' : this.native.status.error || (this.native.status.watching ? '正在观察 ' + this.native.status.windowTitle : '正在获取雀魂窗口') }] : [{ label: '牌局桥接', state: this.game.error ? 'error' : this.game.browserConnected ? 'online' : 'offline', hint: this.game.error || state.reason || '已接收公开牌局。' }],")
        w = replace(w, "panels: [{ id: 'game', title: '游戏状态', description: 'state 查询公开状态；connect 打开游戏；attach 附着桥接并保留页面。登录和开局由操作者完成。', getMethods: ['state'] }],", "panels: [{ id: 'game', title: '陪我玩', description: '选择客户端或网页，一键陪玩；只观察、聊天和建议，由你亲自操作。', getMethods: ['state'] }],")
        changed['src/world.ts'] = w
    changed['src/webui/client.js'] = (ROOT / 'integrations/majsoul/webui-client.js').read_text(encoding='utf-8')
    return changed
