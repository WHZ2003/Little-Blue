import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { GameState, commandFor, OPERATIONS } from './state.ts';
import { analyzeHand } from './hand.ts';
import { chooseAction, tileRisk, doraFromIndicator } from './strategy.ts';
import { LocalPlayer } from './local-player.ts';
import { MajsoulWorld } from './world.ts';
import { MAJSOUL_DEFAULTS } from './config.ts';

const tiles = (s: string) => s.split(' ');
const efficient = tiles('1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z 2s 3s 7z');
function view(hand = efficient, ops = [1], players = 4) {
  const state = new GameState();
  state.setSeat(0, Array(players).fill(25000));
  state.apply('ActionNewRound', { tiles: hand, scores: Array(players).fill(25000), doras: ['9p'], chang: 0, ju: 0, ben: 0,
    liqibang: 0, left_tile_count: 60, operation: { seat: 0, operation_list: ops.map(type => ({ type, combination: [] })), time_fixed: 5000, time_add: 20000 } }, 0);
  return state.view;
}
const discard = (tile: string) => ({ tile, called: false, tsumogiri: false, riichi: false });
function legal(v: ReturnType<typeof view>) {
  const before = JSON.stringify(v), d = chooseAction(v);
  assert.ok(d);
  assert.doesNotThrow(() => commandFor(v, d.args));
  assert.equal(JSON.stringify(v), before, 'strategy must not mutate authoritative state');
  return d;
}

test('efficient discard, win priority, forbidden discard, red five preservation', () => {
  assert.equal(legal(view()).args.tile, '7z');
  assert.equal(legal(view(efficient, [1, 8])).args.action, 'tsumo');
  assert.equal(legal(view(efficient.slice(0, 13), [3, 9])).args.action, 'ron');
  const blocked = view(); blocked.operations[0].combinations = ['7z'];
  assert.notEqual(legal(blocked).args.tile, '7z');
  const red = view(tiles('1m 2m 3m 4p 0p 5p 6p 7s 8s 9s 1z 1z 2s 3s'));
  assert.equal(legal(red).args.tile, '5p');
});

test('bad hand folds to genbutsu; multiple threats require safety against both; suji is not zero risk', () => {
  const v = view(tiles('1m 3m 6m 8m 2p 5p 8p 1s 4s 7s 9s 1z 3z 7z'));
  v.players[1].riichi = true; v.players[1].discards = [discard('6m'), discard('4p')];
  let d = legal(v);
  assert.equal(d.mode, 'fold'); assert.equal(d.args.tile, '6m');
  assert.ok(tileRisk(v, '1p') > 0);
  v.players[2].riichi = true; v.players[2].discards = [discard('7z')];
  v.players[1].discards.push(discard('7z'));
  d = legal(v); assert.equal(d.args.tile, '7z'); assert.equal(tileRisk(v, '7z'), 0);
});

test('riichi is legal and non-furiten; committed riichi obeys forced draw discard', () => {
  const v = view(efficient, [1, 7]); v.operations[1].combinations = ['7z'];
  assert.equal(legal(v).args.action, 'riichi');
  v.players[0].discards.push(discard('4s'));
  assert.notEqual(legal(v).args.action, 'riichi');
  v.players[0].riichi = true;
  assert.equal(legal(v).args.tile, v.drawn);
});

test('calls without yaku or without improvement are declined; useful yakuhai pon is accepted', () => {
  const v = view(tiles('2m 2m 3m 4m 5p 6p 7p 2s 3s 7s 8s 1z 1z'), [3]);
  v.operations[0].combinations = ['2m|2m'];
  v.lastAction = { name: 'ActionDiscardTile', seat: 1, tile: '2m' };
  assert.equal(legal(v).args.action, 'pass');
  const yakuhai = view(tiles('5z 5z 1m 2m 3m 4p 5p 6p 2s 3s 7s 7s 9m'), [3]);
  yakuhai.operations[0].combinations = ['5z|5z'];
  yakuhai.lastAction = { name: 'ActionDiscardTile', seat: 1, tile: '5z' };
  assert.equal(legal(yakuhai).args.action, 'pon');
  yakuhai.players[2].riichi = true;
  assert.equal(legal(yakuhai).args.action, 'pass');
});

test('sanma north extraction preserves kokushi and nearly completed seven pairs', () => {
  const ordinary = view(tiles('1m 9m 2p 3p 4p 5p 0p 6p 7p 2s 3s 4s 4z 1z'), [1, 11], 3);
  assert.equal(legal(ordinary).args.action, 'babei');
  const kokushi = view(tiles('1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 2p'), [1, 11], 3);
  assert.equal(legal(kokushi).args.tile, '2p');
  const pairs = view(tiles('1m 1m 9m 9m 2p 2p 4p 4p 6s 6s 4z 4z 5z 7z'), [1, 11], 3);
  assert.notEqual(legal(pairs).args.action, 'babei');
  assert.equal(doraFromIndicator('9m', 3), '1m');
  assert.equal(doraFromIndicator('1m', 3), '9m');
  assert.equal(doraFromIndicator('9m', 4), '1m');
  assert.equal(doraFromIndicator('4z', 4), '1z');
  assert.equal(doraFromIndicator('7z', 3), '5z');
});

