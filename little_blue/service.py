"""Authenticated loopback-facing HTTP boundary for the container core."""
import argparse
import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from aiohttp import web
from pydantic import Field, ValidationError
from .core import Core
from .models import Model, Environment, Settings
from .storage import Store
from .phone import PhoneCommand, GameError
from .memory_ledger import MemoryCommand

CORE_KEY = web.AppKey('core', Core)

class Command(Model):
    op: Literal['sync', 'configure', 'interact', 'direction', 'submit', 'cancel',
                'resolve', 'search', 'remember', 'audit', 'disconnect', 'identity', 'phone', 'memory_index', 'memory_manage', 'browser', 'bridge']
    host: str = Field(pattern=r'^[a-f0-9]{32}$')
    after: int = Field(0, ge=0)
    environment: Environment | None = None
    settings: Settings | None = None
    interaction: Literal['pet', 'click', 'drag', 'release', 'feed'] | None = None
    direction: Literal[-1, 1] | None = None
    text: str = Field('', max_length=4000)
    task_id: str = Field('', max_length=64, pattern=r'^[a-f0-9]*$')
    reflection: bool = False
    token: str = Field('', max_length=64)
    value: Any = None
    error: str | None = Field(None, max_length=400)
    kind: Literal['semantic', 'episodic'] = 'semantic'
    identity_action: Literal['view', 'on', 'off', 'undo'] = 'view'
    revision: int | None = Field(None, ge=0)
    phone: PhoneCommand | None = None
    memory_action: Literal['status', 'on', 'off', 'rebuild'] = 'status'
    memory_command: MemoryCommand | None = None
    browser_action: Literal['view', 'status', 'auto', 'pause', 'manual', 'share_now'] = 'status'
    bridge_action: Literal['status', 'bind', 'activate', 'pause', 'resume', 'ilink'] = 'status'


