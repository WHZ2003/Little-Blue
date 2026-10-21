import asyncio
from pathlib import Path
import pytest
from little_blue.models import Settings
from little_blue.storage import Store
from little_blue.tools import ToolRuntime, validate_url


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path / 'pet.db')
    async def deny(*args):
        return False
    runtime = ToolRuntime(store, Settings(tools_enabled=True), deny)
    yield runtime
    store.close()


async def test_disabled_tools_fail_closed(runtime):
    runtime.settings.tools_enabled = False
    with pytest.raises(PermissionError):
        await runtime.call('get_time', {})


async def test_unknown_tool_and_arbitrary_command_rejected(runtime):
    with pytest.raises(PermissionError):
        await runtime.call('shell', {'command': 'echo pwned'})
    with pytest.raises(ValueError):
        await runtime.call('system_action', {'action': 'cmd /c echo pwned'})
    with pytest.raises(ValueError):
        await runtime.call('get_time', {'unexpected': True})


async def test_denied_reminder_has_no_side_effect(runtime):
    with pytest.raises(PermissionError):
        await runtime.call('create_reminder', {'text': 'test', 'seconds': 1})
    assert runtime.store.due_reminders(1e20) == []


async def test_approval_cannot_change_frozen_arguments(runtime):
    async def approve(name, args, desc):
        args['text'] = 'changed'
        return True
    runtime.approve = approve
    await runtime.call('create_reminder', {'text': 'original', 'seconds': 1})
    assert runtime.store.due_reminders(1e20)[0]['text'] == 'original'


async def test_revocation_while_approval_pending(runtime):
    async def revoke(*args):
        runtime.settings.tools_enabled = False
        return True
    runtime.approve = revoke
    with pytest.raises(PermissionError):
        await runtime.call('create_reminder', {'text': 'test', 'seconds': 1})
    assert runtime.store.due_reminders(1e20) == []


async def test_file_requires_approval_and_size_limit(runtime, tmp_path):
    path = tmp_path / 'allowed.txt'
    path.write_text('a lovely whale', encoding='utf-8')
    with pytest.raises(PermissionError):
        await runtime.call('read_file', {'path': str(path)})
    async def approve(*args):
        return True
    runtime.approve = approve
    assert await runtime.call('read_file', {'path': str(path)}) == 'a lovely whale'
    path.write_text('a' * 70000)
    with pytest.raises(ValueError):
        await runtime.call('read_file', {'path': str(path)})


async def test_activity_access_is_separate_permission(runtime):
    with pytest.raises(PermissionError):
        await runtime.call('get_activity', {})
    assert 'T' in await runtime.call('get_time', {})


async def test_cancel_pending_approval_does_not_execute(runtime):
    entered = asyncio.Event()
    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()
    runtime.approve = wait
    job = asyncio.create_task(runtime.call('create_reminder', {'text':'cancel', 'seconds':1}))
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert runtime.store.due_reminders(1e20) == []


@pytest.mark.parametrize('url', ['file:///C:/secret', 'http://example.com', 'https://user:pass@example.com', 'https://example.com:8080'])
async def test_bad_browser_url(runtime, url):
    with pytest.raises(ValueError):
        await validate_url(url)


async def test_private_address_rejected(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a: [(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):
        await validate_url('https://example.com')