test('kan is conservative and unavailable in defense; incomplete states never act', () => {
  const v = view(tiles('1m 1m 1m 1m 4p 5p 6p 7s 8s 9s 1z 1z 2s 3s'), [1, 4]);
  v.operations[1].combinations = ['1m'];
  assert.equal(legal(v).args.action, 'ankan');
  v.players[1].riichi = true;
  assert.notEqual(legal(v).args.action, 'ankan');
  v.complete = false; assert.equal(chooseAction(v), null);
  v.complete = true; v.ready = false; assert.equal(chooseAction(v), null);
});

test('seeded samples always choose a legal action without worsening shanten in quiet attack', () => {
  let seed = 91773;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  let maxMs = 0;
  for (let sample = 0; sample < 80; sample++) {
    const players = sample % 2 ? 3 : 4;
    const wall: string[] = [];
    for (const suit of ['m', 'p', 's', 'z']) for (let n = 1; n <= (suit === 'z' ? 7 : 9); n++) {
      if (players === 3 && suit === 'm' && n !== 1 && n !== 9) continue;
      for (let c = 0; c < 4; c++) wall.push(`${n}${suit}`);
    }
    for (let i = wall.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [wall[i], wall[j]] = [wall[j], wall[i]]; }
    const v = view(wall.slice(0, 14), [1], players); v.doras = [wall[14]];
    const start = performance.now(), d = legal(v); maxMs = Math.max(maxMs, performance.now() - start);
    assert.equal(d.candidates![0].shanten, analyzeHand(v)!.discardCandidates[0].shanten);
  }
  console.log(`80 seeded hands: slowest strategy calculation ${maxMs.toFixed(1)} ms`);
});

test('controller deduplicates windows including changed versions after rejection, then accepts new windows', async () => {
  const v = view(); let calls = 0;
  const records: any[] = [];
  const game = { connected: true, busy: false, snapshot: () => structuredClone(v), execute: async () => {
    calls++; throw Error('server rejected; do not retry');
  } };
  const p = new LocalPlayer(game, () => true, () => 500, r => records.push(r));
  try {
    p.start(); p.tick(); p.tick(); await delay(20);
    v.version = 'changed-after-rejection'; p.tick(); await delay(20);
    assert.equal(calls, 1); assert.equal(records[0].outcome, 'failed');
    v.operationReceivedAt! += 1000; p.tick(); await delay(20);
    assert.equal(calls, 2);
  } finally { p.stop(); }
});

test('permission pause aborts an in-flight pre-send wait, confirmed callbacks do not duplicate', async () => {
  const v = view(); let allowed = true, calls = 0, aborted = false;
  const game = { connected: true, busy: false, snapshot: () => structuredClone(v), execute: async (_a: any, _t: number, signal?: AbortSignal) => {
    calls++;
    await new Promise<void>((resolve, reject) => signal!.addEventListener('abort', () => { aborted = true; reject(Error('aborted')); }, { once: true }));
    return { outcome: 'state_changed' };
  } };
  const p = new LocalPlayer(game, () => allowed, () => 500);
  p.start(); allowed = false; p.tick(); await delay(10); p.stop();
  assert.ok(aborted); assert.equal(calls, 1);
  let confirmed = 0;
  game.execute = async () => { calls++; return { outcome: 'state_changed' }; };
  const next = new LocalPlayer(game, () => true, () => 500, r => { if (r.outcome === 'confirmed') confirmed++; });
  next.start(); await delay(20); next.tick(); next.stop();
  assert.equal(confirmed, 1); assert.equal(calls, 2);
});

test('World removes model action tool, blocks cached action tool and preserves read/pause controls', async () => {
  const cfg = { ...MAJSOUL_DEFAULTS, allowActions: true };
  const world = new MajsoulWorld({ cfg, dataDir: '.', persist: async () => {} } as any);
  const cachedAct = world.tools().find(t => t.name === 'majsoul_act')!;
  cfg.localStrategy = true;
  assert.ok(!world.tools().some(t => t.name === 'majsoul_act'));
  assert.ok(world.tools().some(t => t.name === 'majsoul_status'));
  assert.equal(world.suppressThinking(), false);
  const result: any = await cachedAct.handler({ action: 'discard' }, {} as any);
  assert.equal(result.failed, true);
  await world.tools().find(t => t.name === 'majsoul_pause')!.handler({}, {} as any);
  assert.equal(cfg.allowActions, false);
  await world.stop();
});