def create_app(store, token, client=None, wechat_path=None, bridge_token=None):
    if len(token) < 32:
        raise ValueError('Core token must contain at least 32 characters')
    core = Core(store, client)

    @web.middleware
    async def boundary(request, handler):
        # No browser origins/CORS: this is a native desktop API.
        if request.headers.get('Origin'):
            return web.json_response({'error': 'browser_origin_denied'}, status=403)
        if request.path != '/healthz' and not request.path.startswith('/harness/'):
            supplied = request.headers.get('Authorization', '')
            expected = bridge_token if request.path == '/bridge' else token
            if not expected or not hmac.compare_digest(supplied.encode(), ('Bearer ' + expected).encode()):
                return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            return await handler(request)
        except (ValidationError, ValueError, KeyError, TypeError):
            return web.json_response({'error': 'invalid_request'}, status=400)
        except PermissionError:
            return web.json_response({'error': 'session_or_permission_denied'}, status=403)
        except RuntimeError:
            return web.json_response({'error': 'operation_conflict'}, status=409)
        except web.HTTPException:
            raise
        except Exception as error:
            logging.getLogger(__name__).error('Core request failed: %s', type(error).__name__)
            return web.json_response({'error': 'internal_error'}, status=500)

    app = web.Application(middlewares=[boundary], client_max_size=128 * 1024)
    app[CORE_KEY] = core
    from .hermes import install_routes
    install_routes(app, core)

    async def health(request):
        alive = core.loop_task is not None and not core.loop_task.done()
        return web.json_response({'status': 'ok' if alive else 'failed'}, status=200 if alive else 503)

    async def state(request):
        return web.json_response(core.snapshot())

    async def rpc(request):
        command = Command.model_validate(await request.json())
        if core.handoff_requested and command.op not in ('sync', 'resolve', 'cancel', 'audit', 'disconnect'):
            raise RuntimeError('Handoff in progress')
        if command.op == 'sync':
            if command.environment is None:
                raise ValueError('missing environment')
            core.claim_host(command.host, command.environment.model_dump())
            return web.json_response(core.snapshot(command.after, command.host))
        if command.op == 'browser':
            if command.browser_action == 'share_now':
                return web.json_response(await core.browser_share.request_once())
            return web.json_response(await core.browser.control(command.browser_action))
        if command.op == 'bridge':
            return web.json_response(await core.bridge.admin(command.bridge_action, command.value))
        core.require_host(command.host)
        op = command.op
        if op == 'memory_manage':
            cmd = command.memory_command
            if cmd is None:
                raise ValueError('Missing memory command')
            if cmd.action == 'search':
                await core.store.search_hybrid(cmd.query, 20)
            return web.json_response(core.store.ledger.control(cmd))
        if op == 'phone':
            if command.phone is None:
                raise ValueError('missing phone command')
            try:
                return web.json_response(core.phone.command(command.phone.model_dump(), core.life))
            except GameError as error:
                return web.json_response({'error': 'game_rule', 'message': str(error)[:160]}, status=400)
        elif op == 'identity':
            result = (core.store.growth.snapshot() if command.identity_action == 'view' else
                      core.store.growth.control(command.identity_action, command.revision))
            return web.json_response(result)
        elif op == 'configure':
            if command.settings is None:
                raise ValueError('missing settings')
            await core.configure(command.settings.model_dump())
        elif op == 'interact':
            if command.interaction is None:
                raise ValueError('missing interaction')
            core.interact(command.interaction)
        elif op == 'direction':
            if command.direction is None:
                raise ValueError('missing direction')
            core.life.direction = command.direction
        elif op == 'submit':
            if not command.text or not command.task_id:
                raise ValueError('missing task')
            return web.json_response({'id': core.submit(command.text, command.task_id, reflection=command.reflection)})
        elif op == 'cancel':
            await core.cancel()
        elif op == 'resolve':
            core.resolve(command.token, command.value, command.error)
        elif op == 'search':
            return web.json_response({'rows': await core.store.search_hybrid(command.text, 20)})
        elif op == 'memory_index':
            return web.json_response(await core.memory.control(command.memory_action))
        elif op == 'remember':
            core.store.remember(command.text, command.kind)
        elif op == 'audit':
            # Narrow host audit input; never arbitrary SQL or state writes.
            core.store.audit(command.text[:64], str(command.value)[:64])
        elif op == 'disconnect':
            if core.current and core.current.get('origin') != 'wechat':
                await core.cancel()
            core.last_host = float('-inf')
        return web.json_response({'ok': True})

    async def lifecycle(application):
        await core.start()
        channel = None
        if wechat_path is not None:
            from .wechat import supervise
            channel = asyncio.create_task(supervise(core, wechat_path))
        try:
            yield
        finally:
            if channel:
                channel.cancel()
                await asyncio.gather(channel, return_exceptions=True)
            await core.close()
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get('/healthz', health)
    app.router.add_get('/state', state)
    app.router.add_post('/rpc', rpc)
    async def handoff(request):
        if await request.json() != {'action': 'prepare'}:
            raise ValueError('Invalid handoff operation')
        return web.json_response(await core.prepare_handoff())
    app.router.add_post('/handoff', handoff)
    async def bili(request):
        return web.json_response(await core.bili.control(await request.json()))
    app.router.add_post('/bili', bili)
    class DiaryCommand(Model):
        action: Literal['status', 'list', 'read', 'write', 'on', 'off'] = 'status'
        day: str | None = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')

    async def diary(request):
        cmd = DiaryCommand.model_validate(await request.json())
        if cmd.action in ('write', 'on', 'off') and core.handoff_requested:
            raise RuntimeError('Handoff in progress')
        if cmd.action == 'write':
            ident = core.diary.start(cmd.day)
            return web.json_response({'task_id': ident, 'entry': None if ident else core.diary.read(cmd.day or core.diary.today())})
        if cmd.action == 'read':
            return web.json_response({'entry': core.diary.read(cmd.day)})
        if cmd.action == 'list':
            return web.json_response({'entries': core.diary.rows('SELECT day,updated FROM diaries ORDER BY day DESC LIMIT 30')})
        if cmd.action in ('on', 'off'):
            await core.diary.command(cmd.action)
        return web.json_response(core.diary.status())
    app.router.add_post('/diary', diary)
    from .bridge import Incoming, Strict
    class BridgeCommand(Strict):
        action: Literal['heartbeat', 'receive', 'pull', 'begin', 'ack']
        instance: str = Field(pattern=r'^[a-f0-9]{32}$')
        account: str = Field('', max_length=128)
        version: str = Field('', max_length=40)
        status: Literal['ready','locked','disconnected','logged_out','unreadable'] = 'unreadable'
        event: Incoming | None = None
        id: str = Field('', max_length=64)
        result: Literal['sent','failed','unknown'] = 'unknown'

    async def bridge_rpc(request):
        if core.handoff_requested:
            raise RuntimeError('Handoff in progress')
        cmd = BridgeCommand.model_validate(await request.json())
        hub = core.bridge
        if cmd.action == 'heartbeat':
            result = hub.heartbeat(cmd.account, cmd.instance, cmd.version, cmd.status)
        elif cmd.action == 'receive':
            if cmd.instance != hub.instance or cmd.event is None:
                raise PermissionError('Bridge instance mismatch')
            result = hub.receive(cmd.event)
        else:
            result = hub.delivery(cmd.instance, cmd.action, cmd.id, cmd.result)
        return web.json_response(result)
    app.router.add_post('/bridge', bridge_rpc)
    return app


def main():
    parser = argparse.ArgumentParser(description='Little Blue headless core')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=Path(os.getenv('LITTLE_BLUE_DATA_DIR', 'data/core')))
    parser.add_argument('--token-file', type=Path, default=Path(os.getenv('LITTLE_BLUE_TOKEN_FILE', '.secrets/core_token')))
    args = parser.parse_args()
    from .handoff_guard import require_active
    require_active(args.data_dir)
    token = args.token_file.read_text(encoding='utf-8').strip()
    if len(token) < 32:
        parser.error('Generate a core token with tools/init_core.py first')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    store = Store(args.data_dir / 'memory.sqlite3')
    bridge_file = Path(os.getenv('LITTLE_BLUE_BRIDGE_TOKEN_FILE', '/run/secrets/bridge_token'))
    bridge_token = bridge_file.read_text().strip() if bridge_file.exists() else None
    web.run_app(create_app(store, token, wechat_path=args.data_dir / 'wechat-account.json', bridge_token=bridge_token), host=args.host, port=args.port,
                access_log=None, print=None, shutdown_timeout=8)


if __name__ == '__main__':
    main()
