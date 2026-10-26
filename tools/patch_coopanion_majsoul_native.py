"""Compose Windows client observation into the existing read-only companion patch."""
from patch_coopanion_majsoul import ROOT, replace

MARKER = '// DaFeiYu: native companion v1'


def prepare_native(changed):
    w = changed['src/world.ts']
    if MARKER not in w:
        w = MARKER + "\nimport { NativeCompanion, deepSeekDestination } from './native-companion.ts';\n" + w
        w = replace(w, '  readonly advisor = new CompanionAdvisor();', '''  readonly advisor = new CompanionAdvisor();
  readonly native: NativeCompanion;
  private get nativeMode(): boolean { return this.ctx.cfg.companionSource === 'native'; }''')
        w = replace(w, 'return !this.ctx.cfg.companionMode && this.ctx.cfg.allowActions;', 'return !this.nativeMode && !this.ctx.cfg.companionMode && this.ctx.cfg.allowActions;')
        w = replace(w, '  private companionTick(): void {', '''  private companionTick(): void {
    if (this.host && this.nativeMode && this.ctx.cfg.companionMode) {
      this.advisor.reset();
      void this.native.tick(this.host);
      return;
    }
    this.native.stop();''')
        constructor = '    this.player = new LocalPlayer(this.game, () => !!this.host && this.ctx.cfg.localStrategy && this.actionsAllowed, () => this.ctx.cfg.commandTimeoutSec * 1000, record => this.recordDecision(record));'
        w = replace(w, constructor, constructor + '''
    this.native = new NativeCompanion(() => !!this.host && this.nativeMode && this.ctx.cfg.companionMode,
      () => this.ctx.cfg.companionIntervalSec, () => this.ctx.cfg.nativeWindow || '',
      () => deepSeekDestination(this.ctx.botDir));''')
        w = replace(w, 'JSON.stringify({ companionMode:', "JSON.stringify({ companionSource: this.ctx.cfg.companionSource || 'browser', companionMode:")
        w = replace(w, 'return { companionMode:', "return { companionSource: this.ctx.cfg.companionSource || 'browser', nativeWatch: this.native.status, companionMode:")
        w = replace(w, 'advice: this.ctx.cfg.companionMode && this.game.connected', 'advice: !this.nativeMode && this.ctx.cfg.companionMode && this.game.connected')
        w = replace(w, '    this.game.onState = view => {', '    this.game.onState = view => {\n      if (this.nativeMode) return;')
        w = replace(w, 'async stop(): Promise<void> { clearInterval', 'async stop(): Promise<void> { this.native.stop(); clearInterval')
        w = replace(w, "    if (method === 'companion-on' || method === 'companion-off') {", '''    if (method === 'companion-native' || method === 'companion-browser') {
      this.ctx.cfg.companionSource = method === 'companion-native' ? 'native' : 'browser';
      this.native.stop();
      await this.ctx.persist({ companionSource: this.ctx.cfg.companionSource });
      return this.panel('game', 'companion-on');
    }
    if (method === 'companion-on' || method === 'companion-off') {
      this.native.stop();''')
        w = replace(w, "    const permitted = MAJSOUL_CONFIG_GROUP", '''    if (this.nativeMode) { this.companionTick(); return this.status(); }
    const permitted = MAJSOUL_CONFIG_GROUP''')
        w = replace(w, "          call.signal?.throwIfAborted();\n          if (this.game.connected)", "          call.signal?.throwIfAborted();\n          if (this.nativeMode) { this.companionTick(); return this.modelStatus(); }\n          if (this.game.connected)")
        w = replace(w, 'if (this.ctx.cfg.companionMode) throw new Error', 'if (this.nativeMode || this.ctx.cfg.companionMode) throw new Error')
        w = replace(w, '(!this.ctx.cfg.localStrategy && !this.ctx.cfg.companionMode) ||', '(!this.nativeMode && !this.ctx.cfg.localStrategy && !this.ctx.cfg.companionMode) ||')
        changed['src/world.ts'] = w
        cfg = changed['src/config.ts']
        cfg = replace(cfg, 'companionMode: boolean;', "companionMode: boolean; companionSource: 'browser' | 'native'; nativeWindow: string;")
        cfg = replace(cfg, 'companionMode: false,', "companionMode: false, companionSource: 'browser', nativeWindow: '',")
        cfg = replace(cfg, "    'worlds.majsoul.companionMode':", "    'worlds.majsoul.companionSource': { type: 'string', title: '陪玩画面来源', enum: ['browser', 'native'], 'x-hot': true, description: 'browser：专用 Edge；native：只截雀魂客户端窗口，发给当前官方 DeepSeek 看图，不操作游戏。' },\n    'worlds.majsoul.nativeWindow': { type: 'string', title: '客户端窗口句柄（可留空）', 'x-hot': true, description: '唯一雀魂客户端自动选择；多个窗口时按面板提示填写十六进制句柄。' },\n    'worlds.majsoul.companionMode':")
        changed['src/config.ts'] = cfg
        changed['src/definition.ts'] = replace(changed['src/definition.ts'], 'events: [', "events: [{ type: 'majsoul.native', description: '雀魂独立客户端窗口截图或观察故障；只看图聊天，不操作。', tags: ['snapshot'] }, ")
        changed['src/render.ts'] = replace(changed['src/render.ts'], '  const { observation: v, handAnalysis: a } = status;', '''  if (status.companionSource === 'native') return [
    '[雀魂客户端陪玩] companionMode=' + status.companionMode + ' allowActions=false',
    '窗口观察=' + JSON.stringify(status.nativeWatch),
    '此模式只读客户端截图，不使用网页协议手牌。根据最新 majsoul.native 图片用 pet_say 给一两句建议；看不清就说看不清，不打开浏览器，不调用鼠标键盘工具。',
  ].join('\\n');
  const { observation: v, handAnalysis: a } = status;''')
        ui = changed['src/webui/client.js']
        ui = replace(ui, '        function buttons() {', "        const native = ui.button('客户端陪玩（截图发 DeepSeek）', {onClick: () => void update('companion-native')});\n        const browser = ui.button('切回网页陪玩', {onClick: () => void update('companion-browser')});\n        function buttons() {\n          native.disabled = loading || !last?.companionSource || (last?.companionMode && last?.companionSource === 'native');\n          browser.disabled = loading || !last?.companionSource;\n          browser.hidden = last?.companionSource !== 'native';")
        ui = replace(ui, "            const view = state.observation;", "            const view = state.observation;\n            const isNative = state.companionSource === 'native';")
        ui = replace(ui, "              {k: '游戏浏览器',", "              {k: '陪玩来源', v: isNative ? '雀魂独立客户端（只看窗口）' : '专用 Edge 网页'},\n              ...(isNative ? [\n                {k: '游戏窗口', v: state.nativeWatch?.windowTitle || '等待识别雀魂客户端'},\n                {k: '观察状态', v: !state.companionMode ? '已暂停观察' : state.nativeWatch?.error || (state.nativeWatch?.watching ? '正在看图陪玩' : '正在获取窗口')},\n                {k: '最近观察', v: state.nativeWatch?.capturedAt ? new Date(state.nativeWatch.capturedAt).toLocaleTimeString() : '尚未收到画面'},\n                {k: '已投递画面', v: String(state.nativeWatch?.delivered || 0)},\n              ] : [\n              {k: '游戏浏览器',")
        ui = replace(ui, "              {k: '选牌方式',", "              ]),\n              {k: '选牌方式',")
        ui = replace(ui, "state.companionMode ? '看着呢，等新的牌局机会' : '未开启陪玩'", "state.companionMode ? isNative ? '根据最新窗口画面聊天和建议' : state.connected ? '等待新的牌局机会' : '未收到牌局，请附着专用网页' : '未开启陪玩'")
        ui = replace(ui, "              {k: '牌局状态',", "              ...(!isNative ? [\n              {k: '牌局状态',")
        ui = replace(ui, "              {k: '决策目标', v: `${state.decisionTargetMs / 1000} 秒`},", "              {k: '决策目标', v: `${state.decisionTargetMs / 1000} 秒`},\n              ] : []),")
        ui = replace(ui, 'message.textContent = state.error ||', 'message.textContent = (isNative ? state.nativeWatch?.error : state.error) ||')
        ui = replace(ui, 'actions.append(companion, refresh, connect, attach);', 'actions.append(companion, native, browser, refresh, connect, attach);')
        ui = replace(ui, '          connect.disabled = loading;', "          connect.disabled = loading || last?.companionSource === 'native';")
        ui = replace(ui, '          attach.disabled = loading || !!last?.busy;', "          attach.disabled = loading || !!last?.busy || last?.companionSource === 'native';")
        ui = replace(ui, '点击“陪我玩”，你打牌，她聊天和给建议。', '独立客户端请点“客户端陪玩”：只截雀魂窗口发给 DeepSeek，由你自己操作。退出陪玩即停止截图。')
        changed['src/webui/client.js'] = ui
    for name in ['native-companion.ts']:
        changed['src/' + name] = (ROOT / 'integrations/majsoul' / name).read_text(encoding='utf-8')
    changed['setup/native-capture.ps1'] = (ROOT / 'integrations/majsoul/native-capture.ps1').read_text(encoding='utf-8')
    return changed
