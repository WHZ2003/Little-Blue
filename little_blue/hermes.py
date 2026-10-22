"""Hermes owns the agent loop; the application owns permissions, billing and delivery."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import time

import httpx
from aiohttp import web

from .errors import CognitiveError
from .tools import schemas


def conversation_parts(messages):
    """Keep turn-specific system guidance after history instead of hoisting it."""
    boundary = next((i for i, m in enumerate(messages) if m['role'] != 'system'), len(messages))
    return ('\n\n'.join(m['content'] for m in messages[:boundary]),
            messages[boundary:-1])


class HermesBridge:
    def __init__(self, agent):
        self.agent = agent
        self.url = os.environ['LITTLE_BLUE_HERMES_URL'].rstrip('/')
        self.token_file = Path(os.environ.get('LITTLE_BLUE_HERMES_TOKEN_FILE', '/run/secrets/hermes_token'))
        self.active = None

    def authorized(self, supplied):
        return bool(self.active and hmac.compare_digest(supplied, 'Bearer ' + self.active['token']))

    async def run(self, messages, allow_tools, task_id, *, extra_tools=(), tool_handler=None, private_memory=True):
        if self.active:
            raise RuntimeError('Hermes already has an active turn')
        token = secrets.token_hex(32)
        mapped = {}
        tools = [{'type': 'function', 'function': {
            'name': 'dafeiyu_memory_search',
            'description': 'Search long-term memories and confirmed chat history, including records from before Hermes migration.',
            'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'maxLength': 300}},
                           'required': ['query'], 'additionalProperties': False}}}]
        if not private_memory:
            tools = []
        tools.extend(extra_tools)
        if allow_tools:
            for schema in schemas():
                definition = dict(schema['function'])
                original = definition['name']
                definition['name'] = 'dafeiyu_' + original
                mapped[definition['name']] = original
                tools.append({'type': 'function', 'function': definition})
        self.active = {'token': token, 'tools': mapped, 'calls': 0, 'private_memory': private_memory,
                       'extra': {t['function']['name'] for t in extra_tools}, 'handler': tool_handler}
        headers = {'Authorization': 'Bearer ' + self.token_file.read_text().strip()}
        system, history = conversation_parts(messages)
        payload = {'run_token': token, 'task_id': task_id, 'model': self.agent.settings.model,
                   'persona': messages[0]['content'],
                   'system': system, 'history': history,
                   'text': messages[-1]['content'], 'tools': tools, 'private_memory': private_memory}
        self.agent.store.audit('hermes_turn', 'started')
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=230) as client:
                response = await client.post(self.url + '/run', headers=headers, json=payload)
                if response.status_code != 200:
                    raise CognitiveError('Hermes 未完成这次任务，请稍后重试。不会自动重发微信消息。')
                result = response.json()
                if not result.get('ok') or not isinstance(result.get('content'), str):
                    raise CognitiveError('Hermes 返回了无效结果。')
                self.agent.store.audit('hermes_turn', 'succeeded')
                return {'role': 'assistant', 'content': result['content'], 'api_calls':self.active['calls']}
        except asyncio.CancelledError:
            self.agent.store.audit('hermes_turn', 'cancelled')
            raise
        except Exception:
            self.agent.store.audit('hermes_turn', 'failed')
            raise
        finally:
            self.active = None  # Revoke provider/tool authority before asking the worker to exit.
            async def cancel():
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                        await client.post(self.url + '/cancel', headers=headers, json={'run_token': token})
                except httpx.HTTPError:
                    pass  # Worker also has a hard deadline and no longer holds a valid capability.
            await asyncio.shield(cancel())

    async def complete(self, body):
        active = self.active
        if not active or not self.agent.settings.online:
            raise PermissionError('Turn is no longer authorized')
        if active['calls'] >= 5:
            raise CognitiveError('任务超过模型调用上限，已停止。')
        if body.get('stream'):
            raise ValueError('This adapter uses non-streaming model calls')
        messages = body.get('messages')
        if not isinstance(messages, list) or not messages or len(messages) > 100:
            raise ValueError('Invalid conversation')
        active['calls'] += 1
        # This client enforces the existing persistent request/token budget and model setting.
        message = await self.agent.client.complete(messages, body.get('tools'))
        if self.active is not active:
            raise PermissionError('Turn was cancelled')
        return {'id': 'chatcmpl-' + secrets.token_hex(12), 'object': 'chat.completion',
                'created': int(time.time()), 'model': self.agent.settings.model,
                'choices': [{'index': 0, 'message': message,
                             'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]}

    async def tool(self, body):
        active = self.active
        if not active or not self.agent.settings.online:
            raise PermissionError('Turn is no longer authorized')
        name, args = body.get('name'), body.get('arguments')
        if not isinstance(args, dict):
            raise ValueError('Invalid tool arguments')
        if name in active.get('extra', ()) and active.get('handler'):
            return await active['handler'](name, args)
        if name == 'dafeiyu_memory_search' and active.get('private_memory', True):
            query = args.get('query')
            if not isinstance(query, str) or len(query) > 300:
                raise ValueError('Invalid search')
            memories = await self.agent.store.search_hybrid(query, 8)
            pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            with self.agent.store.lock:
                rows = self.agent.store.db.execute(
                    "SELECT c.role,c.origin,c.text,c.created FROM conversation c "
                    "LEFT JOIN conversation_inbox i ON i.message_id=c.id "
                    "WHERE c.text LIKE ? ESCAPE '\\' AND c.delivery IN ('legacy','delivered') "
                    "AND COALESCE(i.state,'') NOT IN ('received','processing') ORDER BY c.id DESC LIMIT 10", (pattern,)).fetchall()
            return {'ok': True, 'data': {'memories': memories, 'conversation': [dict(row) for row in reversed(rows)]}}
        if name not in active['tools']:
            raise PermissionError('Tool is not available for this turn')
        try:
            result = await self.agent.tools.call(active['tools'][name], args)
            return {'ok': True, 'data': result}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__}


def install_routes(app, core):
    async def dispatch(request):
        bridge = getattr(core.agent, 'harness', None)
        if not bridge or not bridge.authorized(request.headers.get('Authorization', '')):
            return web.json_response({'error': 'unauthorized'}, status=401)
        body = await request.json()
        try:
            result = await (bridge.tool(body) if request.path.endswith('/tool') else bridge.complete(body))
            return web.json_response(result)
        except CognitiveError:
            return web.json_response({'error': {'message': 'Provider or application budget refused this request', 'type': 'application_error'}}, status=429)
    app.router.add_post('/harness/v1/chat/completions', dispatch)
    app.router.add_post('/harness/tool', dispatch)
