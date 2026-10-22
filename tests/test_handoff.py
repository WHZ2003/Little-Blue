import asyncio
from contextlib import closing
import json
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.exceptions import InvalidTag, InvalidSignature

from little_blue.handoff_guard import read_state, write_state, require_active
from little_blue.handoff_payload import (ensure_key, make_request, export_handoff, import_handoff,
    device_id, public_bytes, activate, decrypt, read_envelope, make_receipt, verify_receipt)
from little_blue.memory_transfer import digest
from little_blue.storage import Store
from little_blue.wechat import Account, save_account, read_account, WeChatChannel
from little_blue.service import create_app, CORE_KEY


@pytest.fixture
def transfer(tmp_path):
    source = tmp_path / 'source'
    store = Store(source / 'memory.sqlite3')
    store.remember('用户喜欢蓝莓茶', 'semantic')
    store.continuity.append('user', '这是切换之前的最后一句话', 'wechat')
    store.continuity.append('assistant', '已经保存了', 'wechat', delivery='delivered')
    store.put('settings', {'online': False, 'tools_enabled': True, 'perception': True})
    store.put('wechat:bot:cursor', 'last-poll-position')
    store.claim_message('wechat:bot', 'last-message')
    store.add_reminder('明天的提醒', 9999999999)
    store.close()
    save_account(source / 'wechat-account.json', Account(token='TEST-PRIVATE-TOKEN', owner='owner', bot='bot', enabled=False))
    from little_blue.bili_api import save_credential
    save_credential(source / 'bili-credential.json', {'sessdata': 'TEST-BILI-PRIVATE-TOKEN'})
    write_state(source, {'phase': 'draining'})
    source_key = ensure_key(tmp_path / 'source.pem')
    target_key = ensure_key(tmp_path / 'target.pem')
    request = make_request(target_key)
    package = tmp_path / 'transfer.lbhand'
    header = export_handoff(source, request, package, device_id(public_bytes(source_key)))
    return source, target_key, request, package, header


