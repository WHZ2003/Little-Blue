import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright-core';
import { WebSocketServer, type WebSocket } from 'ws';
import { encode, decode, envelope, request, unpack, xorAction } from './protocol.ts';
import { MajsoulWorld } from './world.ts';
import { MAJSOUL_DEFAULTS } from './config.ts';

test('local controller plays through real Edge/protocol with no model; pause prevents next submission', { timeout: 20000 }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'majsoul-local-controller-'));
  let peer: WebSocket, step = 0, submitted = 0, sentTile = '';
  const auth = request('.lq.FastTest.authGame', 1, { account_id: 101 });
  const server = createServer((_req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end(`<script>const ws=new WebSocket('ws://'+location.host+'/gateway');ws.binaryType='arraybuffer';ws.onopen=()=>ws.send(Uint8Array.from(atob('${auth.toString('base64')}'),c=>c.charCodeAt(0)));</script>`);
  });
  const sockets = new WebSocketServer({ server });
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
  const context = await chromium.launchPersistentContext(join(dir, 'profile'), { channel: 'msedge', headless: true,
    args: ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] });
  const port = (await readFile(join(dir, 'profile', 'DevToolsActivePort'), 'utf8')).split('\n')[0];
  const cfg = { ...MAJSOUL_DEFAULTS, localStrategy: true, allowActions: true, newRoundDelayMs: 0 };
  let persisted = false;
  const world = new MajsoulWorld({ cfg, dataDir: dir, persist: async (patch: any) => { persisted = patch.allowActions === false; } } as any);
  const events: any[] = [];
  await world.start({ pushDeferred: (event: any, options: any) => { events.push({ event, options }); } } as any);
  const wait = async (condition: () => boolean) => { const until = Date.now() + 8000; while (!condition()) {
    assert.ok(Date.now() < until, world.game.error || 'timed out'); await delay(20);
  } };
  try {
    await world.game.connect(`http://127.0.0.1:${port}`, `http://127.0.0.1:${(server.address() as any).port}/`);
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
  } finally {
    await world.stop(); await context.close();
    for (const client of sockets.clients) client.terminate();
    await new Promise<void>(r => sockets.close(() => r()));
    await new Promise<void>(r => server.close(() => r()));
  }
});
