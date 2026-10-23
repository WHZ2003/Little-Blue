import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { convertClip } from './fatfish-converter.ts';
import { Speaker } from '../.research/Coopanion/data/extensions/node_modules/cortico-world-fatfish/src/speaker.ts';

test('local converter validates service and WAV, handles cancellation, then falls back on failure', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'firefly-voice-test-'));
  const source = join(dir, 'source.mp3'); await writeFile(source, 'fake-source');
  const wav = Buffer.alloc(60); wav.write('RIFF'); wav.write('WAVE', 8);
  let mode = 'ok', count = 0;
  const server = createServer(async (req, res) => {
    if (req.url === '/health') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ service: 'dafeiyu-firefly-seed-vc', ready: true })); return; }
    assert.equal(req.headers['x-fatfish-voice'], '1');
    count++;
    if (mode === 'slow') await delay(100);
    if (mode === 'bad') { res.end('not audio'); return; }
    res.setHeader('Content-Type', 'audio/wav'); res.end(wav);
  });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const opts = { seedVcEnabled: true, seedVcEndpoint: `http://127.0.0.1:${(server.address() as any).port}` };
  const logs: string[] = [];
  try {
    assert.equal(await convertClip(source, { seedVcEnabled: false }, () => {}), source);
    const result = await convertClip(source, opts, m => logs.push(m));
    assert.ok(result.endsWith('.wav')); assert.deepEqual(await readFile(result), wav); assert.equal(count, 1);
    mode = 'slow';
    const abort = new AbortController();
    const cancelled = convertClip(source, opts, () => {}, abort.signal);
    setTimeout(() => abort.abort(), 15);
    await assert.rejects(cancelled);
    mode = 'bad';
    assert.equal(await convertClip(source, opts, m => logs.push(m)), source);
    assert.ok(logs.some(l => l.includes('using original Edge voice')));
  } finally { server.closeAllConnections(); await new Promise<void>(r => server.close(() => r())); }
});

test('hush while synthesizing drops the eventual clip and does not play it', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'firefly-hush-test-'));
  const source = join(dir, 'source.mp3'); await writeFile(source, 'fake-source');
  const speaker = new Speaker() as any;
  let played = false;
  speaker.synthesise = async () => { await delay(30); return source; };
  speaker.play = async () => { played = true; };
  const result = speaker.speak('hello', { voice: 'test', rate: '+0%', volume: '+0%' }, () => {});
  speaker.hush();
  assert.equal(await result, false); assert.equal(played, false);
  await speaker.stop();
});

test('speaker passes the conversion options without changing spoken text or original TTS settings', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'firefly-pipeline-test-'));
  const source = join(dir, 'source.mp3'); await writeFile(source, 'fake-source');
  const speaker = new Speaker() as any;
  const opts = { voice: 'original', rate: '+15%', volume: '-10%', seedVcEnabled: false };
  let played = '';
  speaker.synthesise = async (text: string, settings: any) => { assert.equal(text, '原来的说话方式'); assert.deepEqual(settings, opts); return source; };
  speaker.play = async (file: string) => { played = file; };
  assert.equal(await speaker.speak('原来的说话方式', opts, () => {}), true);
  assert.equal(played, source);
  await assert.rejects(readFile(source));
  await speaker.stop();
});
