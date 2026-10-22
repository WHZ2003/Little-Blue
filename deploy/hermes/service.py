"""Private single-turn host for the pinned upstream Hermes agent."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import signal
import sys

from fastapi import FastAPI, HTTPException, Request
import uvicorn

app = FastAPI()
jobs = {}
admission = asyncio.Lock()
TOKEN = Path(os.environ.get('HERMES_TOKEN_FILE', '/run/secrets/hermes_token')).read_text().strip()


def authenticate(request):
    if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + TOKEN):
        raise HTTPException(401, 'unauthorized')


async def stop(process):
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), 3)
        except ProcessLookupError:
            pass
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


@app.get('/healthz')
async def health():
    return {'status': 'ok', 'harness': 'hermes', 'revision': '55ee4acdd822b73fb9573a2321531ae3a9d0b3e0'}


@app.post('/cancel')
async def cancel(request: Request):
    authenticate(request)
    data = await request.json()
    process = jobs.get(data.get('run_token'))
    if process:
        await stop(process)
    return {'ok': True}


@app.post('/run')
async def run(request: Request):
    authenticate(request)
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(413, 'turn too large')
    data = json.loads(raw)
    key = data.get('run_token', '')
    if not isinstance(key, str) or len(key) != 64:
        raise HTTPException(400, 'invalid turn')
    if admission.locked():
        raise HTTPException(409, 'another turn is active')
    await admission.acquire()
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '/adapter/worker.py',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    except BaseException:
        admission.release()
        raise
    jobs[key] = process
    try:
        output, _ = await asyncio.wait_for(process.communicate(raw), 225)
        if process.returncode != 0 or len(output) > 256 * 1024:
            raise HTTPException(502, 'Hermes turn failed; see private runtime logs')
        result = json.loads(output)
        if not result.get('ok'):
            raise HTTPException(502, 'Hermes could not complete this turn')
        return result
    except TimeoutError:
        raise HTTPException(504, 'Hermes turn timed out') from None
    finally:
        await stop(process)
        jobs.pop(key, None)
        admission.release()


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8787, access_log=False, log_level='warning')
