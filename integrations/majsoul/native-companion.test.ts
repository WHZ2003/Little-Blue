import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join, dirname, resolve, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { NativeCompanion, deepSeekDestination, type NativeFrame } from './native-companion.ts';
import { MajsoulWorld } from './world.ts';
import { MAJSOUL_DEFAULTS } from './config.ts';

const frame = (value = 1): NativeFrame => ({ jpeg: new Uint8Array([255, 216, value]), window: { handle: '123', title: '雀魂麻將', pid: 42 } });
function fixture() {
  let enabled = true, destination = true, captures = 0, image = frame();
  const events: any[] = [];
  const host: any = { modelFacts: { accepts: () => true }, pushDeferred: (event: any, options: any) => events.push({ event, options }) };
  const watch = new NativeCompanion(() => enabled, () => 10, () => '', () => destination, async () => { captures++; return image; });
  return { watch, host, events, setEnabled: (v: boolean) => { enabled = v; }, setDestination: (v: boolean) => { destination = v; },
    setImage: (v: NativeFrame) => { image = v; }, captures: () => captures };
}

test('native frames are throttled, deduplicated and delivered with images; newer frame supersedes old', async () => {
  const f = fixture();
  await f.watch.tick(f.host, 0);
  assert.equal(f.captures(), 1);
  assert.equal(f.events[0].options.trigger, 'debounce');
  const first = f.events[0].event.render();
  assert.match(first.text, /pet_say/);
  assert.match(first.text, /不点击/);
  assert.equal(first.blobs[0].mime, 'image/jpeg');
  await f.watch.tick(f.host, 5000);
  assert.equal(f.captures(), 1);
  await f.watch.tick(f.host, 10000);
  assert.equal(f.events.length, 1, 'unchanged images are not sent again');
  f.setImage(frame(2)); await f.watch.tick(f.host, 20000);
  assert.equal(f.events.length, 2);
  assert.equal(f.events[0].event.render(), null);
  assert.equal(f.events[1].event.render().blobs[0].bytes[2], 2);
});

test('stopping cancels an ongoing capture and drops late results and queued screenshots', async () => {
  const f = fixture();
  await f.watch.tick(f.host, 0);
  f.setEnabled(false); f.watch.stop();
  assert.equal(f.events[0].event.render(), null);
  let finish!: (v: NativeFrame) => void, signal!: AbortSignal;
  const pending = new NativeCompanion(() => true, () => 10, () => '', () => true, async (_h, s) => {
    signal = s; return new Promise(r => { finish = r; });
  });
  const run = pending.tick(f.host, 0);
  pending.stop(); assert.equal(signal.aborted, true);
  finish(frame()); await run;
  assert.equal(pending.status.frames, 0);
  assert.equal(f.events.length, 1);
});

test('wrong provider and image-incapable models never capture; changing destination drops queued image', async () => {
  const f = fixture(); f.setDestination(false);
  await f.watch.tick(f.host, 0);
  assert.equal(f.captures(), 0); assert.match(f.watch.status.error, /仅授权/);
  f.setDestination(true); f.host.modelFacts.accepts = () => false;
  await f.watch.tick(f.host, 10000);
  assert.equal(f.captures(), 0); assert.match(f.watch.status.error, /不支持看图/);
  f.host.modelFacts.accepts = () => true;
  await f.watch.tick(f.host, 20000);
  f.setDestination(false);
  assert.equal(f.events.at(-1).event.render(), null);
});

test('capture failures are visible and announced once, then recover without old-frame delivery', async () => {
  let failing = false;
  const f = fixture();
  const watch = new NativeCompanion(() => true, () => 10, () => '', () => true, async () => {
    if (failing) throw new Error('窗口已最小化'); return frame();
  });
  await watch.tick(f.host, 0);
  failing = true; await watch.tick(f.host, 10000);
  assert.equal(watch.status.watching, false);
  assert.match(watch.status.error, /最小化/);
  assert.equal(f.events[0].event.render(), null);
  assert.match(f.events[1].event.render(), /最小化/);
  await watch.tick(f.host, 20000); assert.equal(f.events.length, 2);
  failing = false; await watch.tick(f.host, 30000);
  assert.equal(watch.status.watching, true); assert.equal(f.events.length, 3);
  assert.equal(f.events[1].event.render(), null);
});

test('native sampling is single-flight and does not export an expired image', async () => {
  const f = fixture(); let finish!: (v: NativeFrame) => void, calls = 0;
  const watch = new NativeCompanion(() => true, () => 10, () => '', () => true, async () => {
    calls++; return new Promise(r => { finish = r; });
  });
  const run = watch.tick(f.host, 0); await watch.tick(f.host, 20000);
  assert.equal(calls, 1); finish(frame()); await run;
  const original = Date.now;
  try {
    Date.now = () => original() + 26000;
    assert.equal(f.events[0].event.render(), null);
  } finally { Date.now = original; }
});

test('native source never enables game actions, reconnect never opens a browser, and exit stays paused', async () => {
  const cfg = { ...MAJSOUL_DEFAULTS, companionSource: 'native' as const, companionMode: true, localStrategy: true, allowActions: true };
  const world = new MajsoulWorld({ cfg, botDir: '.', dataDir: '.', persist: async () => {} } as any);
  world.game.connect = async () => { throw new Error('Native mode must not attach a browser'); };
  assert.equal(world.status().allowActions, false);
  assert.equal(world.status().companionSource, 'native');
  await assert.rejects(world.game.execute({}, 50), /已暂停/);
  assert.ok(!world.tools().some(t => t.name === 'majsoul_act'));
  const output: any = await world.tools().find(t => t.name === 'majsoul_reconnect')!.handler({}, {} as any);
  assert.equal(typeof output, 'string'); assert.match(output, /客户端陪玩/);
  await world.panel('game', 'attach'); await world.panel('game', 'connect');
  await world.panel('game', 'companion-off');
  assert.equal(cfg.allowActions, false);
  cfg.allowActions = true;
  assert.equal(world.status().allowActions, false, 'native source stays observation-only even when companion is off');
  await world.stop();
});

test('destination check accepts only the configured official HTTPS DeepSeek provider', async () => {
  const root = await mkdtemp(join(tmpdir(), 'majsoul-destination-'));
  const bot = join(root, 'companion'), provider = join(root, 'providers', 'deepseek');
  try {
    await mkdir(bot, { recursive: true }); await mkdir(provider, { recursive: true });
    await writeFile(join(bot, 'config.json'), JSON.stringify({ activeProvider: 'deepseek' }));
    for (const [url, allowed] of [['https://api.deepseek.com', true], ['https://api.deepseek.com/v1', true],
      ['https://api.deepseek.com.example.org', false], ['http://api.deepseek.com', false], ['https://other.example.org', false]] as const) {
      await writeFile(join(provider, 'config.json'), JSON.stringify({ baseUrl: url }));
      assert.equal(deepSeekDestination(bot), allowed, url);
    }
  } finally {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.match(basename(root), /^majsoul-destination-/);
    await rm(root, { recursive: true, force: true });
  }
});
