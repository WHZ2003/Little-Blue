"""Add optional local Seed-VC conversion to Fatfish 0.3.8; preserve its TTS fallback."""
import argparse
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / '.research/Coopanion/data/extensions/node_modules/cortico-world-fatfish'
MARKER = '// DaFeiYu: firefly Seed-VC voice v1'


def replace(s, old, new):
    if s.count(old) != 1:
        raise ValueError('Source changed; inspect before patching: ' + old[:100])
    return s.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=PACKAGE)
    args = parser.parse_args()
    package = args.package
    if json.loads((package / 'package.json').read_text(encoding='utf-8'))['version'] != '0.3.8':
        raise ValueError('Review patch compatibility for this plugin version')
    files = {n: (package / 'src' / n).read_text(encoding='utf-8') for n in ['speaker.ts', 'config.ts', 'world.ts']}
    s = files['speaker.ts']
    if MARKER not in s:
        s = MARKER + "\nimport { convertClip, type ConversionOptions } from './fatfish-converter.ts';\n" + s
        s = replace(s, 'export interface SpeakOptions {', 'export interface SpeakOptions extends ConversionOptions {')
        s = replace(s, '  private tts:', '  private speechGeneration = 0;\n  private converting?: AbortController;\n  private tts:')
        s = replace(s, '''    const clip = await this.synthesise(text, opts, log);
    if (!clip) return false;
    await this.play(clip, log);
    await rm(clip, { force: true });
    return true;''', '''    const generation = this.speechGeneration;
    const clip = await this.synthesise(text, opts, log);
    if (!clip) return false;
    let converted = clip;
    const controller = new AbortController();
    this.converting = controller;
    try {
      if (generation !== this.speechGeneration) return false;
      converted = await convertClip(clip, opts, log, controller.signal);
      if (generation !== this.speechGeneration || controller.signal.aborted) return false;
      this.converting = undefined;
      await this.play(converted, log);
      return true;
    } catch (error) {
      if (!controller.signal.aborted) log('fatfish voice failed: ' + String(error));
      return false;
    } finally {
      if (this.converting === controller) this.converting = undefined;
      await rm(clip, { force: true }).catch(() => {});
      if (converted !== clip) await rm(converted, { force: true }).catch(() => {});
    }''')
        s = replace(s, '  hush(): void {', '  hush(): void {\n    this.speechGeneration++;\n    this.converting?.abort();')
        s = replace(s, 'const quoted = `"${clip}"`;', 'const quoted = `"${clip}"`;\n        const mediaType = clip.endsWith(".wav") ? "waveaudio" : "mpegvideo";')
        s = replace(s, 'type mpegvideo alias tts', 'type ${mediaType} alias tts')
        files['speaker.ts'] = s
        cfg = files['config.ts']
        cfg = replace(cfg, '  ttsEnabled: boolean;', '  ttsEnabled: boolean;\n  seedVcEnabled: boolean;\n  seedVcEndpoint: string;\n  seedVcLauncher: string;')
        cfg = replace(cfg, '  ttsEnabled: true,', "  ttsEnabled: true,\n  seedVcEnabled: false,\n  seedVcEndpoint: 'http://127.0.0.1:18892',\n  seedVcLauncher: '',")
        cfg = replace(cfg, "          [at('ttsEnabled')]:", "          [at('seedVcEnabled')]: { type: 'boolean', title: '流萤音色（本地 Seed-VC）', description: '原语音合成后转换；服务失败时使用原音色。', 'x-hot': true },\n          [at('seedVcEndpoint')]: { type: 'string', title: '本地音色服务地址', 'x-hot': true },\n          [at('seedVcLauncher')]: { type: 'string', title: '本地音色服务启动脚本', 'x-hot': true },\n          [at('ttsEnabled')]:")
        files['config.ts'] = cfg
        files['world.ts'] = replace(files['world.ts'], "      voice: str(cfg.voice, DEFAULT_VOICE),", "      voice: str(cfg.voice, DEFAULT_VOICE),\n      seedVcEnabled: cfg.seedVcEnabled === true,\n      seedVcEndpoint: str(cfg.seedVcEndpoint, 'http://127.0.0.1:18892'),\n      seedVcLauncher: str(cfg.seedVcLauncher, ''),")
    files['fatfish-converter.ts'] = (ROOT / 'firefly-voice/fatfish-converter.ts').read_text(encoding='utf-8')
    changes = {n: s for n, s in files.items() if not (package / 'src' / n).exists() or (package / 'src' / n).read_text(encoding='utf-8') != s}
    backup = ROOT / 'backups' / ('firefly-voice-' + str(time.time_ns()))
    for name, content in changes.items():
        dest = package / 'src' / name
        if dest.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup / name)
        temporary = dest.with_suffix('.ts.dafeiyu-tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(dest)
    print(json.dumps({'patched': list(changes), 'backup': str(backup) if changes else None}))


if __name__ == '__main__':
    main()
