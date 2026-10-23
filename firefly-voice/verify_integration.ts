/** Exercise the installed Speaker without waking the user: intercept only OS playback. */
import assert from 'node:assert/strict';
import { copyFile, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Speaker } from '../.research/Coopanion/data/extensions/node_modules/cortico-world-fatfish/src/speaker.ts';

const cfg = JSON.parse(await readFile('.research/Coopanion/data/home/companion/config.json', 'utf8')).worlds.fatfish;
const settings = { voice: cfg.voice, rate: cfg.rate, volume: cfg.volume,
  seedVcEnabled: true, seedVcEndpoint: 'http://127.0.0.1:18892', seedVcLauncher: resolve('firefly-voice/start-service.ps1') };
const speaker = new Speaker() as any;
const logs: string[] = [];
let output = '';
speaker.play = async (clip: string) => {
  assert.ok(clip.endsWith('.firefly.wav'), 'must reach the new voice rather than silently fall back');
  const data = await readFile(clip);
  assert.equal(data.toString('ascii', 0, 4), 'RIFF');
  output = resolve('firefly-voice/samples/fatfish-integrated.wav');
  await copyFile(clip, output);
};
try {
  const started = performance.now();
  assert.equal(await speaker.speak('我在呢，你忙完了就过来聊会儿。', settings, (line: string) => logs.push(line)), true);
  assert.ok(logs.some(line => line.includes('Seed-VC firefly-zh-v1 converted')));
  assert.ok(output);
  const report = { success: true, pipeline: 'installed Fatfish Speaker → existing Edge TTS → trained Seed-VC → WAV playback input',
    osPlaybackMutedForVerification: true, elapsedSeconds: (performance.now() - started) / 1000, file: output, logs };
  await writeFile('firefly-voice/logs/integration-check.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { await speaker.stop(); }
