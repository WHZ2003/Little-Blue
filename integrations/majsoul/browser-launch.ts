import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const run = promisify(execFile);
const starts = new Map<string, Promise<void>>();

export function ensureGameBrowser(dataDir: string, port: number, showWindow = false): Promise<void> {
  if (process.platform !== 'win32' || !Number.isInteger(port) || port < 1024 || port > 65535) {
    return Promise.reject(new Error('雀魂专用浏览器需要 Windows 和有效的调试端口。'));
  }
  const key = `${dataDir}:${port}:${showWindow}`;
  const pending = starts.get(key);
  if (pending) return pending;
  const job = run('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', fileURLToPath(new URL('../setup/start-browser.ps1', import.meta.url)),
    '-Port', String(port), '-ProfileDir', join(dataDir, 'majsoul', 'edge-profile'),
    ...(showWindow ? ['-ShowWindow'] : [])],
    { windowsHide: true, timeout: 35000, maxBuffer: 16384 }).then(() => undefined).catch((error) => {
      const detail = String(error.stderr || error.message).replace(/\x1b\[[0-9;]*m/g, '').trim();
      throw new Error('专用游戏浏览器未能启动：' + detail.slice(0, 700));
    }).finally(() => { starts.delete(key); });
  starts.set(key, job);
  return job;
}
