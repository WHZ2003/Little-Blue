import { spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';

export interface ConversionOptions {
  seedVcEnabled?: boolean;
  seedVcEndpoint?: string;
  seedVcLauncher?: string;
}
let launching: Promise<void> | undefined;
let failedAt = 0;

function endpoint(value?: string): string {
  const url = new URL(value || 'http://127.0.0.1:18892');
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw Error('Seed-VC endpoint must be a local loopback HTTP origin');
  }
  return url.origin;
}

async function ready(origin: string): Promise<boolean> {
  try {
    const r = await fetch(origin + '/health', { signal: AbortSignal.timeout(700) });
    const data = await r.json() as { service?: string; ready?: boolean };
    return r.ok && data.service === 'dafeiyu-firefly-seed-vc' && data.ready === true;
  } catch { return false; }
}

async function ensureReady(origin: string, launcher?: string): Promise<void> {
  if (await ready(origin)) return;
  if (!launcher || process.platform !== 'win32') throw Error('Voice service is not ready');
  launching ??= new Promise<void>((resolve, reject) => {
    const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', launcher, '-WaitReady'], { windowsHide: true, stdio: 'ignore' });
    const timer = setTimeout(() => { child.kill(); reject(Error('Voice service startup timed out')); }, 60000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(Error('Voice service failed to start')); });
  }).finally(() => { launching = undefined; });
  await launching;
  if (!await ready(origin)) throw Error('Unexpected voice service');
}

/** Falls back to the original clip; cancellation must never play the fallback. */
export async function convertClip(source: string, opts: ConversionOptions, log: (line: string) => void, signal?: AbortSignal): Promise<string> {
  if (!opts.seedVcEnabled || Date.now() - failedAt < 20000) return source;
  try {
    signal?.throwIfAborted();
    const origin = endpoint(opts.seedVcEndpoint);
    await ensureReady(origin, opts.seedVcLauncher);
    signal?.throwIfAborted();
    const timeout = AbortSignal.timeout(30000);
    const response = await fetch(origin + '/convert', {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Fatfish-Voice': '1' },
      body: new Uint8Array(await readFile(source)), signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (!response.ok) throw Error(`Voice conversion HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (data.length < 44 || data.length > 5_000_000 || data.toString('ascii', 0, 4) !== 'RIFF' || data.toString('ascii', 8, 12) !== 'WAVE') throw Error('Invalid converted audio');
    signal?.throwIfAborted();
    const destination = source + '.firefly.wav';
    await writeFile(destination, data);
    log('fatfish voice: Seed-VC firefly-zh-v1 converted');
    return destination;
  } catch (error) {
    if (signal?.aborted) throw error;
    failedAt = Date.now();
    log(`fatfish voice: Seed-VC unavailable, using original Edge voice (${error instanceof Error ? error.message : String(error)})`);
    return source;
  }
}
