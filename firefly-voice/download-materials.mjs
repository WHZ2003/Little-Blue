import { File } from './download-tools/node_modules/megajs/dist/main.node-es.mjs';
import { writeFile, mkdir } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { resolve, basename } from 'node:path';

const root = File.fromURL('https://mega.nz/folder/CxxmyCgI#WTVw2jbFYoEQbrF_M0nk3A');
await root.loadAttributes();
const files = [];
function walk(f, path = '') {
  const name = path + '/' + f.name;
  if (f.directory) for (const child of f.children ?? []) walk(child, name);
  else files.push({ path: name, size: f.size, file: f });
}
walk(root);
await mkdir('firefly-voice/downloads', { recursive: true });
await writeFile('firefly-voice/downloads/mega-manifest.json', JSON.stringify(files.map(({path,size}) => ({path,size})), null, 2));
const selection = files.filter(f => /语音合成/.test(f.path) && /训练数据|参考语音/.test(f.path));
console.log(JSON.stringify(selection.map(({path,size}) => ({path,size})), null, 2));
if (process.argv[2] === '--download') {
  const chosen = selection.filter(f => f.path === process.argv[3]);
  if (chosen.length !== 1) throw Error('Specify one exact manifest path');
  for (const item of chosen) {
    const dest = resolve('firefly-voice/downloads', basename(item.path));
    console.log('Downloading ' + item.path + ' (' + item.size + ' bytes)');
    await pipeline(item.file.download({ maxConnections: 4 }), createWriteStream(dest));
    console.log('Saved ' + dest);
  }
}
