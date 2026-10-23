import { readFile, mkdir, copyFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MsEdgeTTS, OUTPUT_FORMAT } from '../.research/Coopanion/data/extensions/node_modules/cortico-world-fatfish/src/vendor/msedge-tts/index.ts';

const cfg = JSON.parse(await readFile('.research/Coopanion/data/home/companion/config.json', 'utf8')).worlds.fatfish;
const lines = ['我在呢，你忙完了就过来聊会儿。', '怎么又熬夜了，快去睡吧。', '这个视频也太有意思了，想发给你看看。'];
const tts = new MsEdgeTTS({ enableLogger: false });
const output = resolve('firefly-voice/samples');
await mkdir(output, { recursive: true });
try {
  await tts.setMetadata(cfg.voice || 'zh-CN-XiaoxiaoNeural', OUTPUT_FORMAT.AUDIO_24KHZ_48KBITRATE_MONO_MP3);
  for (const [i, line] of lines.entries()) {
    const { audioFilePath } = await tts.toFile(output, line, { rate: cfg.rate || '+0%', volume: cfg.volume || '+0%' });
    await copyFile(audioFilePath, resolve(output, `source-${i + 1}.mp3`));
  }
  await writeFile(resolve(output, 'texts.json'), JSON.stringify(lines, null, 2));
  console.log(JSON.stringify({ samples: lines.length, voice: cfg.voice, directory: output }));
} finally { tts.close(); }
