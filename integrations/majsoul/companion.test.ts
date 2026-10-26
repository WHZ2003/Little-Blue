import assert from 'node:assert/strict';
import { test } from 'node:test';
import { GameState } from './state.ts';
import { CompanionAdvisor } from './companion-advisor.ts';
import { MajsoulWorld } from './world.ts';
import { MAJSOUL_DEFAULTS } from './config.ts';

function state() {
  const s = new GameState();
  s.setSeat(0, [25000, 25000, 25000, 25000]);
  s.apply('ActionNewRound', { tiles: '1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z 2s 3s 7z'.split(' '),
    scores: [25000, 25000, 25000, 25000], doras: ['9p'], chang: 0, ju: 0, ben: 0, liqibang: 0, left_tile_count: 60,
    operation: { seat: 0, operation_list: [{ type: 1, combination: [] }], time_fixed: 5000, time_add: 20000 } }, 0, 1000);
  return s;
}

test('advice is read-only, deduplicated and invalidated after a manual discard or disconnect', () => {
  const s = state(), advisor = new CompanionAdvisor();
  const before = JSON.stringify(s.view);
  assert.equal(advisor.current(s.view)?.suggestion, '打出 中');
  assert.equal(JSON.stringify(s.view), before);
  assert.equal(advisor.shouldSpeak(s.view, 10, 1000), true);
  s.touch();
  assert.equal(advisor.shouldSpeak(s.view, 10, 12000), false, 'version-only changes do not repeat advice');
  s.apply('ActionDiscardTile', { seat: 0, tile: '7z' }, 1);
  assert.equal(advisor.current(s.view), null);
  assert.equal(advisor.shouldSpeak(s.view, 10, 12000), false);
  s.apply('ActionDealTile', { seat: 0, tile: '6z', left_tile_count: 59,
    operation: { seat: 0, operation_list: [{ type: 1, combination: [] }] } }, 2, 12000);
  assert.equal(advisor.current(s.view)?.suggestion, '打出 发');
  assert.equal(advisor.shouldSpeak(s.view, 10, 12000), true);
  s.block('Disconnected');
  assert.equal(advisor.current(s.view), null);
  assert.equal(advisor.shouldSpeak(s.view, 10, 30000), false);
});

test('ordinary advice is throttled; new threats and wins can speak sooner; ended rounds do not loop', () => {
  const s = state(), advisor = new CompanionAdvisor();
  assert.ok(advisor.shouldSpeak(s.view, 10, 0));
  s.view.operationReceivedAt = 2000; s.touch();
  assert.equal(advisor.shouldSpeak(s.view, 10, 2000), false);
  s.view.players[1].riichi = true; s.touch();
  assert.equal(advisor.shouldSpeak(s.view, 10, 3000), true);
  s.view.operations.push({ type: 8, name: 'tsumo', combinations: [] }); s.touch();
  assert.equal(advisor.shouldSpeak(s.view, 10, 6000), true);
  assert.equal(advisor.current(s.view)?.suggestion, '自摸和牌');
  s.apply('ActionHule', {}, 1);
  assert.equal(advisor.current(s.view), null);
  assert.equal(advisor.shouldSpeak(s.view, 10, 9000), true);
  s.touch();
  assert.equal(advisor.shouldSpeak(s.view, 10, 20000), false);
});

for (const localStrategy of [false, true]) test(`companion takes priority over allowed actions (localStrategy=${localStrategy})`, async () => {
  const cfg = { ...MAJSOUL_DEFAULTS, allowActions: true };
  const persisted: any[] = [];
  const world = new MajsoulWorld({ cfg, dataDir: '.', persist: async (patch: any) => { persisted.push(patch); } } as any);
  const cachedAct = world.tools().find(t => t.name === 'majsoul_act')!;
  cfg.localStrategy = localStrategy;
  cfg.companionMode = true;
  assert.equal(world.status().allowActions, false);
  assert.equal(world.status().controller, 'companion');
  assert.equal(world.suppressThinking(), false);
  assert.equal(JSON.parse(world.envPromptVars()['majsoul.permissions']).actions, false);
  assert.ok(!world.tools().some(t => t.name === 'majsoul_act'));
  assert.ok(world.tools().some(t => t.name === 'majsoul_reconnect'));
  assert.match((await cachedAct.handler({ action: 'discard' }, {} as any) as any).text, /陪我玩/);
  await assert.rejects(world.game.execute({}, 100), /游戏操作已暂停/);
  await world.panel('game', 'companion-off');
  assert.equal(cfg.allowActions, false, 'leaving the panel mode never resumes auto-play');
  await world.panel('game', 'companion-on');
  assert.deepEqual(persisted, [{ companionMode: false, allowActions: false }, { companionMode: true, allowActions: false }]);
  await world.stop();
});

test('companion reconnect can attach without action permission, and delayed advice renders latest state', async () => {
  const cfg = { ...MAJSOUL_DEFAULTS, companionMode: true };
  const world = new MajsoulWorld({ cfg, dataDir: '.', persist: async () => {} } as any);
  let attached = false;
  world.panel = async (_panel, method) => { attached = method === 'attach'; return {}; };
  await world.tools().find(t => t.name === 'majsoul_reconnect')!.handler({}, {} as any);
  assert.ok(attached);
  world.game.state.view = state().view;
  Object.defineProperty(world.game, 'connected', { get: () => true });
  const events: any[] = [];
  await world.start({ pushDeferred: (event: any, options: any) => { events.push({ event, options }); } } as any);
  try {
    const event = events.find(e => e.event.type === 'majsoul.advice');
    assert.equal(event.options.trigger, 'debounce');
    assert.match(event.event.render(), /打出 中/);
    world.game.state.consume();
    assert.doesNotMatch(event.event.render(), /打出 中/);
    assert.match(event.event.render(), /当前无可操作建议/);
    cfg.companionMode = false;
    assert.equal(event.event.render(), null);
  } finally { await world.stop(); }
});
