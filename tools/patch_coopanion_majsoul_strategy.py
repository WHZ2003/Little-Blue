"""Install the deterministic Mahjong controller, without touching persona/config.

Pass --package to test on an isolated copy before installing. Requires browser repair.
"""
import argparse
import json
from pathlib import Path
import shutil
import time

from patch_coopanion_majsoul import ROOT, PACKAGE, replace

MARKER = '// DaFeiYu: local strategy v1'


def prepare(package):
    meta = json.loads((package / 'package.json').read_text(encoding='utf-8'))
    if meta['version'] != '1.1.1':
        raise ValueError('Review the patch for this plugin version first')
    names = ['src/world.ts', 'src/config.ts', 'src/definition.ts', 'src/render.ts', 'src/webui/client.js']
    changed = {n: (package / n).read_text(encoding='utf-8') for n in names}
    w = changed['src/world.ts']
    if MARKER not in w:
        if 'dedicated-browser panel repair v1' not in w:
            raise ValueError('Apply the browser repair first')
        w = MARKER + "\nimport { LocalPlayer, type DecisionRecord } from './local-player.ts';\nimport { appendFileSync, mkdirSync, statSync, renameSync, existsSync, rmSync } from 'node:fs';\n" + w
        w = replace(w, "const read: ToolSpec =", "const pause: ToolSpec = { name: 'majsoul_pause', description: '停止本地自动打牌；已提交服务器的动作不能撤销。恢复由用户在配置中开启允许操作。', tags: ['act'], barrierAfter: true, parameters: { type: 'object', properties: {}, additionalProperties: false } };\nconst read: ToolSpec =")
        w = replace(w, 'export class MajsoulWorld', 'MAJSOUL_TOOL_DECLS.push(pause);\nexport class MajsoulWorld')
        w = replace(w, '  private host?: WorldHost;', '  private host?: WorldHost;\n  readonly player: LocalPlayer;\n  private lastCommentary = 0;\n  private lastMode = "";')
        w = replace(w, '  envPromptVars():', '''  private recordDecision(record: DecisionRecord): void {
    const path = join(this.ctx.dataDir, 'majsoul', 'decisions.jsonl');
    try {
      mkdirSync(join(this.ctx.dataDir, 'majsoul'), { recursive: true });
      if (existsSync(path) && statSync(path).size > 5_000_000) {
        if (existsSync(path + '.1')) rmSync(path + '.1');
        renameSync(path, path + '.1');
      }
      const result = record.result as { outcome?: string; decisionMs?: number; acknowledgementMs?: number } | undefined;
      appendFileSync(path, JSON.stringify({ ...record, result: result && { outcome: result.outcome, decisionMs: result.decisionMs, acknowledgementMs: result.acknowledgementMs } }) + '\\n');
    } catch { /* Diagnostic IO must not interrupt gameplay. */ }
    if (record.outcome !== 'confirmed' || !record.decision || !this.host) return;
    const mode = record.decision.mode;
    const notable = mode === 'win' || mode !== this.lastMode;
    this.lastMode = mode;
    if (!notable || Date.now() - this.lastCommentary < 30000) return;
    this.lastCommentary = Date.now();
    const text = JSON.stringify({ outcome: record.outcome, decision: record.decision, at: record.at });
    this.host.pushDeferred({ type: 'majsoul.decision', senderKey: this.id, tags: ['snapshot'], render: () => text }, { trigger: 'debounce' });
  }
  envPromptVars():''')
        w = replace(w, 'JSON.stringify({ actions: this.ctx.cfg.allowActions,', 'JSON.stringify({ localStrategy: this.ctx.cfg.localStrategy, actions: this.ctx.cfg.allowActions,')
        w = replace(w, '    return !!this.host && this.ctx.cfg.allowActions', '    return !this.ctx.cfg.localStrategy && !!this.host && this.ctx.cfg.allowActions')
        w = replace(w, '    return { browserConnected:', "    return { controller: this.ctx.cfg.localStrategy ? 'local' : 'legacy-model', localDecision: this.player.last, browserConnected:")
        w = replace(w, '    this.host = host;', '    this.host = host;\n    this.player.start();')
        w = replace(w, '    this.game.onState = view => {', '    this.game.onState = view => {\n      this.player.tick();')
        w = replace(w, "{ trigger: this.ctx.cfg.allowActions && view.ready", "{ trigger: !this.ctx.cfg.localStrategy && this.ctx.cfg.allowActions && view.ready")
        w = replace(w, 'async stop(): Promise<void> { this.game.onState', 'async stop(): Promise<void> { this.player.stop(); this.game.onState')
        w = replace(w, '      handler: async (args, call) => {\n        try {', "      handler: async (args, call) => {\n        try {\n          if (this.ctx.cfg.localStrategy) throw new Error('本地策略负责操作，模型只能观察和解说。');")
        w = replace(w, '    }];\n  }\n}', '''    }, { ...pause, handler: async () => {
      this.ctx.cfg.allowActions = false;
      this.player.tick();
      await this.ctx.persist({ allowActions: false });
      return this.modelStatus({ message: '已暂停自动打牌；已提交的动作无法撤销。' });
    } }].filter(tool => !this.ctx.cfg.localStrategy || tool.name !== 'majsoul_act');
  }
}''')
        constructor_line = "    this.game = new GameBrowser(() => ctx.cfg.allowActions, join(ctx.dataDir, 'majsoul', 'actions.jsonl'), () => ctx.cfg.newRoundDelayMs, () => ctx.cfg.maxRenderFps);"
        w = replace(w, constructor_line, constructor_line + "\n    this.player = new LocalPlayer(this.game, () => !!this.host && this.ctx.cfg.localStrategy && this.ctx.cfg.allowActions, () => this.ctx.cfg.commandTimeoutSec * 1000, record => this.recordDecision(record));")
        changed['src/world.ts'] = w
        cfg = changed['src/config.ts']
        cfg = replace(cfg, 'enabled: boolean;', 'enabled: boolean; localStrategy: boolean;')
        cfg = replace(cfg, '{ enabled: false,', '{ enabled: false, localStrategy: false,')
        cfg = replace(cfg, "    'worlds.majsoul.allowActions':", "    'worlds.majsoul.localStrategy': { type: 'boolean', title: '本地代码选牌（DeepSeek 仅解说）', description: '切换模式需重启，建议局间操作。' },\n    'worlds.majsoul.allowActions':")
        changed['src/config.ts'] = cfg
        changed['src/definition.ts'] = replace(changed['src/definition.ts'], "      majsoul_status:", "      majsoul_pause: '暂停本地自动打牌。',\n      majsoul_status:")
        changed['src/definition.ts'] = replace(changed['src/definition.ts'], "events: [", "events: [{ type: 'majsoul.decision', description: '本地策略确认的关键动作，仅供简短解说。', tags: ['snapshot'] }, ")
        changed['src/render.ts'] = replace(changed['src/render.ts'], '  if (status.error || v.reason)', "  if (status.controller === 'local') {\n    lines.push('执行者=本地策略；DeepSeek只观察与解说，不提交动作。');\n    if (status.localDecision) lines.push('最近本地决策=' + JSON.stringify(status.localDecision, (key, value) => key === 'result' ? undefined : value));\n  }\n  if (status.error || v.reason)")
        changed['src/webui/client.js'] = replace(changed['src/webui/client.js'], "{k: '游戏操作',", "{k: '选牌方式', v: state.controller === 'local' ? '本地策略（DeepSeek 仅解说）' : '旧版模型决策'},\n              {k: '最近策略', v: state.localDecision?.decision?.reason || '等待操作窗口'},\n              {k: '策略回执', v: state.localDecision?.outcome || '暂无'},\n              {k: '游戏操作',")
    # Keep handler parameters contextually typed before filtering the advertised tools.
    w = changed['src/world.ts']
    if '    return [read,' in w:
        w = replace(w, '    return [read,', '    const tools: ToolDef[] = [read,')
        w = replace(w, "    } }].filter(tool => !this.ctx.cfg.localStrategy || tool.name !== 'majsoul_act');",
                    "    } }];\n    return tools.filter(tool => !this.ctx.cfg.localStrategy || tool.name !== 'majsoul_act');")
    changed['src/world.ts'] = w
    changed['src/config.ts'] = changed['src/config.ts'].replace(
        "'worlds.majsoul.localStrategy': { type: 'boolean', title:",
        "'worlds.majsoul.localStrategy': { type: 'boolean', 'x-hot': false, title:")
    for name in ['strategy.ts', 'local-player.ts', 'ENV_PROMPT.md']:
        changed['src/' + name] = (ROOT / 'integrations/majsoul' / name).read_text(encoding='utf-8')
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=PACKAGE)
    args = parser.parse_args()
    changed = {n: s for n, s in prepare(args.package).items()
               if not (args.package / n).exists() or (args.package / n).read_text(encoding='utf-8') != s}
    backup = ROOT / 'backups' / ('majsoul-strategy-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000))
    for name, content in changed.items():
        dest = args.package / name
        if dest.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
        tmp = dest.with_suffix(dest.suffix + '.dafeiyu-tmp')
        tmp.write_text(content, encoding='utf-8')
        tmp.replace(dest)  # Break pnpm hardlinks; never edit the shared store in-place.
    print(json.dumps({'patched': list(changed), 'backup': str(backup) if changed else None}))


if __name__ == '__main__':
    main()