def test_roundtrip_preserves_history_credentials_cursors_and_reminders(transfer, tmp_path):
    source, key, request, package, header = transfer
    assert b'TEST-PRIVATE-TOKEN' not in package.read_bytes()
    assert b'TEST-BILI-PRIVATE-TOKEN' not in package.read_bytes()
    assert read_state(source)['phase'] == 'released'
    with pytest.raises(RuntimeError):
        require_active(source)
    target = tmp_path / 'target'
    import_handoff(package, target, key, request)
    from little_blue.bili_api import read_credential
    assert read_credential(target / 'bili-credential.json').sessdata == 'TEST-BILI-PRIVATE-TOKEN'
    with pytest.raises(RuntimeError):
        require_active(target)
    with closing(sqlite3.connect(source / 'memory.sqlite3')) as a, closing(sqlite3.connect(target / 'memory.sqlite3')) as b:
        for table in ('memories', 'conversation', 'channel_messages', 'reminders', 'growth_state', 'memory_meta'):
            assert a.execute(f'SELECT * FROM {table}').fetchall() == b.execute(f'SELECT * FROM {table}').fetchall()
        assert json.loads(b.execute("SELECT value FROM kv WHERE key='wechat:bot:cursor'").fetchone()[0]) == 'last-poll-position'
        settings = json.loads(b.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        assert not settings['online'] and not settings['tools_enabled'] and not settings['perception']
    assert read_account(target / 'wechat-account.json') == read_account(source / 'wechat-account.json')
    activate(target, header['transfer_id'])
    require_active(target)
    with pytest.raises(RuntimeError):
        require_active(source)


def test_wrong_device_or_request_cannot_receive(transfer, tmp_path):
    source, key, request, package, header = transfer
    wrong_key = ensure_key(tmp_path / 'wrong.pem')
    for actual_key, actual_request in ((wrong_key, request), (key, make_request(key))):
        with pytest.raises(ValueError):
            import_handoff(package, tmp_path / 'target', actual_key, actual_request)
    assert not (tmp_path / 'target').exists()


def test_tamper_is_rejected_before_destination_changes(transfer, tmp_path):
    source, key, request, package, header = transfer
    raw = bytearray(package.read_bytes())
    raw[-1] ^= 1
    package.write_bytes(raw)
    with pytest.raises(InvalidTag):
        import_handoff(package, tmp_path / 'target', key, request)
    assert not (tmp_path / 'target').exists()


def test_source_cannot_issue_to_second_recipient(transfer, tmp_path):
    source, key, request, package, header = transfer
    other = make_request(ensure_key(tmp_path / 'other.pem'))
    with pytest.raises(ValueError):
        export_handoff(source, other, tmp_path / 'fork.lbhand', header['source'])
    assert not (tmp_path / 'fork.lbhand').exists()


def test_retry_is_idempotent_and_no_existing_data_overwrite(transfer, tmp_path):
    source, key, request, package, header = transfer
    before = digest(package)
    assert export_handoff(source, request, package, header['source']) == header
    assert digest(package) == before
    target = tmp_path / 'target'
    import_handoff(package, target, key, request)
    before = digest(target / 'memory.sqlite3')
    import_handoff(package, target, key, request)
    assert before == digest(target / 'memory.sqlite3')
    existing = tmp_path / 'existing'
    store = Store(existing / 'memory.sqlite3')
    store.close()
    with pytest.raises(ValueError):
        import_handoff(package, existing, key, request)


def test_receipt_signed_by_receiver_does_not_unlock_source(transfer):
    source, key, request, package, header = transfer
    receipt = make_receipt(key, header, digest(package))
    verify_receipt(receipt, request, header, digest(package))
    assert read_state(source)['phase'] == 'released'
    receipt['data']['generation'] += 1
    with pytest.raises(InvalidSignature):
        verify_receipt(receipt, request, header, digest(package))


def test_return_handoff_increments_same_lineage(transfer, tmp_path):
    source, key, request, package, header = transfer
    target = tmp_path / 'target'
    import_handoff(package, target, key, request)
    activate(target, header['transfer_id'])
    state = read_state(target)
    write_state(target, {**state, 'phase': 'draining'})
    receiver = make_request(ensure_key(tmp_path / 'return.pem'))
    returned = export_handoff(target, receiver, tmp_path / 'return.lbhand', header['target'])
    assert returned['lineage'] == header['lineage']
    assert returned['generation'] == header['generation'] + 1


def test_publication_failure_leaves_source_locked_and_retry_recovers(tmp_path, monkeypatch):
    import little_blue.handoff_payload as module
    source = tmp_path / 'source'
    store = Store(source / 'memory.sqlite3')
    store.continuity.append('user', 'last committed message', 'desktop')
    store.close()
    write_state(source, {'phase': 'draining'})
    key = ensure_key(tmp_path / 'receiver.pem')
    request = make_request(key)
    package = tmp_path / 'retry.lbhand'
    link = module.os.link
    def fail(*args):
        raise OSError('simulated disk interruption')
    monkeypatch.setattr(module.os, 'link', fail)
    with pytest.raises(OSError):
        export_handoff(source, request, package, 'source-device')
    assert not package.exists()
    assert read_state(source)['phase'] == 'released'
    with pytest.raises(RuntimeError):
        require_active(source)
    transfer_id = read_state(source)['transfer_id']
    monkeypatch.setattr(module.os, 'link', link)
    header = export_handoff(source, request, package, 'source-device')
    assert header['transfer_id'] == transfer_id
    import_handoff(package, tmp_path / 'recovered', key, request)


def test_partial_import_is_fenced_not_reused(transfer, tmp_path, monkeypatch):
    import shutil
    source, key, request, package, header = transfer
    target = tmp_path / 'partial'
    def fail(*args):
        raise OSError('simulated disk interruption')
    monkeypatch.setattr(shutil, 'copy2', fail)
    with pytest.raises(OSError):
        import_handoff(package, target, key, request)
    assert read_state(target)['phase'] == 'importing'
    with pytest.raises(RuntimeError):
        require_active(target)
    with pytest.raises(ValueError):
        activate(target, header['transfer_id'])


async def test_api_auth_quiesce_and_new_task_rejection(tmp_path):
    token = 't' * 48
    app = create_app(Store(tmp_path / 'memory.sqlite3'), token)
    client = TestClient(TestServer(app), headers={'Authorization': 'Bearer ' + token})
    await client.start_server()
    try:
        assert (await client.post('/handoff', json={'action': 'prepare'}, headers={'Authorization': ''})).status == 401
        host = 'a' * 32
        assert (await client.post('/rpc', json={'op': 'sync', 'host': host, 'environment': {}})).status == 200
        response = await client.post('/handoff', json={'action': 'prepare'})
        assert response.status == 200
        assert (await response.json()) == {'requested': True, 'drained': True}
        assert read_state(tmp_path)['phase'] == 'draining'
        assert (await client.post('/rpc', json={'op': 'submit', 'host': host, 'text': 'new', 'task_id': 'b' * 32})).status == 409
        assert (await client.post('/bridge', json={})).status == 401
        assert (await client.post('/rpc', json={'op': 'configure', 'host': host, 'settings': {}})).status == 409
        assert (await client.post('/rpc', json={'op': 'sync', 'host': host, 'environment': {}})).status == 200
    finally:
        await client.close()


async def test_inflight_poll_is_saved_and_drained_before_handoff(tmp_path):
    from little_blue.core import Core
    core = Core(Store(tmp_path / 'memory.sqlite3'))
    class API:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0
        async def request(self, *args):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return {'get_updates_buf': 'cursor-last', 'msgs': [{
                'message_id': 'last', 'from_user_id': 'owner', 'message_type': 1,
                'message_state': 2, 'context_token': 'ctx',
                'item_list': [{'type': 1, 'text_item': {'text': '最后的消息'}}]}]}
    api = API()
    channel = core.wechat_channel = WeChatChannel(core, Account(token='test', owner='owner', bot='bot'), api)
    poll = asyncio.create_task(channel.poll())
    try:
        await api.entered.wait()
        core.handoff_requested = True
        assert not core.handoff_status()['drained']
        api.release.set()
        async with asyncio.timeout(3):
            while not channel.poll_paused:
                await asyncio.sleep(.01)
        assert api.calls == 1
        assert channel.queue.qsize() == 1
        assert not core.handoff_status()['drained']
        assert core.store.get(channel.key + ':cursor') == 'cursor-last'
        assert core.store.db.execute('SELECT text FROM conversation').fetchone()[0] == '最后的消息'
        channel.queue.get_nowait()
        assert core.handoff_status()['drained']
    finally:
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        core.store.close()
