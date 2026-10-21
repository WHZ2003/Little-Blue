"""Validated tool registry. Every invocation crosses a revocable permission gate."""
import asyncio
import ctypes
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlparse
import webbrowser
import xml.etree.ElementTree as ET
import httpx
from pydantic import Field
from .models import Model
from .perception import perceive


class Empty(Model):
    pass


class ReadFile(Model):
    path: str = Field(min_length=1, max_length=1024)


class Search(Model):
    query: str = Field(min_length=1, max_length=300)


class Reminder(Model):
    text: str = Field(min_length=1, max_length=500)
    seconds: int = Field(ge=1, le=31536000)


class Browser(Model):
    url: str = Field(min_length=1, max_length=2000)


class SystemAction(Model):
    action: str = Field(pattern="^lock_screen$")


SPECS = {
    "get_time": (Empty, "Get the local time", False),
    "get_activity": (Empty, "Get idle seconds and focus status if local perception is enabled", False),
    "web_search": (Search, "Search Bing public web results; requires approval to send the query", True),
    "create_reminder": (Reminder, "Create a local reminder, delivered while the app is running", True),
    "read_file": (ReadFile, "Read one approved local UTF-8 text file (64 KiB maximum); contents sent to the model", True),
    "browser_open": (Browser, "Open an approved public HTTPS URL in the user's browser", True),
    "browser_read": (Browser, "Read a public web page in isolated Playwright Chromium; requires optional browser installation", True),
    "system_action": (SystemAction, "Lock Windows session after explicit approval; no arbitrary shell commands", True),
}


def schemas():
    return [{"type": "function", "function": {"name": name, "description": desc,
            "parameters": model.model_json_schema()}} for name, (model, desc, _) in SPECS.items()]


async def validate_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError("仅允许无凭据的公开 HTTPS 地址。")
    addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443), 5)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError("不允许访问本机、局域网或保留地址。")
    return url


class ToolRuntime:
    def __init__(self, store, settings, approve):
        self.store, self.settings, self.approve = store, settings, approve

    async def call(self, name, raw):
        if not self.settings.tools_enabled:
            self.store.audit(name, "disabled")
            raise PermissionError("工具访问已关闭。")
        if name not in SPECS:
            self.store.audit(name[:64], "unknown")
            raise PermissionError("未知工具。")
        model, description, needs_approval = SPECS[name]
        args = model.model_validate(raw)
        if isinstance(args, ReadFile):
            p = Path(args.path)
            if not p.is_absolute() or str(p).startswith(("\\\\", "//")):
                raise ValueError("仅允许本机绝对文件路径。")
            p = p.resolve(strict=True)
            if not p.is_file() or ":" in str(p)[2:]:
                raise ValueError("必须是普通文件。")
            args.path = str(p)
        if needs_approval:
            frozen = args.model_dump_json()
            self.store.audit(name, "approval_requested")
            approved = await asyncio.wait_for(self.approve(name, json.loads(frozen), description), 120)
            if not approved:
                self.store.audit(name, "denied")
                raise PermissionError("用户拒绝了本次调用。")
            args = model.model_validate_json(frozen)
        # A user may revoke access while an approval is pending.
        if not self.settings.tools_enabled:
            raise PermissionError("工具权限已撤回。")
        self.store.audit(name, "allowed")
        try:
            async with asyncio.timeout(25):
                result = await self.execute(name, args)
            self.store.audit(name, "succeeded")
            return result
        except asyncio.CancelledError:
            self.store.audit(name, "cancelled")
            raise
        except Exception:
            self.store.audit(name, "failed")
            raise

    async def execute(self, name, args):
        if name == "get_time":
            return datetime.now().astimezone().isoformat()
        if name == "get_activity":
            if not self.settings.perception:
                raise PermissionError("环境感知未开启。")
            return perceive(True, self.settings.dnd).model_dump()
        if name == "create_reminder":
            return {"id": self.store.add_reminder(args.text, time.time() + args.seconds), "status": "scheduled"}
        if name == "read_file":
            # Re-resolve immediately before reading to reject swapped symlinks.
            p = Path(args.path)
            if str(p.resolve(strict=True)) != args.path:
                raise PermissionError("审批后文件路径发生变化。")
            def read():
                with p.open("rb") as file:
                    data = file.read(65537)
                if len(data) > 65536 or b"\x00" in data:
                    raise ValueError("仅支持 64 KiB 以内的 UTF-8 文本。")
                return data.decode("utf-8-sig")
            return await asyncio.to_thread(read)
        if name == "web_search":
            async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", "https://www.bing.com/search", params={"q": args.query, "format": "rss"}) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for part in response.aiter_bytes():
                        content.extend(part)
                        if len(content) > 500000:
                            raise ValueError("搜索响应过大。")
            root = ET.fromstring(content)
            return [{"title": x.findtext("title"), "url": x.findtext("link"),
                     "snippet": (x.findtext("description") or "")[:800]} for x in root.findall("./channel/item")[:5]]
        if name == "browser_open":
            await validate_url(args.url)
            ok = await asyncio.to_thread(webbrowser.open, args.url)
            return {"opened": bool(ok)}
        if name == "browser_read":
            await validate_url(args.url)
            try:
                from playwright.async_api import async_playwright
            except ImportError as error:
                raise RuntimeError("请安装 browser 可选依赖及 Playwright Chromium。") from error
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(accept_downloads=False, service_workers="block", java_script_enabled=False)
                    async def route(request_route):
                        try:
                            await validate_url(request_route.request.url)
                            if request_route.request.resource_type not in ("document", "stylesheet"):
                                await request_route.abort()
                            else:
                                await request_route.continue_()
                        except Exception:
                            await request_route.abort()
                    await context.route("**/*", route)
                    page = await context.new_page()
                    await page.goto(args.url, wait_until="domcontentloaded", timeout=15000)
                    return {"url": page.url, "text": (await page.locator("body").inner_text(timeout=3000))[:12000]}
                finally:
                    await asyncio.wait_for(browser.close(), 5)
        if name == "system_action":
            if sys.platform != "win32":
                raise RuntimeError("仅 Windows 支持锁屏。")
            if not ctypes.windll.user32.LockWorkStation():
                raise RuntimeError("Windows 拒绝锁屏。")
            return {"locked": True}
        raise PermissionError("没有工具实现。")
