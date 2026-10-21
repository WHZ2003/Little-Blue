"""One bounded browsing space; no access to the pet's database or API keys."""
import asyncio
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import time
from urllib.parse import urlsplit

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

DOMAINS = ('wikipedia.org', 'wikimedia.org', 'nasa.gov', 'noaa.gov')
SEEDS = ('https://zh.wikipedia.org/wiki/鲸', 'https://zh.wikipedia.org/wiki/海洋',
         'https://science.nasa.gov/earth/', 'https://oceanservice.noaa.gov/facts/')


def allowed(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
                and bool(p.hostname) and any(p.hostname == d or p.hostname.endswith('.' + d) for d in DOMAINS))
    except ValueError:
        return False


async def validate(url):
    if not allowed(url):
        raise PermissionError('仅开放维基百科、NASA 和 NOAA 科普站点的 HTTPS 页面。')
    addresses = await asyncio.get_running_loop().getaddrinfo(urlsplit(url).hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise PermissionError('禁止访问本机或私有网络。')


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['auto', 'pause', 'manual', 'visit', 'click', 'scroll', 'back', 'abort']
    version: int = Field(ge=0)
    url: str = Field('', max_length=2048)
    x: int = Field(0, ge=0, le=1099)
    y: int = Field(0, ge=0, le=699)
    delta: int = Field(500, ge=-700, le=700)


class Browser:
    def __init__(self, directory='/browser'):
        self.directory = Path(directory)
        self.mode, self.version = 'pause', 0
        self.lock = asyncio.Lock()
        self.active = None
        self.page = self.context = self.playwright = None
        self.observation = {'title': '还没有打开网页', 'url': '', 'text': '', 'links': []}
        self.observation_id = 0

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.directory / 'profile'), headless=True, chromium_sandbox=True,
            viewport={'width': 1100, 'height': 700}, accept_downloads=False,
            service_workers='block', permissions=[],
            args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        await self.context.route('**/*', self.route)
        await self.context.route_web_socket('**/*', lambda ws: ws.close())
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.context.on('page', lambda p: asyncio.create_task(p.close()) if p != self.page else None)
        self.page.on('dialog', lambda d: asyncio.create_task(d.dismiss()))
        await self.page.goto('about:blank')
        self.context.set_default_timeout(5000)

    async def route(self, route):
        try:
            if route.request.method not in ('GET', 'HEAD') or route.request.resource_type in ('media', 'websocket'):
                raise PermissionError()
            await asyncio.wait_for(validate(route.request.url), 3)
            await route.continue_()
        except Exception:
            await route.abort()

    def status(self):
        url = self.page.url if self.page and allowed(self.page.url) else ''
        title = self.observation['title'] if url == self.observation['url'] else '页面已变化，尚未读取'
        return {'mode': self.mode, 'version': self.version, 'title': title,
                'url': url, 'observed_at': self.observation.get('observed_at'),
                'busy': self.active is not None, 'domains': DOMAINS, 'seeds': SEEDS}

    async def cancel_active(self):
        task = self.active
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.page:
            try:
                await asyncio.wait_for(self.page.evaluate('window.stop()'), 2)
            except Exception:
                pass

    async def execute(self, command, human=False):
        if command.version != self.version:
            raise ValueError('状态已变化，请刷新后再操作。')
        if command.action in ('auto', 'pause', 'manual', 'abort'):
            if command.action == 'abort' and self.mode != 'auto':
                raise PermissionError('接管后不能取消人的操作。')
            self.version += 1
            if command.action != 'abort':
                self.mode = command.action
            # Change ownership before waiting: queued old actions cannot start.
            await self.cancel_active()
            return self.status()
        if self.mode != ('manual' if human else 'auto'):
            raise PermissionError('先接管浏览器，或让她继续浏览。')
        if not human and command.action != 'visit':
            raise PermissionError('自主浏览仅允许读取页面。')
        async with self.lock:
            if command.version != self.version:
                raise ValueError('操作已过期。')
            self.active = asyncio.current_task()
            try:
                async with asyncio.timeout(20):
                    response = None
                    if command.action == 'visit':
                        await validate(command.url)
                        response = await self.page.goto(command.url, wait_until='domcontentloaded', timeout=15000)
                    elif command.action == 'click':
                        await self.page.mouse.click(command.x, command.y)
                        await self.page.wait_for_timeout(500)
                    elif command.action == 'scroll':
                        await self.page.mouse.wheel(0, command.delta)
                    elif command.action == 'back':
                        response = await self.page.go_back(wait_until='domcontentloaded', timeout=15000)
                    if response and response.status >= 400:
                        raise ValueError('页面没有成功返回，未记录阅读经历。')
                    result = await self.observe()
                    if command.version != self.version:
                        raise ValueError('浏览已暂停或被接管。')
                    return result
            finally:
                self.active = None

    async def observe(self):
        if not allowed(self.page.url):
            raise PermissionError('当前没有可读取的获准页面。')
        result = await self.page.evaluate('''() => ({title: document.title,
            text: (document.querySelector('main, article, #mw-content-text') || document.body).innerText.slice(0,6000),
            links: Array.from(document.querySelectorAll('main a[href], article a[href], #mw-content-text a[href]'))
              .map(a => ({title: a.innerText.trim().slice(0,100), url: a.href}))
              .filter(a => a.title.length > 2).slice(0,150)})''')
        result['url'] = self.page.url
        result['title'] = result['title'][:200]
        links, seen = [], set()
        for link in result['links']:
            if allowed(link['url']) and len(link['url']) <= 2048 and link['url'] not in seen and '#' not in link['url']:
                seen.add(link['url'])
                links.append(link)
        result['links'] = links[:16]
        result['observed_at'] = time.time()
        self.observation_id += 1
        result['id'] = self.observation_id
        self.observation = result
        return result

    async def close(self):
        self.mode = 'pause'
        self.version += 1
        await self.cancel_active()
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


