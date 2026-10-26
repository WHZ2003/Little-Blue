import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, type BrowserContext } from 'playwright-core';
import { WebSocketServer, type WebSocket } from 'ws';
import { encode, decode, envelope, request, unpack, xorAction } from './protocol.ts';
import { MajsoulWorld } from './world.ts';
import { MAJSOUL_DEFAULTS } from './config.ts';

for (const mode of ['local', 'companion-local', 'companion-model', 'switch-before-send'])
test(`real Edge/protocol: ${mode}`, { timeout: 20000 }, async t => {
  const dir = await mkdtemp(join(tmpdir(), 'majsoul-local-controller-'));
  let peer: WebSocket, step = 0, submitted = 0, sentTile = '';
  const auth = request('.lq.FastTest.authGame', 1, { account_id: 101 });
  const server = createServer((_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(`<script>const ws=new WebSocket('ws://'+location.host+'/gateway');ws.binaryType='arraybuffer';ws.onopen=()=>ws.send(Uint8Array.from(atob('${auth.toString('base64')}'),c=>c.charCodeAt(0)));</script>`);
  });
  const sockets = new WebSocketServer({ server });
  let context: BrowserContext | undefined;
  let cleanupWorld: MajsoulWorld | undefined;
  t.after(async () => {
    await cleanupWorld?.stop(); await context?.close();
    for (const client of sockets.clients) client.terminate();
    await new Promise<void>(r => sockets.close(() => r()));
    await new Promise<void>(r => server.close(() => r()));
  });
  const operation = { seat: 0, operation_list: [{ type: 1 }], time_fixed: 5000 };
  const notify = (name: string, data: any) => peer.send(envelope(1, 0, '.lq.ActionPrototype', encode('lq.ActionPrototype', {
    name, step: step++, data: xorAction(encode(`lq.${name}`, data)),
  })));
  sockets.on('connection', socket => {
    peer = socket;
    socket.on('message', bytes => {
      const frame = unpack(Buffer.from(bytes as Buffer));
      if (frame.name.endsWith('.authGame')) {
        socket.send(envelope(3, frame.id, '', encode('lq.ResAuthGame', { seat_list: [101, 102, 103, 104] })));
        notify('ActionNewRound', { tiles: '1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z 2s 3s 7z'.split(' '),
          scores: [25000, 25000, 25000, 25000], doras: ['9p'], left_tile_count: 60, operation });
      } else {
        submitted++;
        const data = decode('lq.ReqSelfOperation', frame.data); sentTile = data.tile;
        notify('ActionDiscardTile', { seat: 0, tile: data.tile, moqie: data.moqie });
        socket.send(envelope(3, frame.id, '', encode('lq.ResCommon', {})));
      }
    });
  });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  context = await chromium.launchPersistentContext(join(dir, 'profile'), { channel: 'msedge', headless: true,
    args: ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] });
  const port = (await readFile(join(dir, 'profile', 'DevToolsActivePort'), 'utf8')).split('\n')[0];
  const cfg = { ...MAJSOUL_DEFAULTS, localStrategy: mode !== 'companion-model', companionMode: mode.startsWith('companion'),
    allowActions: true, newRoundDelayMs: mode === 'switch-before-send' ? 1500 : 0 };
  let persisted = false;
  const world = new MajsoulWorld({ cfg, dataDir: dir, persist: async (patch: any) => { persisted = patch.allowActions === false; } } as any);
  cleanupWorld = world;
  const events: any[] = [];
  await world.start({ pushDeferred: (event: any, options: any) => { events.push({ event, options }); } } as any);
  const wait = async (condition: () => boolean) => { const until = Date.now() + 8000; while (!condition()) {
    assert.ok(Date.now() < until, world.game.error || 'timed out'); await delay(20);
  } };
  {
    await world.game.connect(`http://127.0.0.1:${port}`, `http://127.0.0.1:${(server.address() as any).port}/`);
    if (mode !== 'local') {
      await wait(() => world.game.snapshot().ready);
      if (mode === 'switch-before-send') {
        await world.panel('game', 'companion-on');
        await delay(1700);
      }
      await wait(() => !!world.status().advice);
      await delay(250);
      assert.equal(submitted, 0, 'companion must never send game actions, even if allowActions was enabled');
      assert.equal(world.status().advice?.suggestion, '打出 中');
      assert.ok(events.some(e => e.event.type === 'majsoul.advice' && e.options.trigger === 'debounce'));
      await assert.rejects(world.game.execute({ version: world.game.snapshot().version, action: 'discard', tile: '7z' }, 100), /已暂停/);
      notify('ActionDiscardTile', { seat: 0, tile: '7z' });
      await wait(() => !world.game.snapshot().ready);
      assert.equal(world.status().advice, null, 'a manual discard invalidates the old suggestion');
      notify('ActionDealTile', { seat: 0, tile: '6z', operation, left_tile_count: 59 });
      await wait(() => world.status().advice?.suggestion === '打出 发');
      assert.equal(submitted, 0);
      await world.panel('game', 'companion-off');
      await delay(250);
      assert.equal(submitted, 0, 'leaving companion mode keeps actions paused');
      return;
    }
    await wait(() => world.player.last?.outcome === 'confirmed');
    assert.equal(submitted, 1); assert.equal(sentTile, '7z');
    assert.equal(world.status().controller, 'local');
    assert.ok(!world.tools().some(t => t.name === 'majsoul_act'));
    assert.ok(!events.some(e => e.options.trigger === 'preempt'));
    assert.ok(events.some(e => e.event.type === 'majsoul.decision'));
    assert.equal(JSON.parse((await readFile(join(dir, 'majsoul', 'decisions.jsonl'), 'utf8')).trim()).outcome, 'confirmed');
    await world.tools().find(t => t.name === 'majsoul_pause')!.handler({}, {} as any);
    assert.ok(persisted);
    notify('ActionDealTile', { seat: 0, tile: '6z', operation, left_tile_count: 59 });
    await wait(() => world.game.snapshot().ready); await delay(250);
    assert.equal(submitted, 1, 'paused controller must leave new operation to the user');
    cfg.allowActions = true;
    await wait(() => submitted === 2 && world.player.last?.decision?.args.tile === '6z');
    assert.equal(world.player.last?.outcome, 'confirmed');
  }
});
