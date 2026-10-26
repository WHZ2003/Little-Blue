"""Read-only companion-mode additions, composed by the strategy installer."""
from patch_coopanion_majsoul import ROOT, replace

MARKER = '// DaFeiYu: companion mode v1'


def prepare_companion(changed):
    w = changed['src/world.ts']
    if MARKER not in w:
        w = MARKER + "\nimport { CompanionAdvisor } from './companion-advisor.ts';\n" + w
        w = replace(w, '  private host?: WorldHost;', '''  private host?: WorldHost;
  readonly advisor = new CompanionAdvisor();
  private companionTimer?: ReturnType<typeof setInterval>;
  private get actionsAllowed(): boolean { return !this.ctx.cfg.companionMode && this.ctx.cfg.allowActions; }
  private companionTick(): void {
    if (!this.host || !this.ctx.cfg.companionMode || !this.game.connected) { this.advisor.reset(); return; }
    const view = this.game.snapshot();
    if (!this.advisor.shouldSpeak(view, this.ctx.cfg.companionIntervalSec)) return;
    this.host.pushDeferred({ type: 'majsoul.advice', senderKey: this.id, tags: ['snapshot'], render: () => {
      if (!this.host || !this.ctx.cfg.companionMode || !this.game.connected) return null;
      return '陪我玩：用户亲自操作，你只聊天和建议。以下是投递时的最新牌局；没有当前建议时不要沿用旧建议。\\n' + this.renderState();
    } }, { trigger: 'debounce' });
  }''')
        w = replace(w, 'new GameBrowser(() => ctx.cfg.allowActions,', 'new GameBrowser(() => this.actionsAllowed,')
        w = replace(w, '!!this.host && this.ctx.cfg.localStrategy && this.ctx.cfg.allowActions,', '!!this.host && this.ctx.cfg.localStrategy && this.actionsAllowed,')
        w = replace(w, "if (record.outcome !== 'confirmed'", "if (this.ctx.cfg.companionMode || record.outcome !== 'confirmed'")
        w = replace(w, 'JSON.stringify({ localStrategy: this.ctx.cfg.localStrategy, actions: this.ctx.cfg.allowActions,', 'JSON.stringify({ companionMode: !!this.ctx.cfg.companionMode, localStrategy: this.ctx.cfg.localStrategy, actions: this.actionsAllowed,')
        w = replace(w, '!this.ctx.cfg.localStrategy && !!this.host && this.ctx.cfg.allowActions', '!this.ctx.cfg.localStrategy && !!this.host && this.actionsAllowed')
        w = replace(w, "value: this.ctx.cfg.allowActions ? '允许' : '暂停', tone: this.ctx.cfg.allowActions ? 'on' : 'off'", "value: this.ctx.cfg.companionMode ? '陪我玩 · 只建议' : this.actionsAllowed ? '允许' : '暂停', tone: this.actionsAllowed ? 'on' : 'off'")
        w = replace(w, "description: '先操作后短句解说、公开状态和回执。'", "description: '陪玩建议、自动模式解说、公开状态和回执。'")
        w = replace(w, "return { controller: this.ctx.cfg.localStrategy ? 'local' : 'legacy-model',", "return { companionMode: !!this.ctx.cfg.companionMode, advice: this.ctx.cfg.companionMode && this.game.connected ? this.advisor.current(observation) : null, controller: this.ctx.cfg.companionMode ? 'companion' : this.ctx.cfg.localStrategy ? 'local' : 'legacy-model',")
        w = replace(w, 'error: this.game.error, allowActions: this.ctx.cfg.allowActions,', 'error: this.game.error, allowActions: this.actionsAllowed,')
        w = replace(w, '    this.player.start();', '''    this.player.start();
    clearInterval(this.companionTimer);
    this.companionTimer = setInterval(() => this.companionTick(), 500);
    this.companionTimer.unref?.();
    this.companionTick();''')
        w = replace(w, '      this.player.tick();\n      const fingerprint', '      this.player.tick();\n      this.companionTick();\n      const fingerprint')
        w = replace(w, "{ trigger: !this.ctx.cfg.localStrategy && this.ctx.cfg.allowActions && view.ready", "{ trigger: this.ctx.cfg.companionMode ? 'piggyback' : !this.ctx.cfg.localStrategy && this.actionsAllowed && view.ready")
        w = replace(w, 'async stop(): Promise<void> { this.player.stop();', 'async stop(): Promise<void> { clearInterval(this.companionTimer); this.advisor.reset(); this.player.stop();')
        w = replace(w, "    if (method === 'state') return this.status();", '''    if (method === 'state') return this.status();
    if (method === 'companion-on' || method === 'companion-off') {
      this.ctx.cfg.companionMode = method === 'companion-on';
      this.ctx.cfg.allowActions = false;
      this.player.tick();
      this.advisor.reset();
      await this.ctx.persist({ companionMode: this.ctx.cfg.companionMode, allowActions: false });
      this.companionTick();
      return this.status();
    }''')
        w = replace(w, "if (!this.ctx.cfg.allowActions) throw new Error('游戏操作已暂停，不能自动重连。');", "if (!this.ctx.cfg.companionMode && !this.actionsAllowed) throw new Error('游戏操作已暂停，不能自动重连。');")
        w = replace(w, "          if (this.ctx.cfg.localStrategy) throw", "          if (this.ctx.cfg.companionMode) throw new Error('陪我玩模式只提供建议，由用户操作。');\n          if (this.ctx.cfg.localStrategy) throw")
        w = replace(w, 'retry: !unknown && this.ctx.cfg.allowActions &&', 'retry: !unknown && this.actionsAllowed &&')
        w = replace(w, "return tools.filter(tool => !this.ctx.cfg.localStrategy || tool.name !== 'majsoul_act');", "return tools.filter(tool => (!this.ctx.cfg.localStrategy && !this.ctx.cfg.companionMode) || tool.name !== 'majsoul_act');")
        changed['src/world.ts'] = w
        cfg = changed['src/config.ts']
        cfg = replace(cfg, 'enabled: boolean;', 'enabled: boolean; companionMode: boolean; companionIntervalSec: number;')
        cfg = replace(cfg, '{ enabled: false,', '{ enabled: false, companionMode: false, companionIntervalSec: 10,')
        cfg = replace(cfg, "    'worlds.majsoul.localStrategy':", "    'worlds.majsoul.companionMode': { type: 'boolean', title: '陪我玩', 'x-hot': true, description: '只看牌、聊天和给建议，优先禁止一切代打。推荐用面板按钮切换，它会同时关闭操作权限。' },\n    'worlds.majsoul.companionIntervalSec': { type: 'integer', title: '陪玩建议间隔（秒）', minimum: 5, maximum: 60, 'x-hot': true, description: '默认 10 秒；和牌机会、对手威胁和结算可提前提醒。实际语音速度取决于模型和语音服务。' },\n    'worlds.majsoul.localStrategy':")
        changed['src/config.ts'] = cfg
        changed['src/definition.ts'] = replace(changed['src/definition.ts'], 'events: [', "events: [{ type: 'majsoul.advice', description: '陪玩时主动给建议和聊天，用户亲自操作；只使用最新牌局。', tags: ['snapshot'] }, ")
        changed['src/render.ts'] = replace(changed['src/render.ts'], "  if (status.controller === 'local')", "  if (status.controller === 'companion') {\n    lines.push('模式=陪我玩；用户亲自操作，你只聊天和给建议，不能代打。');\n    lines.push(status.advice ? '当前建议（未执行）=' + JSON.stringify(status.advice) : '当前无可操作建议，不要沿用上一个操作窗口的建议。');\n  }\n  if (status.controller === 'local')")
        ui = changed['src/webui/client.js']
        ui = replace(ui, "        function buttons() {", "        const companion = ui.button('陪我玩', {onClick: () => void update(last?.companionMode ? 'companion-off' : 'companion-on')});\n        function buttons() {\n          companion.disabled = loading;\n          companion.textContent = last?.companionMode ? '退出陪玩（保持暂停）' : '陪我玩';")
        ui = replace(ui, "state.controller === 'local' ?", "state.controller === 'companion' ? '陪我玩（你打牌，她给建议）' : state.controller === 'local' ?")
        ui = replace(ui, "              {k: '最近策略',", "              {k: '当前建议', v: state.advice ? `${state.advice.suggestion}：${state.advice.reason}` : state.companionMode ? '看着呢，等新的牌局机会' : '未开启陪玩'},\n              {k: '最近策略',")
        ui = replace(ui, "v: state.localDecision?.decision?.reason || '等待操作窗口'", "v: state.companionMode ? '只建议，不执行' : state.localDecision?.decision?.reason || '等待操作窗口'")
        ui = replace(ui, "v: state.localDecision?.outcome || '暂无'", "v: state.companionMode ? '由你亲自操作' : state.localDecision?.outcome || '暂无'")
        ui = replace(ui, "v: state.allowActions ? '允许' : '暂停'", "v: state.companionMode ? '陪玩中 · 禁止代打' : state.allowActions ? '允许' : '暂停'")
        ui = replace(ui, "view.ready ? '等待出牌决策'", "view.ready ? state.companionMode ? '等你出牌' : '等待出牌决策'")
        ui = replace(ui, 'actions.append(refresh, connect, attach);', 'actions.append(companion, refresh, connect, attach);')
        ui = replace(ui, '附着桥接会保留当前页面。', '附着桥接会保留当前页面。点击“陪我玩”，你打牌，她聊天和给建议。')
        changed['src/webui/client.js'] = ui
    # Assets are served from disk while the World may still be the old in-memory module.
    ui = changed['src/webui/client.js']
    if 'companion.disabled = loading;' in ui:
        ui = replace(ui, 'companion.disabled = loading;', "companion.disabled = loading || typeof last?.companionMode !== 'boolean';")
        ui = replace(ui, "companion.textContent = last?.companionMode ? '退出陪玩（保持暂停）' : '陪我玩';",
                     "companion.textContent = last && typeof last.companionMode !== 'boolean' ? '重启后可陪玩' : last?.companionMode ? '退出陪玩（保持暂停）' : '陪我玩';")
        ui = replace(ui, "message.textContent = state.error || '';",
                     "message.textContent = state.error || (typeof state.companionMode !== 'boolean' ? '雀魂插件已更新，后台仍是旧版本。请在局间从大肥鱼托盘菜单选择重启，再刷新此页面。' : '');")
    changed['src/webui/client.js'] = ui
    changed['src/companion-advisor.ts'] = (ROOT / 'integrations/majsoul/companion-advisor.ts').read_text(encoding='utf-8')
    return changed