def create_app(browser, token):
    tickets, sessions = {}, {}

    @web.middleware
    async def boundary(request, handler):
        try:
            if request.path.startswith('/internal/'):
                if request.headers.get('Origin') or not hmac.compare_digest(
                        request.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
                    raise web.HTTPUnauthorized()
            elif request.path.startswith('/api/'):
                if sessions.get(request.cookies.get('browser_session'), 0) < time.monotonic():
                    raise web.HTTPUnauthorized()
                if request.headers.get('Host') not in ('127.0.0.1:8766', 'localhost:8766'):
                    raise web.HTTPForbidden()
                if request.method == 'POST' and (request.headers.get('X-Browser-Control') != '1'
                    or request.headers.get('Origin') not in ('http://127.0.0.1:8766', 'http://localhost:8766')):
                    raise web.HTTPForbidden()
            response = await handler(request)
        except asyncio.CancelledError:
            # Explicit ownership changes cancel a visit; the HTTP server remains alive.
            response = web.json_response({'error': '浏览已暂停或被接管。'}, status=409)
        except (ValueError, ValidationError, PermissionError) as error:
            response = web.json_response({'error': str(error)[:180]}, status=400)
        except web.HTTPException as error:
            response = web.Response(status=error.status, text=error.text)
        except Exception:
            response = web.json_response({'error': '浏览器操作未完成，请刷新或稍后重试。'}, status=503)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'"
        return response

    app = web.Application(middlewares=[boundary], client_max_size=8192)

    async def ticket(request):
        now = time.monotonic()
        for key in list(tickets):
            if tickets[key] < now:
                del tickets[key]
        if len(tickets) >= 20:
            raise ValueError('查看入口请求过多。')
        value = secrets.token_urlsafe(32)
        tickets[value] = now + 60
        return web.json_response({'url': 'http://127.0.0.1:8766/?ticket=' + value})

    async def home(request):
        if 'ticket' in request.query:
            if tickets.pop(request.query['ticket'], 0) < time.monotonic():
                raise web.HTTPUnauthorized(text='入口已过期，请从桌宠重新打开。')
            now = time.monotonic()
            for key in list(sessions):
                if sessions[key] < now:
                    del sessions[key]
            if len(sessions) >= 10:
                sessions.pop(next(iter(sessions)))
            value = secrets.token_urlsafe(32)
            sessions[value] = now + 43200
            response = web.Response(status=302, headers={'Location': '/'})
            response.set_cookie('browser_session', value, httponly=True, samesite='Strict', max_age=43200)
            return response
        return web.Response(text=Path(__file__).with_name('viewer.html').read_text(encoding='utf-8'), content_type='text/html')

    async def status(request):
        return web.json_response(browser.status())

    async def control(request):
        command = Operation.model_validate(await request.json())
        return web.json_response(await browser.execute(command, human=request.path.startswith('/api/')))

    async def screenshot(request):
        async with asyncio.timeout(4):
            data = await browser.page.screenshot(type='jpeg', quality=70, timeout=3000)
        return web.Response(body=data, content_type='image/jpeg')

    async def health(request):
        return web.json_response({'status': 'ok' if browser.page and not browser.page.is_closed() else 'starting'})

    app.router.add_get('/', home)
    app.router.add_get('/healthz', health)
    app.router.add_post('/internal/ticket', ticket)
    app.router.add_get('/internal/status', status)
    app.router.add_post('/internal/control', control)
    app.router.add_get('/api/status', status)
    app.router.add_post('/api/control', control)
    app.router.add_get('/api/screenshot', screenshot)

    async def lifecycle(app):
        await browser.start()
        try:
            yield
        finally:
            await browser.close()
    app.cleanup_ctx.append(lifecycle)
    return app


if __name__ == '__main__':
    secret = Path('/run/secrets/browser_token').read_text().strip()
    if len(secret) < 32:
        raise ValueError('Browser token not configured')
    web.run_app(create_app(Browser(), secret), host='0.0.0.0', port=8080, access_log=None, print=None)
